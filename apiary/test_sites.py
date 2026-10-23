import base64
import json

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase, override_settings
from apiary.mcp_testing import mcp_client

from apiary import projectdata, sites
from apiary.models import APIToken, DataSnapshot, Site
from demo.models import MortalityBill

SITES = {"SITES_DOMAIN": "sites.test", "SITES_URL": "https://{name}.sites.test/",
         "ALLOWED_HOSTS": ["testserver", ".sites.test", "localhost"]}


@override_settings(**SITES)
class SiteTests(TestCase):
    databases = {"default", "demo_db"}

    def setUp(self):
        self.staff = User.objects.create_superuser("editor", password="x")
        MortalityBill.objects.create(year=1665, type="Plague", count=10)
        self.site = sites.create_site("demo", "plague", title="Plague", user=self.staff)

    def get(self, path, host="plague.sites.test"):
        return self.client.get(path, HTTP_HOST=host)

    def body(self, response):
        return b"".join(response.streaming_content) if response.streaming else response.content

    def test_publish_serves_files(self):
        sites.put_files(self.site, {"index.html": b"<h1>Plague</h1>", "css/site.css": b"h1{}",
                                    "data/bills.json": b'[{"year": 1665}]'}, user=self.staff)
        version = sites.publish(self.site, user=self.staff)

        self.assertEqual(version.number, 1)
        self.assertEqual(sorted(version.files), ["css/site.css", "data/bills.json", "index.html"])
        self.assertIsNone(version.data_snapshot)

        response = self.get("/")
        self.assertEqual((response.status_code, self.body(response)), (200, b"<h1>Plague</h1>"))
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("sandbox allow-scripts", response["Content-Security-Policy"])
        self.assertIn("connect-src http://plague.sites.test;", response["Content-Security-Policy"])
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")
        self.assertEqual(json.loads(self.body(self.get("/data/bills.json"))), [{"year": 1665}])

    def test_versions_and_rollback(self):
        sites.put_files(self.site, {"index.html": b"one"}, user=self.staff)
        sites.publish(self.site, user=self.staff)
        sites.put_files(self.site, {"index.html": b"two", "extra.txt": b"x"}, user=self.staff)
        self.assertEqual(self.body(self.get("/")), b"one")  # drafts are not served
        sites.publish(self.site, user=self.staff)
        self.assertEqual(self.body(self.get("/")), b"two")

        sites.rollback(self.site, 1)
        self.assertEqual(self.body(self.get("/")), b"one")
        self.assertEqual(self.get("/extra.txt").status_code, 404)

        sites.unpublish(self.site)
        self.assertEqual(self.get("/").status_code, 404)
        with self.assertRaisesMessage(sites.SiteError, "no published version 7"):
            sites.rollback(self.site, 7)

    def test_a_new_draft_starts_from_the_published_files(self):
        sites.put_files(self.site, {"index.html": b"one", "a.css": b"a"}, user=self.staff)
        sites.publish(self.site, user=self.staff)
        sites.delete_files(self.site, ["a.css"], user=self.staff)
        version = sites.publish(self.site, user=self.staff)
        self.assertEqual((version.number, sorted(version.files)), (2, ["index.html"]))
        self.assertEqual(self.body(self.get("/")), b"one")

    def test_hosts_are_kept_apart(self):
        sites.put_files(self.site, {"index.html": b"page"}, user=self.staff)
        sites.publish(self.site, user=self.staff)
        other = sites.create_site("relec", "families", user=self.staff)
        sites.put_files(other, {"index.html": b"families"}, user=self.staff)
        sites.publish(other, user=self.staff)

        self.assertEqual(self.body(self.get("/", host="families.sites.test")), b"families")
        for host in ("plague.sites.test", "sites.test", "a.b.sites.test", "nope.sites.test"):
            self.assertEqual(self.get("/admin/login/", host=host).status_code, 404, host)
        for host in ("sites.test", "a.b.sites.test", "nope.sites.test"):
            self.assertEqual(self.get("/", host=host).status_code, 404, host)
        self.assertEqual(self.get("/admin/login/", host="testserver").status_code, 200)  # the app's own host

    def test_rejects_bad_files(self):
        for files, message in [
            ({"../x.html": b""}, "Bad path"),
            ({"/abs.html": b""}, "Bad path"),
            ({"run.exe": b""}, "file type not allowed"),
            ({"big.txt": b"x" * (sites.MAX_FILE_BYTES + 1)}, "the limit is"),
        ]:
            with self.assertRaisesMessage(sites.SiteError, message):
                sites.put_files(self.site, files, user=self.staff)
        with self.assertRaisesMessage(sites.SiteError, "needs an index.html"):
            sites.publish(self.site, user=self.staff)
        with self.assertRaisesMessage(sites.SiteError, "taken (by demo/plague): names are unique across projects"):
            sites.create_site("relec", "plague")
        for name in ("Bad Name", "-edge", "edge-", "a.b", "x" * 51):
            with self.assertRaisesMessage(sites.SiteError, "Site names are"):
                sites.create_site("demo", name)
        with self.assertRaisesMessage(sites.SiteError, "'www' is reserved"):
            sites.create_site("demo", "www")


