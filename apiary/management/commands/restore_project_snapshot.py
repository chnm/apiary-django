from django.core.management.base import BaseCommand, CommandError

from apiary import projectdata
from apiary.models import DataSnapshot


class Command(BaseCommand):
    help = (
        "Replace a project's data with a snapshot's, in one transaction. "
        "Snapshots the current data first, so the restore can be undone."
    )

    def add_arguments(self, parser):
        parser.add_argument("--snapshot", required=True, type=int, help="The snapshot's number (#).")
        parser.add_argument(
            "--confirm", required=True,
            help="The snapshot's project name; guards against restoring the wrong project.",
        )

    def handle(self, *args, snapshot, confirm, **options):
        try:
            target = DataSnapshot.objects.get(pk=snapshot)
        except DataSnapshot.DoesNotExist:
            raise CommandError(f"No snapshot #{snapshot}.")
        if confirm != target.project:
            raise CommandError(f'Snapshot #{snapshot} is of {target.project}: type "{target.project}" to confirm.')
        try:
            before = projectdata.restore_snapshot(target)
        except projectdata.SnapshotError as error:
            raise CommandError(str(error))
        after = projectdata.row_counts(target.project)
        self.stdout.write(f"Restored {target}")
        for table, count in before.row_counts.items():
            self.stdout.write(f"  {table}: {count} -> {after[table]} rows")
        self.stdout.write(f"Undo with snapshot #{before.pk}.")
