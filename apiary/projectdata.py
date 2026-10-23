"""
Snapshot and restore one project's data, on SQLite or PostgreSQL alike.

A snapshot is the project's rows as a gzipped Django fixture (typed, keeps
NULL vs empty) plus a manifest, stored in private storage under
snapshots/<project>/<id>/ and recorded as a DataSnapshot. It is read from one
consistent moment and records both halves of the schema: the applied
migrations of the project's models in code, and the definitions of its live
tables (apiary.live). A restore refuses a snapshot from other migrations,
and rebuilds the live tables as the snapshot defines them, so restoring
undoes schema changes too.

Restoring first snapshots the current data, so every restore can be undone.
"""
import functools
import gzip
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from contextlib import contextmanager

from django.core import serializers
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management.color import no_style
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, transaction
from django.db.migrations.recorder import MigrationRecorder

from apiary import projects
from apiary.live import runtime
from apiary.live import schema as live_schema
from apiary.projects import PROJECT_APPS, UnknownProject


class SnapshotError(Exception):
    pass


def project_alias(project):
    try:
        return projects.project_alias(project)
    except UnknownProject as error:
        raise SnapshotError(str(error))


def project_models(project):
    """Every table of the project as a model: code models, then live tables."""
    project_alias(project)
    return list(projects.project_models(project).values())


def row_counts(project):
    alias = project_alias(project)
    return {name: model._default_manager.using(alias).count() for name, model in projects.project_models(project).items()}


def applied_migrations(project):
    recorder = MigrationRecorder(connections[project_alias(project)])
    return sorted(name for app, name in recorder.applied_migrations() if app == project)


def flush_tables(project, models):
    connection = connections[project_alias(project)]
    tables = [m._meta.db_table for m in models]
    if tables:
        connection.ops.execute_sql_flush(connection.ops.sql_flush(
            no_style(), tables, reset_sequences=True, allow_cascade=False,
        ))


def flush(project):
    """
    Empty the project's tables, code and live, with the statements Django's
    flush uses (TRUNCATE ... RESTART IDENTITY on PostgreSQL). No CASCADE: the
    tables are truncated together, and nothing outside the project may go.
    Call it inside a transaction on the project's database.
    """
    flush_tables(project, project_models(project))


def flush_code_tables(project):
    flush_tables(project, list(projects.code_models(project).values()))


@contextmanager
def consistent_read(project):
    """
    One read-only snapshot of every table: PostgreSQL otherwise lets each
    SELECT see writes committed since the previous one. SQLite's read
    transaction already holds one.
    """
    alias = project_alias(project)
    connection = connections[alias]
    with transaction.atomic(using=alias):
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield alias


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_exactly(name, content):
    """Save to exactly this name: storages rename instead of overwriting."""
    saved = storages["default"].save(name, content)
    if saved != name:
        storages["default"].delete(saved)
        raise SnapshotError(f"Storage already holds {name}; not overwriting it.")


def write_records(project, alias, path):
    """Every row of the project as one gzipped Django fixture, table by table."""
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("[")
        first = True
        for model in project_models(project):
            queryset = model._default_manager.using(alias).order_by("pk")
            for record in serializers.serialize("python", queryset.iterator(chunk_size=2000)):
                handle.write(("" if first else ",\n") + json.dumps(record, cls=DjangoJSONEncoder))
                first = False
        handle.write("]\n")


def take_snapshot(project, *, kind, user=None, label=""):
    """Snapshot the project and return its DataSnapshot."""
    from apiary.models import DataSnapshot

    workdir = tempfile.mkdtemp()
    try:
        data_path = os.path.join(workdir, "data.json.gz")
        with consistent_read(project) as alias:
            counts = row_counts(project)
            migrations = applied_migrations(project)
            specs = runtime.read_specs(alias)
            write_records(project, alias, data_path)

        snapshot = DataSnapshot.objects.create(
            project=project, kind=kind, label=label, created_by=user,
            row_counts=counts, migrations=migrations, schema=specs,
            sha256=sha256(data_path), size=os.path.getsize(data_path),
        )
        with open(data_path, "rb") as handle:
            save_exactly(snapshot.data_path, File(handle))
        save_exactly(snapshot.manifest_path, ContentFile(json.dumps(snapshot.manifest(), indent=2).encode()))
        return snapshot
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def read_records(data_path):
    try:
        with gzip.open(data_path) as handle:
            records = json.load(handle)
    except (OSError, ValueError) as error:
        raise SnapshotError(f"The snapshot data is not readable gzipped JSON: {error}")
    if not isinstance(records, list):
        raise SnapshotError("The snapshot data is not a list of records.")
    return records


