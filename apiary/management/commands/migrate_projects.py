from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = "Create PostgreSQL project schemas and migrate all project databases."

    def handle(self, *args, **options):
        # Restore targets may already have a data directory, so initdb is not enough.
        with connections["default"].cursor() as cursor:
            for schema in ("bom", "connthreads", "mappingviolence", "relec"):
                cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        call_command("migrate", database="default", interactive=False)
        for app in ("bom", "connthreads", "mappingviolence", "relec"):
            call_command("migrate", app, database=f"{app}_db", interactive=False)
