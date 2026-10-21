import json
from datetime import timedelta

from django.contrib.auth.models import Permission, User
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from starlette.testclient import TestClient

from apiary.models import APIToken
from bom.models import MortalityBill
from pinkertons.models import Activity, Location

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def issue(user, **fields):
    token = APIToken(user=user, name="agent", **fields)
    secret = token.generate()
    token.save()
    return token, secret


# Transactional: the MCP app reads on its own thread, which can't see a
# TestCase's uncommitted rows.
class McpServerTests(TransactionTestCase):
    databases = {"default", "bom_db", "pinkertons_db"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from apiary.mcp_server import app

        # The SDK's session manager starts once per process: one client for all
        # tests. Not self.client, which Django replaces with its own before each test.
        cls.mcp_client = TestClient(app, base_url="http://localhost")
        cls.mcp_client.__enter__()
        cls.addClassCleanup(cls.mcp_client.__exit__, None, None, None)

    def setUp(self):
        self.user = User.objects.create_user("reader")
        self.token, self.secret = issue(self.user)
        MortalityBill.objects.bulk_create([
            MortalityBill(year=1665, type="Plague", count=10),
            MortalityBill(year=1665, type="Plague", count=5),
            MortalityBill(year=1666, type="Fever", count=3),
        ])
        bank = Location.objects.create(locality="Manhattan, NYC", location_name="Bank")
        Activity.objects.create(investigation="nyc", activity="Surveillance", location=bank)
        Activity.objects.create(investigation="nyc", activity="Shadowing", location=bank)

    def rpc(self, method, params=None, secret=None):
        headers = {**ACCEPT, "Authorization": f"Bearer {secret or self.secret}"}
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        return self.mcp_client.post("/mcp", json=body, headers=headers)

    def call(self, tool, **arguments):
        result = self.rpc("tools/call", {"name": tool, "arguments": arguments}).json()["result"]
        text = result["content"][0]["text"]
        return result["isError"], (text if result["isError"] else json.loads(text))

    def test_requires_an_active_token_of_an_active_user(self):
        response = self.mcp_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=ACCEPT)
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.headers["www-authenticate"].startswith("Bearer"))
        self.assertEqual(self.rpc("tools/list", secret="apiary_wrong").status_code, 401)

        _, expired = issue(self.user, expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.rpc("tools/list", secret=expired).status_code, 401)

        self.assertEqual(self.rpc("tools/list").status_code, 200)
        APIToken.objects.filter(pk=self.token.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.rpc("tools/list").status_code, 401)

        _, other = issue(self.user)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.rpc("tools/list", secret=other).status_code, 401)

    def test_records_use_and_offers_only_read_tools(self):
        tools = {tool["name"] for tool in self.rpc("tools/list").json()["result"]["tools"]}
        self.assertEqual(tools, {"list_projects", "query_records", "get_record", "aggregate"})
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.last_used_at)

    def test_reads_any_project(self):
        error, projects = self.call("list_projects")
        self.assertFalse(error)
        self.assertEqual([p["project"] for p in projects["projects"]],
                         ["bom", "connthreads", "mappingviolence", "relec", "pinkertons"])

        error, page = self.call("query_records", project="bom", model="mortalitybill",
                                filters={"year__gte": 1666}, order_by=["-count"])
        self.assertFalse(error)
        self.assertEqual(page["count"], 1)
        self.assertEqual(page["results"][0]["type"], "Fever")

        error, record = self.call("get_record", project="bom", model="mortalitybill", id=1)
        self.assertEqual(record["year"], 1665)

        error, rows = self.call("aggregate", project="bom", model="mortalitybill",
                                metric="sum", field="count", group_by=["year"])
        self.assertEqual(rows["rows"], [{"year": 1665, "value": 15}, {"year": 1666, "value": 3}])

        error, rows = self.call("aggregate", project="pinkertons", model="activity",
                                group_by=["location__locality"])
        self.assertEqual(rows["rows"], [{"location__locality": "Manhattan, NYC", "value": 2}])

    def test_rejects_bad_input_with_a_message_the_agent_can_act_on(self):
        # The SDK hides any message but a ToolError's: "Error executing tool" alone
        # gives the agent nothing to correct.
        for tool, arguments, message in [
            ("query_records", {"project": "connthreads", "model": "textiles"},
             "Unknown model 'textiles' in connthreads; one of textile."),
            ("query_records", {"project": "auth", "model": "user"}, "Unknown project 'auth'"),
            ("query_records", {"project": "bom", "model": "user"}, "Unknown model 'user'"),
            ("query_records", {"project": "bom", "model": "mortalitybill", "filters": {"nope": 1}},
             "Unknown field 'nope'"),
            ("query_records", {"project": "bom", "model": "mortalitybill", "filters": {"year__regex": "."}},
             "Unsupported lookup 'regex' on 'year'"),
            ("query_records", {"project": "bom", "model": "mortalitybill", "filters": {"year": "abc"}},
             "expected a number but got 'abc'"),
            ("query_records", {"project": "bom", "model": "mortalitybill", "filters": {"year__type__exact": 1}},
             "'year' on mortalitybill is not a relation."),
            ("query_records", {"project": "bom", "model": "mortalitybill", "order_by": ["password"]},
             "Unknown field 'password'"),
            ("aggregate", {"project": "bom", "model": "mortalitybill", "metric": "sum"}, "sum needs a field."),
            ("get_record", {"project": "bom", "model": "mortalitybill", "id": 999}, "No mortalitybill with id 999"),
        ]:
            error, text = self.call(tool, **arguments)
            self.assertTrue(error, (tool, arguments, text))
            self.assertIn(message, text, (tool, arguments))


class ApiTokenAdminTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner")
        self.admin = User.objects.create_superuser("root", password="x")

    def test_superuser_creates_a_token_shown_once_and_stored_as_a_digest(self):
        self.client.force_login(self.admin)
        response = self.client.post("/admin/apiary/apitoken/add/", {"user": self.owner.pk, "name": "laptop agent"})
        self.assertEqual(response.status_code, 200)
        token = APIToken.objects.get()
        secret = response.context["secret"]
        self.assertTrue(secret.startswith(APIToken.PREFIX))
        self.assertContains(response, secret)
        self.assertEqual(token.digest, APIToken.hash(secret))
        self.assertNotIn(secret, json.dumps([token.name, token.hint, token.digest]))
        self.assertEqual(APIToken.authenticate(secret), token)

        changelist = self.client.get("/admin/apiary/apitoken/")
        self.assertNotContains(changelist, secret)

    def test_revoke_action(self):
        token, secret = issue(self.owner)
        self.client.force_login(self.admin)
        self.client.post("/admin/apiary/apitoken/", {"action": "revoke", "_selected_action": [token.pk]})
        token.refresh_from_db()
        self.assertIsNotNone(token.revoked_at)
        self.assertIsNone(APIToken.authenticate(secret))

    def test_only_superusers_manage_tokens(self):
        staff = User.objects.create_user("staff", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__endswith="apitoken"))
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/admin/apiary/apitoken/").status_code, 403)
        self.assertEqual(self.client.get("/admin/apiary/apitoken/add/").status_code, 403)
