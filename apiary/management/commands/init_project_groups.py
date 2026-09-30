"""
Management command to create role groups for each project.

Creates groups with view/add/change/delete permissions for each project app.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.apps import apps
from apiary.models import Project


class Command(BaseCommand):
    help = "Create role groups for each project (idempotent)"

    def handle(self, *args, **options):

        # Get projects from database
        projects = [
            {'name': project.name, 'app_label': project.app_label}
            for project in Project.objects.all()
        ]
        if not projects:
            self.stdout.write(
                self.style.WARNING('No projects found in database. Load fixtures first.')
            )
            return

        # Define role types
        roles = [
            {
                'suffix': 'viewer',
                'permissions': ['view'],
                'description': 'Can view all data'
            },
            {
                'suffix': 'editor',
                'permissions': ['view', 'add', 'change'],
                'description': 'Can view, add, and edit data'
            },
            {
                'suffix': 'admin',
                'permissions': ['view', 'add', 'change', 'delete'],
                'description': 'Full access to all data'
            },
        ]

        for project in projects:
            app_label = project['app_label']
            project_name = project['name']

            self.stdout.write(self.style.SUCCESS(f"\n{project_name} - {app_label}"))

            # Get all models for this app
            try:
                app_config = apps.get_app_config(app_label)
            except LookupError:
                self.stdout.write(
                    self.style.WARNING(f'App "{app_label}" not found, skipping...')
                )
                continue

            models = list(app_config.get_models())

            if not models:
                self.stdout.write(
                    self.style.WARNING(f'No models found in "{app_label}", skipping...')
                )
                continue

            # Create groups for each role
            for role in roles:
                self.stdout.write(self.style.SUCCESS(f'Role: "{role['suffix']}"'))

                group_name = f"{app_label}-{role['suffix']}"
                
                # Create or get the group
                group, created = Group.objects.get_or_create(name=group_name)
                
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(f'Created group: "{group_name}"')
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(f'Group "{group_name}" already exists, updating permissions...')
                    )
                    # Clear existing permissions to reset
                    group.permissions.clear()

                # Add permissions for all models in this app
                permissions_added = 0
                for model in models:
                    self.stdout.write(self.style.SUCCESS(f'"{model.__name__}"'))
                    content_type = ContentType.objects.get_for_model(model)
                    self.stdout.write(self.style.SUCCESS(f'"{content_type}"'))
                    
                    for perm_type in role['permissions']:
                        self.stdout.write(self.style.SUCCESS(f'"{perm_type}"'))
                        try:
                            codename = f"{perm_type}_{model._meta.model_name}"
                            permission = Permission.objects.get(
                                codename=codename,
                                content_type=content_type,
                            )
                            group.permissions.add(permission)
                            permissions_added += 1
                        except Permission.DoesNotExist:
                            self.stdout.write(
                                self.style.WARNING(
                                    f'  Permission "{codename}" not found for {model._meta.label}'
                                )
                            )

                self.stdout.write(
                    self.style.SUCCESS(
                        f'  Added {permissions_added} permissions to "{group_name}"'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS('\nProject groups initialized successfully!')
        )
