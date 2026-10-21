from pathlib import Path

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connections, router, transaction

from apiary.views import PROJECT_APPS


class Command(BaseCommand):
    help = (
        "Empty one project's tables and reload its fixtures, in one transaction "
        "on the project's database: if the fixtures fail to load, nothing changes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--project", required=True, choices=PROJECT_APPS)
        parser.add_argument(
            "--confirm", required=True,
            help="The project name again; guards against wiping the wrong project.",
        )
        parser.add_argument("--no-fixtures", action="store_true", help="Only empty the tables.")

    def handle(self, *args, project, confirm, no_fixtures, **options):
        if confirm != project:
            raise CommandError(f'Type the project name "{project}" to confirm.')

        config = apps.get_app_config(project)
        models = list(config.get_models(include_auto_created=True))
        alias = router.db_for_write(models[0])
        connection = connections[alias]
        fixtures = sorted(str(path) for path in (Path(config.path) / "fixtures").glob("*.json"))

        with transaction.atomic(using=alias):
            before = {model._meta.db_table: model._default_manager.using(alias).count() for model in models}
            # The statements Django's flush uses (TRUNCATE ... RESTART IDENTITY
            # on PostgreSQL), limited to this project's tables. No CASCADE: they
            # are truncated together, and nothing outside the project may go.
            connection.ops.execute_sql_flush(connection.ops.sql_flush(
                no_style(), list(before), reset_sequences=True, allow_cascade=False,
            ))
            if fixtures and not no_fixtures:
                call_command("loaddata", *fixtures, database=alias, verbosity=0)
            after = {model._meta.db_table: model._default_manager.using(alias).count() for model in models}

        self.stdout.write(f"{config.verbose_name} ({alias}, {connection.display_name})")
        for table in before:
            self.stdout.write(f"  {table}: {before[table]} -> {after[table]} rows")
        if fixtures and not no_fixtures:
            self.stdout.write(f"Loaded {', '.join(Path(path).name for path in fixtures)}.")
        else:
            self.stdout.write("No fixtures loaded.")
