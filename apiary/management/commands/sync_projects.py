from django.apps import apps
from django.core.management.base import BaseCommand

from apiary.models import Project
from apiary.projects import PROJECT_APPS


class Command(BaseCommand):
    help = (
        "Give every project app a Project row, named after the app. Idempotent; never changes "
        "an existing row, so names and descriptions edited in the admin are kept."
    )

    def handle(self, *args, **options):
        for label in PROJECT_APPS:
            name = str(apps.get_app_config(label).verbose_name)
            _, created = Project.objects.get_or_create(app_label=label, defaults={"name": name, "description": ""})
            self.stdout.write(f"{'created' if created else 'kept'} {label} ({name})")
