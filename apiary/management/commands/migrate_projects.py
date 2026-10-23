from pathlib import Path

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connections

from apiary.projects import PROJECT_DATABASES, code_models

# The projects kept in PostgreSQL schemas (settings_db); bom and pinkertons are SQLite files.
POSTGRES_SCHEMAS = ("demo", "connthreads", "mappingviolence", "relec")


class Command(BaseCommand):
    help = (
        "Create PostgreSQL project schemas, migrate all project databases (each project's own app "
        "and its live table definitions), then make sure every project has its Project row and "
        "role groups, and the demo project its mock data."
    )

    def handle(self, *args, **options):
        # Restore targets may already have a data directory, so initdb is not enough.
        with connections["default"].cursor() as cursor:
            for schema in POSTGRES_SCHEMAS:
                cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        call_command("migrate", database="default", interactive=False)
        for app, alias in PROJECT_DATABASES.items():
            call_command("migrate", app, database=alias, interactive=False)
            call_command("migrate", "live", database=alias, interactive=False)
        call_command("sync_projects")
        call_command("init_project_groups")
        self.seed_demo()

    def seed_demo(self):
        """The root page shows demo's mock data: load it once, into an empty project."""
        alias = PROJECT_DATABASES["demo"]
        if any(model.objects.using(alias).exists() for model in code_models("demo").values()):
            return
        fixtures = sorted(str(path) for path in (Path(apps.get_app_config("demo").path) / "fixtures").glob("*.json"))
        call_command("loaddata", *fixtures, database=alias, verbosity=0)
        self.stdout.write(f"Loaded demo fixtures: {', '.join(Path(path).name for path in fixtures)}")
