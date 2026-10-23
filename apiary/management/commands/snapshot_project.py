from django.core.management.base import BaseCommand

from apiary import projectdata
from apiary.projects import PROJECT_APPS


class Command(BaseCommand):
    help = "Snapshot one project's data to object storage (apiary.projectdata)."

    def add_arguments(self, parser):
        parser.add_argument("--project", required=True, choices=PROJECT_APPS)
        parser.add_argument("--label", default="")

    def handle(self, *args, project, label, **options):
        snapshot = projectdata.take_snapshot(project, kind="manual", label=label)
        self.stdout.write(f"Snapshot #{snapshot.pk} of {project}: {snapshot.size} bytes, sha256 {snapshot.sha256}")
        for table, count in snapshot.row_counts.items():
            self.stdout.write(f"  {table}: {count} rows")
