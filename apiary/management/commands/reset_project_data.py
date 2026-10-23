from pathlib import Path

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apiary import projectdata
from apiary.projects import PROJECT_APPS


class Command(BaseCommand):
    help = (
        "Empty one project's tables and reload its fixtures, in one transaction "
        "on the project's database: if the fixtures fail to load, nothing changes. "
        "Snapshots the data first, so restore_project_snapshot can undo it."
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
        alias = projectdata.project_alias(project)
        fixtures = sorted(str(path) for path in (Path(config.path) / "fixtures").glob("*.json"))

        before = projectdata.take_snapshot(project, kind="pre-reset", label="Before reset_project_data")
        with transaction.atomic(using=alias):
            projectdata.flush(project)
            if fixtures and not no_fixtures:
                call_command("loaddata", *fixtures, database=alias, verbosity=0)
        after = projectdata.row_counts(project)

        self.stdout.write(f"{config.verbose_name} ({alias})")
        for table, count in before.row_counts.items():
            self.stdout.write(f"  {table}: {count} -> {after[table]} rows")
        if fixtures and not no_fixtures:
            self.stdout.write(f"Loaded {', '.join(Path(path).name for path in fixtures)}.")
        else:
            self.stdout.write("No fixtures loaded.")
        self.stdout.write(f"Undo with snapshot #{before.pk} (restore_project_snapshot).")
