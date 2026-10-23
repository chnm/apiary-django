"""
Live tables as Django models, built at runtime from their definitions.

A definition ("spec") is plain data, the same in the database, in schema
changes and in snapshots:

    {"name": "letters", "label": "", "description": "",
     "columns": [{"name": "sent", "type": "date", "required": False, ...}]}

Each build gets its own model registry, so the models never join Django's
global one: migrations, the admin and dumpdata never see them. Each process
caches one build per project and rebuilds when its SchemaState changes.
"""
import keyword
import re
import threading
import uuid

from django.apps.registry import Apps
from django.db import models
from django.db.migrations.state import AppConfigStub

from apiary.projects import check_project, project_alias

NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
RESERVED = {"id", "pk", "objects", "meta", "check", "save", "delete", "clean", "full_clean"}
RESERVED_PREFIXES = ("meta_", "django_", "sqlite_", "pg_", "_")
TEXT_MAX_LENGTH = 255
TEXT_LIMIT = 2000
COLUMN_KEYS = ("name", "type", "label", "description", "required", "unique", "max_length", "target", "default")

_cache = {}
_lock = threading.Lock()


class SchemaError(ValueError):
    pass


def check_name(kind, name):
    if not isinstance(name, str) or not NAME.match(name):
        raise SchemaError(f"{kind} name {name!r}: use 1-40 lowercase letters, digits and underscores, starting with a letter.")
    if name in RESERVED or keyword.iskeyword(name) or name.startswith(RESERVED_PREFIXES):
        raise SchemaError(f"{kind} name {name!r} is reserved.")
    return name


def db_table(name):
    return f"live_{name}"


def column_spec(column):
    """A column's spec with every key, defaults filled in."""
    spec = {
        "name": column.get("name"),
        "type": column.get("type"),
        "label": column.get("label") or "",
        "description": column.get("description") or "",
        "required": bool(column.get("required", False)),
        "unique": bool(column.get("unique", False)),
        "max_length": column.get("max_length"),
        "target": column.get("target") or "",
        "default": column.get("default"),
    }
    if spec["type"] == "text" and spec["max_length"] is None:
        spec["max_length"] = TEXT_MAX_LENGTH
    return spec


def make_field(spec):
    """The Django field for a column spec. Relations are resolved within the build's registry."""
    common = {
        "db_column": spec["name"],
        "null": not spec["required"],
        "blank": not spec["required"],
        "unique": spec["unique"],
        "verbose_name": spec["label"] or spec["name"].replace("_", " "),
        "help_text": spec["description"],
    }
    if spec["default"] is not None:
        common["default"] = spec["default"]
    kind = spec["type"]
    if kind == "text":
        return models.CharField(max_length=spec["max_length"], **common)
    if kind == "longtext":
        return models.TextField(**common)
    if kind == "integer":
        return models.BigIntegerField(**common)
    if kind == "float":
        return models.FloatField(**common)
    if kind == "boolean":
        return models.BooleanField(**common)
    if kind == "date":
        return models.DateField(**common)
    if kind == "datetime":
        return models.DateTimeField(**common)
    if kind == "time":
        return models.TimeField(**common)
    if kind == "json":
        return models.JSONField(**common)
    if kind == "url":
        return models.URLField(max_length=TEXT_LIMIT, **common)
    if kind == "email":
        return models.EmailField(**common)
    if kind == "relation":
        common.pop("default", None)
        common["db_column"] = f"{spec['name']}_id"
        # Required links block deleting what they point at; optional ones clear.
        on_delete = models.PROTECT if spec["required"] else models.SET_NULL
        return models.ForeignKey(spec["target"], on_delete=on_delete, related_name="+", **common)
    raise SchemaError(f"Unknown column type {kind!r}.")


def build_models(project, specs):
    """{name: model} for these table specs, in a registry of their own."""
    registry = Apps(installed_apps=())
    # As Django's migration state does: an app config stub lets the registry
    # list these models, so each knows the links pointing at it and deletes
    # follow on_delete (PROTECT, SET_NULL) instead of failing in the database.
    stub = AppConfigStub(project)
    stub.apps, stub.models = registry, registry.all_models[project]
    registry.app_configs[project] = stub
    built = {}
    for table in specs:
        attrs = {
            "__module__": "apiary.live.tables",
            "id": models.BigAutoField(primary_key=True),
            "Meta": type("Meta", (), {
                "app_label": project,
                "apps": registry,
                "db_table": db_table(table["name"]),
                "managed": False,
                "verbose_name": table.get("label") or table["name"].replace("_", " "),
                "verbose_name_plural": table.get("label") or table["name"].replace("_", " "),
            }),
            "__str__": _describe,
        }
        for column in table["columns"]:
            spec = column_spec(column)
            if spec["type"] == "relation":
                spec["target"] = f"{project}.{spec['target']}"
            attrs[spec["name"]] = make_field(spec)
        built[table["name"]] = type(table["name"], (models.Model,), attrs)
    registry.clear_cache()
    return built


def _describe(record):
    for field in record._meta.concrete_fields:
        if isinstance(field, models.CharField) and getattr(record, field.attname):
            return str(getattr(record, field.attname))
    return f"{record._meta.model_name} #{record.pk}"


def read_specs(alias):
    """The live table specs stored in this database."""
    from apiary.live.models import LiveColumn, LiveTable

    columns = {}
    for column in LiveColumn.objects.using(alias).order_by("position", "id"):
        columns.setdefault(column.table_id, []).append(column_spec({key: getattr(column, key) for key in COLUMN_KEYS}))
    return [
        {"name": table.name, "label": table.label, "description": table.description, "columns": columns.get(table.pk, [])}
        for table in LiveTable.objects.using(alias).order_by("name")
    ]


def schema_state(alias):
    """(version, token) of this database's live schema; (0, "") before any change."""
    from apiary.live.models import SchemaState

    return SchemaState.objects.using(alias).filter(pk=1).values_list("version", "token").first() or (0, "")


def bump(alias):
    """Record a schema change in this database: every process rebuilds its models."""
    from apiary.live.models import SchemaState

    state, _ = SchemaState.objects.using(alias).get_or_create(pk=1)
    state.version += 1
    state.token = uuid.uuid4().hex
    state.save(using=alias)
    return state.version


def live_specs(project):
    return read_specs(project_alias(project))


def live_models(project):
    """The project's live tables as models, by name, rebuilt when its schema changes."""
    alias = project_alias(check_project(project))
    state = schema_state(alias)
    cached = _cache.get(project)
    if cached and cached[0] == state:
        return cached[1]
    with _lock:
        built = build_models(project, read_specs(alias))
        _cache[project] = (state, built)
    return built


def forget(project):
    _cache.pop(project, None)
