from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.db import OperationalError
from django.test import SimpleTestCase, override_settings


@override_settings(SECURE_SSL_REDIRECT=False)
class DeploymentTests(SimpleTestCase):
    def test_health_checks_every_database_and_fails_closed(self):
        aliases = ("default", "bom_db", "connthreads_db", "mappingviolence_db", "relec_db")
        databases = {alias: MagicMock() for alias in aliases}
        with patch("apiary.health.connections", databases):
            self.assertEqual(self.client.get("/health/").status_code, 200)
            for database in databases.values():
                database.cursor.return_value.__enter__.return_value.execute.assert_called_once_with("SELECT 1")
            databases["bom_db"].cursor.side_effect = OperationalError("do not expose credentials")
            response = self.client.get("/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "database unavailable"})

    def test_migrations_create_schemas_and_cover_every_alias_without_loading_fixtures(self):
        database = MagicMock()
        with patch("apiary.management.commands.migrate_projects.connections", {"default": database}), patch(
            "apiary.management.commands.migrate_projects.call_command"
        ) as migrate:
            call_command("migrate_projects")
        statements = database.cursor.return_value.__enter__.return_value.execute.call_args_list
        self.assertEqual([call.args[0] for call in statements], [
            f'CREATE SCHEMA IF NOT EXISTS "{app}"'
            for app in ("bom", "connthreads", "mappingviolence", "relec")
        ])
        self.assertEqual([call.kwargs["database"] for call in migrate.call_args_list], [
            "default", "bom_db", "connthreads_db", "mappingviolence_db", "relec_db",
        ])
        self.assertTrue(all(call.args[0] == "migrate" for call in migrate.call_args_list))

    def test_workflow_uses_pinned_github_k0s_reusable(self):
        from pathlib import Path

        import yaml

        root = Path(__file__).resolve().parent.parent
        workflow = yaml.load(
            (root / ".github/workflows/cicd.yml").read_text(), Loader=yaml.BaseLoader
        )
        deployment = workflow["jobs"]["deployment"]
        self.assertRegex(
            deployment["uses"],
            r"^chnm/\.github/\.github/workflows/django--k0s\.yml@[0-9a-f]{40}$",
        )
        self.assertEqual(deployment["with"]["image"], "rrchnm/apiary-django")
        self.assertEqual(deployment["permissions"]["contents"], "write")
        self.assertEqual(list(deployment["secrets"]), ["ZOT_TOKEN"])
        self.assertEqual(workflow["on"]["push"]["branches"], ["main"])
        self.assertEqual(workflow["on"]["pull_request"]["branches"], ["main"])
        self.assertEqual(workflow["on"]["push"]["paths-ignore"], ["k8s/kustomization.yaml"])
        self.assertFalse((root / ".forgejo/workflows/ci.yml").exists())
        self.assertNotIn("django--deploy.yml", str(workflow))
        self.assertNotIn("django--build-publish.yml", str(workflow))

    def test_environment_false_is_not_truthy_and_persistent_path_is_used(self):
        import os
        import subprocess
        import sys

        environment = {
            **os.environ, "DEBUG": "False", "OBJ_STORAGE": "False",
            "DJANGO_SECRET_KEY": "test-only", "BOM_DB_PATH": "/data/bom.sqlite3",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://workspaces.apiary.rrchnm.org",
        }
        subprocess.run([sys.executable, "-c", """
import django
django.setup()
from django.conf import settings
assert settings.DEBUG is False
assert settings.OBJ_STORAGE is False
assert settings.SESSION_COOKIE_SECURE
assert settings.CSRF_COOKIE_SECURE
assert settings.DATABASES["bom_db"]["NAME"] == "/data/bom.sqlite3"
assert settings.CSRF_TRUSTED_ORIGINS == ["https://workspaces.apiary.rrchnm.org"]
"""], env={**environment, "DJANGO_SETTINGS_MODULE": "apiary.settings"}, check=True)

    def test_init_superuser_creates_once_and_never_resets_an_existing_account(self):
        from io import StringIO

        User = MagicMock()
        credentials = {"DJANGO_SUPERUSER_USERNAME": "ops", "DJANGO_SUPERUSER_PASSWORD": "pw"}
        with patch("apiary.management.commands.init_superuser.get_user_model", return_value=User):
            with patch.dict("os.environ", {}, clear=True):
                call_command("init_superuser", stdout=StringIO())
            User.objects.filter.assert_not_called()

            with patch.dict("os.environ", credentials, clear=True):
                User.objects.filter.return_value.exists.return_value = False
                call_command("init_superuser", stdout=StringIO())
                User.objects.create_superuser.assert_called_once_with(username="ops", email="", password="pw")

                User.objects.filter.return_value.exists.return_value = True
                call_command("init_superuser", stdout=StringIO())
        self.assertEqual(User.objects.create_superuser.call_count, 1)

    def test_migrate_job_bootstraps_the_superuser_after_migrations(self):
        from pathlib import Path

        import yaml

        job = yaml.safe_load((Path(__file__).resolve().parent.parent / "k8s/migrate.yaml").read_text())
        command = job["spec"]["template"]["spec"]["containers"][0]["command"]
        self.assertEqual(command[-1], "python manage.py migrate_projects && python manage.py init_superuser")
