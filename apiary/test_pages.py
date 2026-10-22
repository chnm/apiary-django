import base64
import json

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase, override_settings
from apiary.mcp_testing import mcp_client

from apiary import pages
from apiary.models import APIToken, DataSnapshot, Site
from bom.models import MortalityBill

PAGES = {"SITES_DOMAIN": "sites.test", "SITES_URL": "https://{name}.sites.test/", "PAGES_HOST": "pages.test",
         "ALLOWED_HOSTS": ["testserver", ".sites.test", "pages.test", "localhost"]}


@override_settings(**PAGES)
class PagesTests(TestCase):
    databases = {"default", "bom_db"}

    def setUp(self):
        self.staff = User.objects.create_superuser("editor", password="x")
        MortalityBill.objects.create(year=1665, type="Plague", count=10)
        self.site = pages.create_site("bom", "plague", title="Plague", user=self.staff)

    def get(self, path, host="plague.sites.test"):
        return self.client.get(path, HTTP_HOST=host)

    def body(self, response):
        return b"".join(response.streaming_content) if response.streaming else response.content

    def test_publish_serves_files_and_a_frozen_dataset(self):
        pages.put_files(self.site, {"index.html": b"<h1>Plague</h1>", "css/site.css": b"h1{}"}, user=self.staff)
        pages.attach_dataset(self.site, "deaths-by-year", params={"type": "Plague"}, user=self.staff)
        version = pages.publish(self.site, user=self.staff)

        self.assertEqual(version.number, 1)
        self.assertEqual(sorted(version.files), ["css/site.css", "data/deaths-by-year.json", "index.html"])
        snapshot = version.data_snapshot
        self.assertEqual((snapshot.kind, snapshot.row_counts), ("page", {"mortalitybill": 1}))

        response = self.get("/")
        self.assertEqual((response.status_code, self.body(response)), (200, b"<h1>Plague</h1>"))
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("sandbox allow-scripts", response["Content-Security-Policy"])
        self.assertIn("connect-src http://plague.sites.test;", response["Content-Security-Policy"])
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")
        data = json.loads(self.body(self.get("/data/deaths-by-year.json")))
        self.assertEqual(data, [{"year": 1665, "type": "Plague", "deaths": 10}])

        # Frozen: later changes to the data reach the page only on republishing.
        MortalityBill.objects.create(year=1666, type="Plague", count=5)
        self.assertEqual(len(json.loads(self.body(self.get("/data/deaths-by-year.json")))), 1)

        # The former shared host redirects to the site's own host and lists it.
        old = self.get("/bom/plague/data/deaths-by-year.json", host="pages.test")
        self.assertEqual((old.status_code, old["Location"]), (301, "https://plague.sites.test/data/deaths-by-year.json"))
        self.assertEqual(self.get("/bom/plague", host="pages.test")["Location"], "https://plague.sites.test/")
        self.assertIn(b'href="https://plague.sites.test/"', self.get("/", host="pages.test").content)

    def test_versions_and_rollback(self):
        pages.put_files(self.site, {"index.html": b"one"}, user=self.staff)
        pages.publish(self.site, user=self.staff)
        pages.put_files(self.site, {"index.html": b"two", "extra.txt": b"x"}, user=self.staff)
        self.assertEqual(self.body(self.get("/")), b"one")  # drafts are not served
        pages.publish(self.site, user=self.staff)
        self.assertEqual(self.body(self.get("/")), b"two")

        pages.rollback(self.site, 1)
        self.assertEqual(self.body(self.get("/")), b"one")
        self.assertEqual(self.get("/extra.txt").status_code, 404)

        pages.unpublish(self.site)
        self.assertEqual(self.get("/").status_code, 404)
        with self.assertRaisesMessage(pages.PagesError, "no published version 7"):
            pages.rollback(self.site, 7)

    def test_a_new_draft_starts_from_the_published_files(self):
        pages.put_files(self.site, {"index.html": b"one", "a.css": b"a"}, user=self.staff)
        pages.publish(self.site, user=self.staff)
        pages.delete_files(self.site, ["a.css"], user=self.staff)
        version = pages.publish(self.site, user=self.staff)
        self.assertEqual((version.number, sorted(version.files)), (2, ["index.html"]))
        self.assertEqual(self.body(self.get("/")), b"one")

    def test_hosts_are_kept_apart(self):
        pages.put_files(self.site, {"index.html": b"page"}, user=self.staff)
        pages.publish(self.site, user=self.staff)
        other = pages.create_site("relec", "families", user=self.staff)
        pages.put_files(other, {"index.html": b"families"}, user=self.staff)
        pages.publish(other, user=self.staff)

        self.assertEqual(self.body(self.get("/", host="families.sites.test")), b"families")
        for host in ("plague.sites.test", "sites.test", "a.b.sites.test", "nope.sites.test", "pages.test"):
            self.assertEqual(self.get("/admin/login/", host=host).status_code, 404, host)
        for host in ("sites.test", "a.b.sites.test", "nope.sites.test"):
            self.assertEqual(self.get("/", host=host).status_code, 404, host)
        self.assertEqual(self.get("/admin/login/", host="testserver").status_code, 200)  # the app's own host

    def test_rejects_bad_files(self):
        for files, message in [
            ({"../x.html": b""}, "Bad path"),
            ({"/abs.html": b""}, "Bad path"),
            ({"run.exe": b""}, "file type not allowed"),
            ({"big.txt": b"x" * (pages.MAX_FILE_BYTES + 1)}, "the limit is"),
        ]:
            with self.assertRaisesMessage(pages.PagesError, message):
                pages.put_files(self.site, files, user=self.staff)
        with self.assertRaisesMessage(pages.PagesError, "needs an index.html"):
            pages.publish(self.site, user=self.staff)
        with self.assertRaisesMessage(pages.PagesError, "No dataset 'nope'"):
            pages.attach_dataset(self.site, "nope", user=self.staff)
        with self.assertRaisesMessage(pages.PagesError, "taken (by bom/plague): names are unique across projects"):
            pages.create_site("relec", "plague")
        for name in ("Bad Name", "-edge", "edge-", "a.b", "x" * 51):
            with self.assertRaisesMessage(pages.PagesError, "Site names are"):
                pages.create_site("bom", name)
        with self.assertRaisesMessage(pages.PagesError, "'www' is reserved"):
            pages.create_site("bom", "www")

    def test_datasets_use_the_publishers_permissions(self):
        outsider = User.objects.create_user("outsider", is_staff=True)
        pages.put_files(self.site, {"index.html": b"x"}, user=outsider)
        pages.attach_dataset(self.site, "deaths-by-year", user=outsider)
        with self.assertRaisesMessage(pages.PagesError, "answered 401"):
            pages.publish(self.site, user=outsider)
        self.assertIsNone(Site.objects.get().live_version)


