from django.contrib.auth.models import Group, User
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import resolve

from apiary import changes
from apiary.live.testing import drop_live_tables
from apiary.models import Change
from apiary.projects import code_models, has_role, role
from demo.models import MortalityBill

ALL = {"default", "demo_db", "bom_db", "connthreads_db", "mappingviolence_db", "relec_db", "pinkertons_db"}


def member(name, project, role_name, **fields):
    user = User.objects.create_user(name, **fields)
    group, _ = Group.objects.get_or_create(name=f"{project}-{role_name}")
    user.groups.add(group)
    return user


class RoleTests(TestCase):
    def test_roles_come_from_project_groups_and_include_lesser_ones(self):
        editor = member("editor", "bom", "editor")
        self.assertEqual(role(editor, "bom"), "editor")
        self.assertTrue(has_role(editor, "bom", "viewer"))
        self.assertFalse(has_role(editor, "bom", "admin"))
        self.assertIsNone(role(editor, "relec"))
        self.assertEqual(role(User.objects.create_superuser("root"), "relec"), "admin")
        editor.is_active = False
        self.assertIsNone(role(editor, "bom"))


class CodeTableApiTests(TestCase):
    databases = {"default", "demo_db"}

    def test_every_code_model_gets_crud_under_its_project(self):
        for name, model in code_models("demo").items():
            for path in (f"/api/demo/v1/{name}/", f"/api/demo/v1/{name}/1/"):
                self.assertEqual(resolve(path).func.cls.model, model, path)

    @override_settings(SECURE_SSL_REDIRECT=False)
    def test_viewers_read_editors_write_admins_delete_and_writes_are_recorded(self):
        bill = MortalityBill.objects.create(year=1665, type="Plague", count=10)
        url = "/api/demo/v1/mortalitybill/"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(member("viewer", "demo", "viewer"))
        self.assertEqual(self.client.get(url).json()["count"], 1)
        self.assertEqual(self.client.post(url, {"year": 1666, "type": "Fever", "count": 3}).status_code, 403)

        editor = member("editor", "demo", "editor")
        self.client.force_login(editor)
        self.assertEqual(self.client.post(url, {"year": 1666, "type": "Fever", "count": 3}).status_code, 201)
        self.assertEqual(self.client.delete(f"{url}{bill.pk}/").status_code, 403)
        self.client.force_login(member("admin", "demo", "admin"))
        self.assertEqual(self.client.delete(f"{url}{bill.pk}/").status_code, 204)

        recorded = list(Change.objects.order_by("pk").values_list("summary", "proposed_via", "status"))
        self.assertEqual(recorded, [("Add 1 rows to mortalitybill", "API", "applied"),
                                    ("Delete 1 rows in mortalitybill", "API", "applied")])


@override_settings(SECURE_SSL_REDIRECT=False)
class LiveTableApiTests(TransactionTestCase):
    databases = ALL

    def setUp(self):
        self.addCleanup(drop_live_tables)
        self.admin = member("owner", "relec", "admin", is_staff=True)
        proposal = changes.propose_schema(self.admin, "relec", [{"op": "create_table", "table": "congregations", "columns": [
            {"name": "name", "type": "text", "required": True}, {"name": "members", "type": "integer"}]}],
            via="test", reason="Census schedules")
        changes.apply(proposal, self.admin, via="test")

    def test_live_tables_get_crud_that_follows_their_schema(self):
        url = "/api/relec/v1/congregations/"
        self.client.force_login(member("editor", "relec", "editor"))
        created = self.client.post(url, {"name": "First Baptist", "members": 120}, content_type="application/json")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.get(url).json()["results"], [{"id": created.json()["id"], "name": "First Baptist", "members": 120}])
        self.assertEqual(self.client.post(url, {"members": 5}, content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get("/api/relec/v1/nope/").status_code, 404)
        self.assertEqual(self.client.get("/api/nope/v1/congregations/").status_code, 404)

        changes.apply(changes.propose_schema(self.admin, "relec", [
            {"op": "add_column", "table": "congregations", "column": {"name": "state", "type": "text", "max_length": 2}}],
            via="test", reason="Map them"), self.admin, via="test")
        self.assertIn("state", self.client.get(url).json()["results"][0])

    def test_live_tables_need_a_role_in_their_project(self):
        self.client.force_login(member("other", "bom", "admin"))
        self.assertEqual(self.client.get("/api/relec/v1/congregations/").status_code, 403)