# Transactional: the MCP app works on its own thread (see test_mcp).
@override_settings(**SITES)
class SiteToolTests(TransactionTestCase):
    databases = {"default", "demo_db"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mcp_client = mcp_client()

    def token(self, user, scopes=("mcp:read", "mcp:write")):
        token = APIToken(user=user, name="agent", scopes=list(scopes))
        secret = token.generate()
        token.save()
        return secret

    def call(self, secret, tool, **arguments):
        response = self.mcp_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments},
        }, headers={"Accept": "application/json, text/event-stream", "Authorization": f"Bearer {secret}"})
        result = response.json()["result"]
        text = result["content"][0]["text"]
        return result["isError"], (text if result["isError"] else json.loads(text))

    def test_staff_build_and_publish_a_site(self):
        MortalityBill.objects.create(year=1665, type="Plague", count=10)
        staff = self.token(User.objects.create_superuser("editor", password="x"))

        self.assertFalse(self.call(staff, "create_site", project="demo", name="plague")[0])
        error, result = self.call(staff, "put_files", project="demo", site="plague", files=[
            {"path": "index.html", "content": "<h1>Plague</h1>"},
            {"path": "dot.png", "content": base64.b64encode(b"\x89PNG").decode(), "encoding": "base64"},
        ])
        self.assertEqual((error, result["files"]), (False, ["dot.png", "index.html"]))
        error, published = self.call(staff, "publish_site", project="demo", site="plague")
        self.assertFalse(error, published)
        self.assertEqual(published["url"], "https://plague.sites.test/")

        error, sites = self.call(staff, "list_sites", project="demo")
        self.assertEqual(sites["sites"][0]["live_version"], 1)

    def test_only_staff_with_the_write_scope_publish(self):
        reader = self.token(User.objects.create_user("reader"))
        error, message = self.call(reader, "create_site", project="demo", name="plague")
        self.assertTrue(error)
        self.assertIn("needs a staff account", message)
        self.assertFalse(self.call(reader, "list_sites")[0])  # reading stays open

        read_only = self.token(User.objects.create_superuser("root", password="x"), scopes=["mcp:read"])
        error, message = self.call(read_only, "create_site", project="demo", name="plague")
        self.assertTrue(error)
        self.assertIn("lacks the mcp:write scope", message)


class SitesNamingMigrationTests(TestCase):
    """0006 moves site files from pages/ to sites/ and renames the snapshot kind."""
    databases = {"default", "demo_db"}

    def test_moves_files_and_renames_kind_both_ways(self):
        from importlib import import_module

        from django.apps import apps
        from django.core.files.base import ContentFile
        from django.core.files.storage import storages

        from apiary.models import SiteVersion

        migration = import_module("apiary.migrations.0006_sites_naming")
        site = Site.objects.create(project="demo", name="old")
        version = SiteVersion.objects.create(site=site, number=1, files={"index.html": {}, "css/a.css": {}})
        old = f"pages/demo/{site.key}/v1/"
        storage = storages["default"]
        for path in version.files:
            storage.save(old + path, ContentFile(path.encode()))
        snapshot = projectdata.take_snapshot("demo", kind="manual")
        DataSnapshot.objects.filter(pk=snapshot.pk).update(kind="page")

        migration.to_sites(apps, None)
        migration.to_sites(apps, None)  # safe to run twice
        for path in version.files:
            self.assertFalse(storage.exists(old + path))
            self.assertEqual(storage.open(version.prefix + path).read(), path.encode())
        self.assertEqual(DataSnapshot.objects.get(pk=snapshot.pk).kind, "site")

        migration.to_pages(apps, None)
        self.assertTrue(all(storage.exists(old + path) for path in version.files))
        self.assertEqual(DataSnapshot.objects.get(pk=snapshot.pk).kind, "page")
