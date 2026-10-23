import json
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase

from apiary.views import ALLOWED_COMMANDS, management_commands_dashboard, run_management_command
from demo.models import Activity, Location, MortalityBill

DATABASES = {"default", "demo_db", "bom_db"}


def reset(project, **options):
    out = StringIO()
    call_command("reset_project_data", project=project, confirm=options.pop("confirm", project), stdout=out, **options)
    return out.getvalue()


class ResetProjectDataTests(TestCase):
    databases = DATABASES

    def test_replaces_the_rows_with_the_fixtures(self):
        MortalityBill.objects.create(year=1900, type="Not a fixture", count=1)
        output = reset("demo")
        self.assertEqual(MortalityBill.objects.count(), 31)
        self.assertFalse(MortalityBill.objects.filter(type="Not a fixture").exists())
        self.assertIn("mortalitybill: 1 -> 31 rows", output)

    def test_related_tables_reset_together(self):
        reset("demo")
        self.assertEqual((Location.objects.count(), Activity.objects.count()), (30, 40))
        reset("demo", no_fixtures=True)
        self.assertEqual((Location.objects.count(), Activity.objects.count()), (0, 0))

    def test_needs_the_project_name_typed_again(self):
        MortalityBill.objects.create(year=1900, type="Kept", count=1)
        with self.assertRaisesMessage(CommandError, 'Type the project name "demo" to confirm.'):
            reset("demo", confirm="relec")
        self.assertEqual(MortalityBill.objects.count(), 1)

    def test_a_failed_load_leaves_the_data_as_it_was(self):
        MortalityBill.objects.create(year=1900, type="Kept", count=1)
        with patch("apiary.management.commands.reset_project_data.call_command", side_effect=RuntimeError("bad fixture")):
            with self.assertRaises(RuntimeError):
                reset("demo")
        self.assertEqual(list(MortalityBill.objects.values_list("type", flat=True)), ["Kept"])

    def test_leaves_other_projects_alone(self):
        MortalityBill.objects.create(year=1900, type="Kept", count=1)
        output = reset("bom")  # no tables in code and no fixtures: nothing to load
        self.assertIn("No fixtures loaded.", output)
        self.assertEqual(MortalityBill.objects.count(), 1)


class ResetProjectDataDashboardTests(TestCase):
    def superuser_request(self, method, path, data=None):
        request = getattr(RequestFactory(), method)(path, data or {})
        request.user = MagicMock(is_active=True, is_superuser=True)
        return request

    def test_dashboard_offers_a_project_choice(self):
        html = management_commands_dashboard(self.superuser_request("get", "/apiary/mgmt/")).content.decode()
        self.assertIn('<select id="reset_project_data_project" name="project" required>', html)
        for project in ALLOWED_COMMANDS["reset_project_data"]["args"][0]["choices"]:
            self.assertIn(f'<option value="{project}">', html)

    def test_dashboard_passes_project_and_confirmation(self):
        finished = MagicMock(returncode=0, stdout="done", stderr="")
        with patch("apiary.views.subprocess.run", return_value=finished) as run:
            response = run_management_command(self.superuser_request("post", "/apiary/run-command/", {
                "command": "reset_project_data", "project": "demo", "confirm": "demo",
            }))
        self.assertTrue(json.loads(response.content)["success"])
        self.assertEqual(run.call_args.args[0][2:], ["reset_project_data", "--project", "demo", "--confirm", "demo"])

        response = run_management_command(self.superuser_request("post", "/apiary/run-command/", {
            "command": "reset_project_data", "project": "demo",
        }))
        self.assertEqual(response.status_code, 400)