def check_schema(schema):
    """A snapshot's live table definitions must build: they become tables on restore."""
    if not isinstance(schema, list):
        raise SnapshotError("The snapshot's schema is not a list of tables.")
    try:
        for table in schema:
            runtime.check_name("Table", table["name"])
            for column in table["columns"]:
                runtime.check_name("Column", column["name"])
                runtime.make_field(runtime.column_spec(column))
    except (KeyError, TypeError, runtime.SchemaError) as error:
        raise SnapshotError(f"The snapshot's schema is not valid: {error}")


def check_records(project, data_path, schema):
    """
    The data must be readable and hold only this project's records: restoring
    loads it, so an imported bundle must not be able to write users or
    another project's tables.
    """
    check_schema(schema)
    records = read_records(data_path)
    allowed = {m._meta.label_lower for m in projects.code_models(project).values()}
    allowed |= {f"{project}.{table['name']}" for table in schema}
    strays = sorted({str(r.get("model")) for r in records if not isinstance(r, dict) or r.get("model") not in allowed})
    if strays:
        raise SnapshotError(f"The snapshot holds records outside {project}: {', '.join(strays[:5])}.")
    return records


def load_records(project, alias, records, live):
    """Insert the snapshot's rows: code models by Django's deserializer, live tables directly."""
    by_label = {f"{project}.{name}": model for name, model in live.items()}
    code = [record for record in records if record["model"] not in by_label]
    for obj in serializers.deserialize("python", code, using=alias):
        obj.save(using=alias)
    rows = {}
    for record in records:
        model = by_label.get(record["model"])
        if model is None:
            continue
        values = {"id": record["pk"]}
        for name, value in record["fields"].items():
            field = model._meta.get_field(name)
            values[field.attname] = field.to_python(value) if not field.is_relation else value
        rows.setdefault(model, []).append(model(**values))
    for model, objects in rows.items():
        model._default_manager.using(alias).bulk_create(objects, batch_size=1000)
    connection = connections[alias]
    statements = connection.ops.sequence_reset_sql(no_style(), project_models(project))
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def restore_snapshot(snapshot, *, user=None):
    """
    Replace the project's rows with the snapshot's, in one transaction, after
    snapshotting the current rows. Returns that pre-restore snapshot.
    """
    project = snapshot.project
    current = applied_migrations(project)
    if current != snapshot.migrations:
        missing = sorted(set(snapshot.migrations) - set(current))
        extra = sorted(set(current) - set(snapshot.migrations))
        raise SnapshotError(
            f"Snapshot #{snapshot.pk} was taken against another schema of {project}. "
            f"Applied now but not then: {extra or 'none'}; then but not now: {missing or 'none'}."
        )

    workdir = tempfile.mkdtemp()
    try:
        # loaddata picks the format from the extension.
        data_path = os.path.join(workdir, f"snapshot-{snapshot.pk}.json.gz")
        with storages["default"].open(snapshot.data_path, "rb") as source, open(data_path, "wb") as target:
            shutil.copyfileobj(source, target)
        if sha256(data_path) != snapshot.sha256:
            raise SnapshotError(f"Snapshot #{snapshot.pk} does not match its checksum; not restoring it.")
        records = check_records(project, data_path, snapshot.schema)  # before anything is deleted

        before = take_snapshot(project, kind="pre-restore", user=user, label=f"Before restoring snapshot #{snapshot.pk}")
        alias = project_alias(project)
        # One transaction for the DDL and the rows. On SQLite the schema
        # editor also turns foreign key checks off until it commits, then
        # checks every row.
        with connections[alias].schema_editor(atomic=True) as editor:
            flush_code_tables(project)
            live = live_schema.replace(editor, project, snapshot.schema)
            load_records(project, alias, records, live)
        return before
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


AUTOMATIC = ("pre-restore", "pre-reset", "pre-change", "site")  # never "manual" or "imported"


def delete_snapshot(snapshot):
    """Delete the snapshot's files, then its row."""
    storage = storages["default"]
    for name in (snapshot.data_path, snapshot.manifest_path):
        if storage.exists(name):
            storage.delete(name)
    snapshot.delete()


