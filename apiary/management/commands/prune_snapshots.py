from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apiary import projectdata


class Command(BaseCommand):
    help = (
        "Delete automatic data snapshots (before resets and restores, unused page "
        "snapshots) past retention. Manual snapshots and those a site version was "
        "built from are kept."
    )

    def add_arguments(self, parser):
        parser.add_argument("--keep", type=int, default=10, help="Newest automatic snapshots kept per project.")
        parser.add_argument("--days", type=int, default=30, help="Automatic snapshots newer than this are kept.")
        parser.add_argument("--mode", choices=["preview", "delete"], default="preview")

    def handle(self, *args, keep, days, mode, **options):
        snapshots = projectdata.prunable(keep=keep, older_than=timezone.now() - timedelta(days=days))
        verb = "Deleting" if mode == "delete" else "Would delete"
        self.stdout.write(f"{verb} {len(snapshots)} snapshot(s) (keeping the newest {keep} per project "
                          f"and anything from the last {days} days).")
        for snapshot in snapshots:
            self.stdout.write(f"  {snapshot}")
            if mode == "delete":
                projectdata.delete_snapshot(snapshot)
