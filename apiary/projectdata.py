"""
Snapshot and restore one project's data, on SQLite or PostgreSQL alike.

A snapshot is the project's rows as a gzipped Django fixture (typed, keeps
NULL vs empty) plus a manifest, stored in private storage under
snapshots/<project>/<id>/ and recorded as a DataSnapshot. It is read from one
consistent moment, and records the project's applied migrations: a restore
refuses a snapshot taken against another schema rather than loading rows
into tables that no longer match them.

Restoring first snapshots the current data, so every restore can be undone.
"""
import gzip
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from contextlib import contextmanager

from django.apps import apps
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import connections, router, transaction
from django.db.migrations.recorder import MigrationRecorder

from apiary.views import PROJECT_APPS


class SnapshotError(Exception):
    pass


def project_models(project):
    if project not in PROJECT_APPS:
        raise SnapshotError(f"Unknown project {project!r}; one of {', '.join(PROJECT_APPS)}.")
    return list(apps.get_app_config(project).get_models(include_auto_created=True))


def project_alias(project):
    return router.db_for_write(project_models(project)[0])


def row_counts(project):
    alias = project_alias(project)
    return {m._meta.db_table: m._default_manager.using(alias).count() for m in project_models(project)}


def applied_migrations(project):
    recorder = MigrationRecorder(connections[project_alias(project)])
    return sorted(name for app, name in recorder.applied_migrations() if app == project)


def flush(project):
    """
    Empty the project's tables with the statements Django's flush uses
    (TRUNCATE ... RESTART IDENTITY on PostgreSQL). No CASCADE: the tables are
    truncated together, and nothing outside the project may go. Call it
    inside a transaction on the project's database.
    """
    connection = connections[project_alias(project)]
    tables = [m._meta.db_table for m in project_models(project)]
    connection.ops.execute_sql_flush(connection.ops.sql_flush(
        no_style(), tables, reset_sequences=True, allow_cascade=False,
    ))


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


def take_snapshot(project, *, kind, user=None, label="", during=None):
    """
    Snapshot the project and return its DataSnapshot. during(alias), if
    given, runs inside the same consistent read, so whatever it reads (e.g.
    a site's datasets) matches the snapshot exactly; its result is returned
    as the second value.
    """
    from apiary.models import DataSnapshot

    workdir = tempfile.mkdtemp()
    try:
        data_path = os.path.join(workdir, "data.json.gz")
        with consistent_read(project) as alias:
            counts = row_counts(project)
            migrations = applied_migrations(project)
            call_command("dumpdata", project, database=alias, output=data_path, verbosity=0)
            extra = during(alias) if during else None

        snapshot = DataSnapshot.objects.create(
            project=project, kind=kind, label=label, created_by=user,
            row_counts=counts, migrations=migrations,
            sha256=sha256(data_path), size=os.path.getsize(data_path),
        )
        with open(data_path, "rb") as handle:
            save_exactly(snapshot.data_path, File(handle))
        save_exactly(snapshot.manifest_path, ContentFile(json.dumps(snapshot.manifest(), indent=2).encode()))
        return snapshot, extra
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_records(project, data_path):
    """
    The data must be readable and hold only this project's records: restoring
    loads it, so an imported bundle must not be able to write users or
    another project's tables.
    """
    try:
        with gzip.open(data_path) as handle:
            records = json.load(handle)
    except (OSError, ValueError) as error:
        raise SnapshotError(f"The snapshot data is not readable gzipped JSON: {error}")
    if not isinstance(records, list):
        raise SnapshotError("The snapshot data is not a list of records.")
    allowed = {m._meta.label_lower for m in project_models(project)}
    strays = sorted({str(r.get("model")) for r in records if not isinstance(r, dict) or r.get("model") not in allowed})
    if strays:
        raise SnapshotError(f"The snapshot holds records outside {project}: {', '.join(strays[:5])}.")
    return len(records)


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
        check_records(project, data_path)  # before anything is deleted

        before, _ = take_snapshot(project, kind="pre-restore", user=user,
                                  label=f"Before restoring snapshot #{snapshot.pk}")
        alias = project_alias(project)
        with transaction.atomic(using=alias):
            flush(project)
            call_command("loaddata", data_path, database=alias, verbosity=0)
        return before
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


AUTOMATIC = ("pre-restore", "pre-reset", "site")  # never "manual" or "imported"


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
    if manifest.get("format") != 1:
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
        check_records(project, data_path)

        source_note = f"#{manifest.get('id')} of {manifest.get('created_at', '?')}"
        snapshot = DataSnapshot.objects.create(
            project=project, kind="imported", created_by=user,
            label=(f"Imported {source_note}: {manifest.get('label') or manifest.get('kind', '')}")[:200],
            row_counts=manifest.get("row_counts", {}), migrations=manifest.get("migrations", []),
            sha256=sha256(data_path), size=os.path.getsize(data_path),
        )
        with open(data_path, "rb") as handle:
            save_exactly(snapshot.data_path, File(handle))
        save_exactly(snapshot.manifest_path, ContentFile(json.dumps(snapshot.manifest(), indent=2).encode()))
        return snapshot
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

