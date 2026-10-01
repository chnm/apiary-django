import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the DJANGO_SUPERUSER_USERNAME superuser if it doesn't exist (idempotent)"

    def handle(self, *args, **options):
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
        if not (username and password):
            self.stdout.write('DJANGO_SUPERUSER_USERNAME/PASSWORD not set; skipping')
            return

        User = get_user_model()
        # Never touch an existing account, so a password changed in the admin survives redeploys.
        if User.objects.filter(username=username).exists():
            self.stdout.write(f'Superuser "{username}" already exists')
            return

        User.objects.create_superuser(
            username=username,
            email=os.environ.get('DJANGO_SUPERUSER_EMAIL', ''),
            password=password,
        )
        self.stdout.write(
            self.style.SUCCESS(f'Superuser "{username}" created successfully')
        )
