import gzip
import io
import json
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.files.storage import storages
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apiary import projectdata
from apiary.models import DataSnapshot, Site, SiteVersion
from demo.models import MortalityBill

ADMIN = "/admin/apiary/datasnapshot/"


# Transactional: restoring runs DDL in its own transaction, which SQLite
# cannot nest in a test's.
class SnapshotAdminTests(TransactionTestCase):
    databases = "__all__"  # the list checks every project's schema

    def setUp(self):
        self.root = User.objects.create_superuser("root", password="x")
        self.client.force_login(self.root)
        MortalityBill.objects.create(year=1665, type="Plague", count=10)

    def test_list_shows_schema_state(self):
        current = projectdata.take_snapshot("demo", kind="manual", label="today")
        older = projectdata.take_snapshot("demo", kind="manual")
        DataSnapshot.objects.filter(pk=older.pk).update(migrations=["0000_gone"])
        html = self.client.get(ADMIN).content.decode()
        self.assertIn("today", html)
        self.assertIn("no: older migrations", html)
        self.assertIn(f"{ADMIN}take/", html)

    def test_take_a_snapshot(self):
        self.assertContains(self.client.get(f"{ADMIN}take/"), 'name="project"')
        response = self.client.post(f"{ADMIN}take/", {"project": "demo", "label": "before experiment"})
        snapshot = DataSnapshot.objects.get()
        self.assertRedirects(response, f"{ADMIN}{snapshot.pk}/change/")
        self.assertEqual((snapshot.kind, snapshot.label, snapshot.created_by), ("manual", "before experiment", self.root))

    def test_restore_compares_then_needs_the_project_name(self):
        snapshot = projectdata.take_snapshot("demo", kind="manual")
        MortalityBill.objects.create(year=1666, type="Fire", count=1)

        page = self.client.get(f"{ADMIN}{snapshot.pk}/restore/").content.decode()
        self.assertIn("<code>mortalitybill</code>", page)
        self.assertRegex(page, r'text-right">1</td><td class="text-right">2</td>')

        wrong = self.client.post(f"{ADMIN}{snapshot.pk}/restore/", {"confirm": "relec"})
        self.assertContains(wrong, 'Type &quot;demo&quot; to confirm.')
        self.assertEqual(MortalityBill.objects.count(), 2)

        response = self.client.post(f"{ADMIN}{snapshot.pk}/restore/", {"confirm": "demo"})
        before = DataSnapshot.objects.get(kind="pre-restore")
        self.assertRedirects(response, f"{ADMIN}{before.pk}/change/")
        self.assertEqual(MortalityBill.objects.count(), 1)
        self.assertEqual(before.row_counts, {"mortalitybill": 2, "location": 0, "activity": 0})

    def test_an_older_schema_offers_no_restore(self):
        snapshot = projectdata.take_snapshot("demo", kind="manual")
        DataSnapshot.objects.filter(pk=snapshot.pk).update(migrations=["0000_gone"])
        page = self.client.get(f"{ADMIN}{snapshot.pk}/restore/")
        self.assertContains(page, "Not restorable")
        self.assertNotContains(page, 'name="confirm"')
        self.client.post(f"{ADMIN}{snapshot.pk}/restore/", {"confirm": "demo"})
        self.assertFalse(DataSnapshot.objects.filter(kind="pre-restore").exists())

    def test_download_and_delete_with_files(self):
        snapshot = projectdata.take_snapshot("demo", kind="manual")
        response = self.client.get(f"{ADMIN}{snapshot.pk}/download/")
        self.assertIn(f'filename="demo-snapshot-{snapshot.pk}.json.gz"', response["Content-Disposition"])
        rows = json.loads(gzip.decompress(b"".join(response.streaming_content)))
        self.assertEqual(rows[0]["fields"]["type"], "Plague")

        self.client.post(f"{ADMIN}{snapshot.pk}/delete/", {"post": "yes"})
        self.assertFalse(DataSnapshot.objects.exists())
        self.assertFalse(storages["default"].exists(snapshot.data_path))
        self.assertFalse(storages["default"].exists(snapshot.manifest_path))

    def test_superusers_only(self):
        snapshot = projectdata.take_snapshot("demo", kind="manual")
        staff = User.objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        for path in (ADMIN, f"{ADMIN}take/", f"{ADMIN}{snapshot.pk}/restore/", f"{ADMIN}{snapshot.pk}/download/"):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        self.assertEqual(self.client.post(f"{ADMIN}{snapshot.pk}/restore/", {"confirm": "demo"}).status_code, 403)
        self.assertFalse(DataSnapshot.objects.filter(kind="pre-restore").exists())


