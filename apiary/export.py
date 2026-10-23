"""
Export one project as a ZIP of CSV files, one per table, described by a
Frictionless Data Package (datapackage.json): https://datapackage.org/

It reads through Django, so SQLite and PostgreSQL projects export alike.
"""
import csv
import io
import json
import zipfile

from django.apps import apps
from django.db import connections, models, transaction
from django.utils import timezone

from apiary.projects import project_alias, project_models

FIELD_TYPES = [
    ((models.BooleanField,), "boolean"),
    ((models.DateTimeField,), "datetime"),
    ((models.DateField,), "date"),
    ((models.TimeField,), "time"),
    ((models.IntegerField, models.AutoField, models.BigAutoField), "integer"),
    ((models.FloatField, models.DecimalField), "number"),
    ((models.JSONField,), "object"),
]


def field_type(field):
    if field.is_relation:
        return field_type(field.target_field)
    for classes, name in FIELD_TYPES:
        if isinstance(field, classes):
            return name
    return "string"


def cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def resource(model):
    fields = model._meta.concrete_fields
    foreign_keys = [
        {"fields": field.attname,
         "reference": {"resource": field.related_model._meta.model_name, "fields": field.target_field.attname}}
        for field in fields if field.is_relation
    ]
    schema = {
        "fields": [{"name": field.attname, "type": field_type(field)} for field in fields],
        "primaryKey": model._meta.pk.attname,
    }
    if foreign_keys:
        schema["foreignKeys"] = foreign_keys
    return {
        "profile": "tabular-data-resource",
        "name": model._meta.model_name,
        "title": str(model._meta.verbose_name_plural).capitalize(),
        "path": f"{model._meta.model_name}.csv",
        "format": "csv",
        "mediatype": "text/csv",
        "encoding": "utf-8",
        "schema": schema,
    }


def write_project_zip(project, destination):
    """Write the project's export to the binary file object destination."""
    config = apps.get_app_config(project)
    alias = project_alias(project)
    table_models = list(project_models(project).values())  # in code and live
    connection = connections[alias]

    # One snapshot of every table: PostgreSQL otherwise lets each SELECT see
    # writes committed since the previous one.
    with transaction.atomic(using=alias), zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        resources = []
        for model in table_models:
            entry = resource(model)
            names = [field["name"] for field in entry["schema"]["fields"]]
            with archive.open(entry["path"], "w") as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                writer = csv.writer(text)
                writer.writerow(names)
                rows = model._default_manager.using(alias).order_by("pk").values_list(*names)
                for row in rows.iterator(chunk_size=2000):
                    writer.writerow([cell(value) for value in row])
                text.flush()
                text.detach()
            resources.append(entry)
        archive.writestr("datapackage.json", json.dumps({
            "profile": "tabular-data-package",
            "name": f"apiary-{project}",
            "title": str(config.verbose_name),
            "created": timezone.now().isoformat(timespec="seconds"),
            "resources": resources,
        }, indent=2))