# Transactional: the MCP app works on its own thread (see test_mcp).
@override_settings(**PAGES)
class PagesToolTests(TransactionTestCase):
    databases = {"default", "bom_db"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mcp_client = mcp_client()

    def token(self, user):
        token = APIToken(user=user, name="agent")
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

        error, datasets = self.call(staff, "list_datasets", project="bom")
        self.assertEqual(datasets["datasets"][0]["name"], "deaths-by-year")
        self.assertFalse(self.call(staff, "create_site", project="bom", name="plague")[0])
        error, result = self.call(staff, "put_files", project="bom", site="plague", files=[
            {"path": "index.html", "content": "<h1>Plague</h1>"},
            {"path": "dot.png", "content": base64.b64encode(b"\x89PNG").decode(), "encoding": "base64"},
        ])
        self.assertEqual((error, result["files"]), (False, ["dot.png", "index.html"]))
        self.assertFalse(self.call(staff, "attach_dataset", project="bom", site="plague", dataset="deaths-by-year")[0])
        error, published = self.call(staff, "publish_site", project="bom", site="plague")
        self.assertFalse(error, published)
        self.assertEqual(published["url"], "https://plague.sites.test/")
        self.assertEqual(DataSnapshot.objects.get().pk, published["data_snapshot"])

        error, sites = self.call(staff, "list_sites", project="bom")
        self.assertEqual(sites["sites"][0]["live_version"], 1)

    def test_only_staff_publish(self):
        reader = self.token(User.objects.create_user("reader"))
        error, message = self.call(reader, "create_site", project="bom", name="plague")
        self.assertTrue(error)
        self.assertIn("needs a staff account", message)
        self.assertFalse(self.call(reader, "list_sites")[0])  # reading stays open