class PruneTests(TestCase):
    databases = {"default", "demo_db"}

    def snapshot(self, kind, days_ago):
        snapshot = projectdata.take_snapshot("demo", kind=kind)
        DataSnapshot.objects.filter(pk=snapshot.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return snapshot

    def test_prunes_only_old_automatic_unused_snapshots(self):
        manual = self.snapshot("manual", 90)
        used = self.snapshot("site", 90)
        site = Site.objects.create(project="demo", name="s")
        SiteVersion.objects.create(site=site, number=1, data_snapshot=used)
        old = [self.snapshot("pre-reset", 60 + i) for i in range(3)]
        recent = self.snapshot("pre-restore", 1)

        out = StringIO()
        call_command("prune_snapshots", keep=1, days=30, stdout=out)
        self.assertIn("Would delete 3 snapshot(s)", out.getvalue())
        self.assertEqual(DataSnapshot.objects.count(), 6)

        call_command("prune_snapshots", keep=1, days=30, mode="delete", stdout=StringIO())
        # Kept: manual, the one a site version uses, and the newest automatic (keep=1).
        self.assertEqual(set(DataSnapshot.objects.values_list("pk", flat=True)), {manual.pk, used.pk, recent.pk})
        self.assertFalse(any(storages["default"].exists(s.data_path) for s in old))


class ExploreTests(TestCase):
    databases = "__all__"

    def setUp(self):
        from demo.models import Activity, Location

        self.root = User.objects.create_superuser("root", password="x")
        self.client.force_login(self.root)
        self.bank = Location.objects.create(location_name="Corn Exchange Bank", locality="Manhattan")
        for i in range(105):
            Activity.objects.create(investigation="nyc", operative=f"Operative {i}", activity="Shadowing",
                                    notes="<script>alert(1)</script>" if i == 0 else "", location=self.bank)
        self.snapshot = projectdata.take_snapshot("demo", kind="manual")
        self.url = f"{ADMIN}{self.snapshot.pk}/explore/"

    def test_browse_tables_rows_relations_search_and_pages(self):
        page = self.client.get(self.url).content.decode()  # first table: activity
        self.assertIn("activity <span", page)
        self.assertIn("location <span", page)
        self.assertIn("105 rows, page 1 of 2", page)
        self.assertIn(f'href="?table=location&amp;id={self.bank.pk}"', page)  # related row link
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)  # escaped, never markup
        self.assertNotIn("<script>alert(1)</script>", page)

        found = self.client.get(self.url, {"table": "activity", "q": "operative 42"}).content.decode()
        self.assertIn("1 row match", found)
        self.assertIn("Operative 42", found)

        row = self.client.get(self.url, {"table": "location", "id": self.bank.pk}).content.decode()
        self.assertIn("Row id", row)
        self.assertIn("Corn Exchange Bank", row)

        self.assertIn("Operative 104", self.client.get(self.url, {"table": "activity", "page": 2}).content.decode())
        self.assertEqual(self.client.get(self.url, {"table": "nope"}).status_code, 404)

    def test_console_data_is_one_ndjson_table_per_model(self):
        page = self.client.get(self.url).content.decode()
        data_url = f"{ADMIN}{self.snapshot.pk}/explore/location.ndjson"
        self.assertIn(data_url, page)  # in the console's table list
        response = self.client.get(data_url)
        self.assertEqual(response["Content-Type"], "application/x-ndjson")
        rows = [json.loads(line) for line in response.content.decode().splitlines()]
        self.assertEqual(rows[0]["id"], self.bank.pk)
        self.assertEqual(rows[0]["location_name"], "Corn Exchange Bank")
        self.assertEqual(self.client.get(f"{ADMIN}{self.snapshot.pk}/explore/nope.ndjson").status_code, 404)

        staff = User.objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(data_url).status_code, 403)

    def test_opens_a_snapshot_from_an_older_schema(self):
        records = [
            {"model": "demo.location", "pk": 1, "fields": {"locality": "Atlanta", "retired_field": "kept"}},
            {"model": "demo.gone", "pk": 9, "fields": {"name": "a table since removed"}},
        ]
        payload = gzip.compress(json.dumps(records).encode())
        storage = storages["default"]
        storage.delete(self.snapshot.data_path)
        storage.save(self.snapshot.data_path, io.BytesIO(payload))
        snapshot = DataSnapshot.objects.create(project="demo", kind="manual", sha256="x", size=len(payload))
        storage.save(snapshot.data_path, io.BytesIO(payload))

        page = self.client.get(f"{ADMIN}{snapshot.pk}/explore/", {"table": "location"}).content.decode()
        self.assertIn("retired_field", page)
        self.assertIn("kept", page)
        gone = self.client.get(f"{ADMIN}{snapshot.pk}/explore/", {"table": "gone"}).content.decode()
        self.assertIn("a table since removed", gone)