def prunable(*, keep, older_than):
    """
    Automatic snapshots past retention: beyond the newest `keep` of each
    project and older than `older_than` (a datetime). Manual snapshots are
    never pruned, nor ones a site version was built from.
    """
    from apiary.models import DataSnapshot, SiteVersion

    in_use = set(SiteVersion.objects.exclude(data_snapshot=None).values_list("data_snapshot", flat=True))
    candidates = []
    for project in PROJECT_APPS:
        automatic = DataSnapshot.objects.filter(project=project, kind__in=AUTOMATIC).order_by("-created_at")
        candidates += [s for s in automatic[keep:] if s.created_at < older_than and s.pk not in in_use]
    return candidates


# Bundles: a snapshot as one portable zip (manifest.json + data.json.gz), to
# move it between environments. Importing makes a new snapshot that restores
# like any other, schema check included.

MAX_BUNDLE_BYTES = 500 * 1024 * 1024


def export_bundle(snapshot, destination):
    """Write the snapshot's bundle to the binary file object destination."""
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_STORED) as bundle:
        bundle.writestr("manifest.json", json.dumps(snapshot.manifest(), indent=2))
        with storages["default"].open(snapshot.data_path, "rb") as source, bundle.open("data.json.gz", "w") as target:
            shutil.copyfileobj(source, target)


def import_bundle(source, *, user=None):
    """Check a bundle (a file object) and store it as a new imported snapshot."""
    from apiary.models import DataSnapshot

    try:
        bundle = zipfile.ZipFile(source)
        manifest = json.loads(bundle.read("manifest.json"))
        info = bundle.getinfo("data.json.gz")
    except (zipfile.BadZipFile, KeyError, ValueError) as error:
        raise SnapshotError(f"Not a snapshot bundle (manifest.json and data.json.gz in a zip): {error}")
    if manifest.get("format") not in (1, 2):
        raise SnapshotError(f"Unsupported bundle format {manifest.get('format')!r}.")
    project = manifest.get("project")
    project_models(project)  # raises for an unknown project
    if info.file_size > MAX_BUNDLE_BYTES:
        raise SnapshotError(f"The data is {info.file_size} bytes; the limit is {MAX_BUNDLE_BYTES}.")

    workdir = tempfile.mkdtemp()
    try:
        data_path = os.path.join(workdir, "data.json.gz")
        with bundle.open(info) as data, open(data_path, "wb") as target:
            shutil.copyfileobj(data, target)
        if sha256(data_path) != manifest.get("data", {}).get("sha256"):
            raise SnapshotError("The data does not match the manifest's checksum.")
        schema_specs = manifest.get("schema", [])
        check_records(project, data_path, schema_specs)

        source_note = f"#{manifest.get('id')} of {manifest.get('created_at', '?')}"
        snapshot = DataSnapshot.objects.create(
            project=project, kind="imported", created_by=user,
            label=(f"Imported {source_note}: {manifest.get('label') or manifest.get('kind', '')}")[:200],
            row_counts=manifest.get("row_counts", {}), migrations=manifest.get("migrations", []), schema=schema_specs,
            sha256=sha256(data_path), size=os.path.getsize(data_path),
        )
        with open(data_path, "rb") as handle:
            save_exactly(snapshot.data_path, File(handle))
        save_exactly(snapshot.manifest_path, ContentFile(json.dumps(snapshot.manifest(), indent=2).encode()))
        return snapshot
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# Exploring: a snapshot's records as one table per model, for the admin's
# browser and SQL console. It reads only the snapshot, never the live schema,
# so snapshots from older schemas open too. Snapshots never change, so the
# parsed tables are cached by key.

@functools.lru_cache(maxsize=4)
def _tables(key, data_path):
    with storages["default"].open(data_path, "rb") as handle:
        records = json.loads(gzip.decompress(handle.read()))
    tables = {}
    for record in records:
        table = record["model"].split(".", 1)[1]
        tables.setdefault(table, []).append({"id": record.get("pk"), **record.get("fields", {})})
    return {name: sorted(rows, key=lambda row: (row["id"] is None, row["id"])) for name, rows in sorted(tables.items())}


def snapshot_tables(snapshot):
    """{table: [row, ...]}, each row {"id": pk, **fields}, ordered by id. Shared: do not modify."""
    return _tables(str(snapshot.key), snapshot.data_path)

