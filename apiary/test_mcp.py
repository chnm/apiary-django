import json
from datetime import timedelta

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from apiary.mcp_server import transport_security  # builds the MCP app now, under the test settings
from apiary.mcp_testing import mcp_client

from apiary.models import APIToken
from demo.models import MortalityBill
from demo.models import Activity, Location

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def issue(user, **fields):
    token = APIToken(user=user, name="agent", **{"scopes": ["mcp:read"], **fields})
    secret = token.generate()
    token.save()
    return token, secret


# Transactional: the MCP app reads on its own thread, which can't see a
# TestCase's uncommitted rows.
class McpServerTests(TransactionTestCase):
    databases = "__all__"  # list_projects reads every project database

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mcp_client = mcp_client()

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

    def test_records_use_and_offers_its_tools(self):
        tools = {tool["name"]: tool for tool in self.rpc("tools/list").json()["result"]["tools"]}
        self.assertEqual(set(tools), {
            "list_projects", "query_records", "get_record", "aggregate", "list_changes", "list_sites",
            "create_records", "update_records", "delete_records", "propose_table_change", "apply_change",
            "reject_change", "revert_change",
            "create_site", "put_files", "delete_files", "publish_site", "rollback_site", "unpublish_site",
        })
        # Clients confirm before destructive tools.
        self.assertTrue(tools["query_records"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["delete_records"]["annotations"]["destructiveHint"])
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.last_used_at)

    def test_reads_any_project(self):
        error, projects = self.call("list_projects")
        self.assertFalse(error)
        self.assertEqual([p["project"] for p in projects["projects"]],
                         ["demo", "bom", "connthreads", "mappingviolence", "relec", "pinkertons"])
        demo = projects["projects"][0]
        self.assertEqual(demo["tables"]["activity"]["kind"], "code")
        self.assertEqual(demo["tables"]["activity"]["columns"]["location"], "relation -> location (nullable)")
        self.assertIsNone(demo["your_role"])

        error, page = self.call("query_records", project="demo", table="mortalitybill",
                                filters={"year__gte": 1666}, order_by=["-count"])
        self.assertFalse(error)
        self.assertEqual(page["count"], 1)
        self.assertEqual(page["results"][0]["type"], "Fever")

        first = MortalityBill.objects.order_by("pk").first().pk  # ids are not reset between transactional tests
        error, record = self.call("get_record", project="demo", table="mortalitybill", id=first)
        self.assertEqual(record["year"], 1665)

        error, rows = self.call("aggregate", project="demo", table="mortalitybill",
                                metric="sum", field="count", group_by=["year"])
        self.assertEqual(rows["rows"], [{"year": 1665, "value": 15}, {"year": 1666, "value": 3}])

        error, rows = self.call("aggregate", project="demo", table="activity",
                                group_by=["location__locality"])
        self.assertEqual(rows["rows"], [{"location__locality": "Manhattan, NYC", "value": 2}])

    def test_rejects_bad_input_with_a_message_the_agent_can_act_on(self):
        # The SDK hides any message but a ToolError's: "Error executing tool" alone
        # gives the agent nothing to correct.
        for tool, arguments, message in [
            ("query_records", {"project": "connthreads", "table": "textiles"},
             "No table 'textiles' in connthreads; one of none yet."),
            ("query_records", {"project": "auth", "table": "user"}, "Unknown project 'auth'"),
            ("query_records", {"project": "demo", "table": "user"}, "No table 'user' in demo"),
            ("query_records", {"project": "demo", "table": "mortalitybill", "filters": {"nope": 1}},
             "Unknown field 'nope'"),
            ("query_records", {"project": "demo", "table": "mortalitybill", "filters": {"year__regex": "."}},
             "Unsupported lookup 'regex' on 'year'"),
            ("query_records", {"project": "demo", "table": "mortalitybill", "filters": {"year": "abc"}},
             "expected a number but got 'abc'"),
            ("query_records", {"project": "demo", "table": "mortalitybill", "filters": {"year__type__exact": 1}},
             "'year' on mortalitybill is not a relation."),
            ("query_records", {"project": "demo", "table": "mortalitybill", "order_by": ["password"]},
             "Unknown field 'password'"),
            ("aggregate", {"project": "demo", "table": "mortalitybill", "metric": "sum"}, "sum needs a field."),
            ("get_record", {"project": "demo", "table": "mortalitybill", "id": 999}, "No row 999 in demo.mortalitybill"),
            ("create_records", {"project": "demo", "table": "mortalitybill", "rows": [{"year": 1}]},
             "lacks the mcp:write scope"),
        ]:
            error, text = self.call(tool, **arguments)
            self.assertTrue(error, (tool, arguments, text))
            self.assertIn(message, text, (tool, arguments))


