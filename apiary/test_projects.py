from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from apiary.models import Project


class SyncProjectsTests(TestCase):
    def test_creates_missing_rows_keeps_edited_ones_and_is_idempotent(self):
        Project.objects.create(app_label="relec", name="Religious Ecologies", description="Edited in the admin")
        out = StringIO()
        call_command("sync_projects", stdout=out)
        self.assertEqual(
            dict(Project.objects.values_list("app_label", "name")),
            {"demo": "Demo", "bom": "Death by Numbers", "connthreads": "Connecting Threads", "mappingviolence": "Mapping Violence",
             "relec": "Religious Ecologies", "pinkertons": "Pinkertons"},
        )
        self.assertIn("kept relec", out.getvalue())
        self.assertEqual(Project.objects.get(app_label="relec").description, "Edited in the admin")

        call_command("sync_projects", stdout=StringIO())
        self.assertEqual(Project.objects.count(), 6)

    def test_then_every_project_gets_its_role_groups(self):
        call_command("sync_projects", stdout=StringIO())
        call_command("init_project_groups", stdout=StringIO())
        names = set(Group.objects.values_list("name", flat=True))
        for label in ("demo", "bom", "connthreads", "mappingviolence", "relec", "pinkertons"):
            self.assertLessEqual({f"{label}-viewer", f"{label}-editor", f"{label}-admin"}, names, label)
        # Code models get the role's model permissions; live tables need none.
        viewer = Group.objects.get(name="demo-viewer")
        self.assertEqual(sorted(viewer.permissions.values_list("codename", flat=True)),
                         ["view_activity", "view_location", "view_mortalitybill"])
        self.assertFalse(Group.objects.get(name="bom-admin").permissions.exists())