class McpChangeTests(TransactionTestCase):
    """Writes and table changes over MCP: scopes on the token, roles in the project."""
    databases = "__all__"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mcp_client = mcp_client()

    def setUp(self):
        from apiary.live.testing import drop_live_tables

        self.addCleanup(drop_live_tables)
        self.owner = User.objects.create_user("owner", is_staff=True)
        self.owner.groups.add(Group.objects.create(name="relec-admin"))
        _, self.secret = issue(self.owner, scopes=["mcp:read", "mcp:write", "mcp:schema"])

    def call(self, tool, secret=None, **arguments):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        result = self.mcp_client.post("/mcp", json=body, headers={**ACCEPT, "Authorization": f"Bearer {secret or self.secret}"}).json()["result"]
        text = result["content"][0]["text"]
        return result["isError"], (text if result["isError"] else json.loads(text))

    def test_an_agent_designs_a_table_fills_it_and_undoes_a_change(self):
        error, proposal = self.call("propose_table_change", project="relec", reason="Transcribe the 1926 schedules", operations=[
            {"op": "create_table", "table": "congregations", "columns": [
                {"name": "name", "type": "text", "required": True}, {"name": "members", "type": "integer"}]}])
        self.assertFalse(error, proposal)
        self.assertEqual((proposal["status"], proposal["proposed_via"]), ("proposed", "MCP: API token agent"))
        self.assertIn("Creates table congregations with 2 columns.", proposal["preview"]["effects"])
        self.assertNotIn("plan", proposal["preview"])

        error, applied = self.call("apply_change", id=proposal["id"])
        self.assertEqual((error, applied["status"]), (False, "applied"))
        error, projects = self.call("list_projects")
        relec = next(p for p in projects["projects"] if p["project"] == "relec")
        self.assertEqual((relec["your_role"], relec["tables"]["congregations"]["kind"]), ("admin", "live"))

        error, created = self.call("create_records", project="relec", table="congregations",
                                   rows=[{"name": "First Baptist", "members": 120}, {"name": "St. Mark", "members": 80}])
        self.assertFalse(error, created)
        error, updated = self.call("update_records", project="relec", table="congregations", ids=created["ids"][:1],
                                   values={"members": 125}, reason="Recounted")
        self.assertEqual(updated["status"], "applied")
        error, page = self.call("query_records", project="relec", table="congregations", order_by=["name"])
        self.assertEqual([r["members"] for r in page["results"]], [125, 80])

        error, reverted = self.call("revert_change", id=updated["id"])
        self.assertFalse(error, reverted)
        error, page = self.call("query_records", project="relec", table="congregations", filters={"name": "First Baptist"})
        self.assertEqual(page["results"][0]["members"], 120)

        error, history = self.call("list_changes", project="relec")
        self.assertEqual([c["status"] for c in history["changes"]], ["applied", "reverted", "applied", "applied"])

    def test_scopes_and_roles_both_apply(self):
        _, read_write = issue(self.owner, scopes=["mcp:read", "mcp:write"])
        error, message = self.call("propose_table_change", secret=read_write, project="relec", reason="x",
                                   operations=[{"op": "create_table", "table": "t", "columns": []}])
        self.assertIn("lacks the mcp:schema scope", message)
        error, message = self.call("propose_table_change", project="bom", reason="x",
                                   operations=[{"op": "create_table", "table": "t", "columns": []}])
        self.assertIn("needs the admin role in bom", message)
        error, message = self.call("propose_table_change", project="relec", reason="x",
                                   operations=[{"op": "add_column", "table": "nope", "column": {"name": "a", "type": "text"}}])
        self.assertIn("No live table 'nope' in relec", message)


class ApiTokenAdminTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner")
        self.admin = User.objects.create_superuser("root", password="x")

    def test_superuser_creates_a_token_shown_once_and_stored_as_a_digest(self):
        self.client.force_login(self.admin)
        response = self.client.post("/admin/apiary/apitoken/add/", {"user": self.owner.pk, "name": "laptop agent",
                                                                     "scopes": ["mcp:write"]})
        self.assertEqual(response.status_code, 200)
        token = APIToken.objects.get()
        secret = response.context["secret"]
        self.assertTrue(secret.startswith(APIToken.PREFIX))
        self.assertContains(response, secret)
        self.assertEqual(token.digest, APIToken.hash(secret))
        self.assertNotIn(secret, json.dumps([token.name, token.hint, token.digest]))
        self.assertEqual(APIToken.authenticate(secret), token)
        self.assertEqual(token.scopes, ["mcp:read", "mcp:write"])

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


class TransportSecurityTests(TestCase):
    @override_settings(ALLOWED_HOSTS=["workspaces.test", ".sites.test"], SITES_DOMAIN="sites.test")
    def test_site_hosts_do_not_switch_off_dns_rebinding_protection(self):
        security = transport_security()
        self.assertTrue(security.enable_dns_rebinding_protection)
        self.assertEqual(security.allowed_hosts, ["workspaces.test", "workspaces.test:*"])

