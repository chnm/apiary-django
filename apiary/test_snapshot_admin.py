import gzip
import json
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.files.storage import storages
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apiary import projectdata
from apiary.models import DataSnapshot, Site, SiteVersion
from bom.models import MortalityBill

ADMIN = "/admin/apiary/datasnapshot/"


class SnapshotAdminTests(TestCase):
    databases = "__all__"  # the list checks every project's schema

    def setUp(self):
        self.root = User.objects.create_superuser("root", password="x")
        self.client.force_login(self.root)
        MortalityBill.objects.create(year=1665, type="Plague", count=10)

    def test_list_shows_schema_state(self):
        current, _ = projectdata.take_snapshot("bom", kind="manual", label="today")
        older, _ = projectdata.take_snapshot("bom", kind="manual")
        DataSnapshot.objects.filter(pk=older.pk).update(migrations=["0000_gone"])
        html = self.client.get(ADMIN).content.decode()
        self.assertIn("today", html)
        self.assertIn("older (not restorable)", html)
        self.assertIn(f"{ADMIN}take/", html)

    def test_take_a_snapshot(self):
        self.assertContains(self.client.get(f"{ADMIN}take/"), 'name="project"')
        response = self.client.post(f"{ADMIN}take/", {"project": "bom", "label": "before experiment"})
        snapshot = DataSnapshot.objects.get()
        self.assertRedirects(response, f"{ADMIN}{snapshot.pk}/change/")
        self.assertEqual((snapshot.kind, snapshot.label, snapshot.created_by), ("manual", "before experiment", self.root))

    def test_restore_compares_then_needs_the_project_name(self):
        snapshot, _ = projectdata.take_snapshot("bom", kind="manual")
        MortalityBill.objects.create(year=1666, type="Fire", count=1)

        page = self.client.get(f"{ADMIN}{snapshot.pk}/restore/").content.decode()
        self.assertIn("<code>mortalitybill</code>", page)
        self.assertRegex(page, r'text-right">1</td><td class="text-right">2</td>')

        wrong = self.client.post(f"{ADMIN}{snapshot.pk}/restore/", {"confirm": "relec"})
        self.assertContains(wrong, 'Type &quot;bom&quot; to confirm.')
        self.assertEqual(MortalityBill.objects.count(), 2)

        response = self.client.post(f"{ADMIN}{snapshot.pk}/restore/", {"confirm": "bom"})
        before = DataSnapshot.objects.get(kind="pre-restore")
        self.assertRedirects(response, f"{ADMIN}{before.pk}/change/")
        self.assertEqual(MortalityBill.objects.count(), 1)
        self.assertEqual(before.row_counts, {"mortalitybill": 2})

    def test_an_older_schema_offers_no_restore(self):
        snapshot, _ = projectdata.take_snapshot("bom", kind="manual")
        DataSnapshot.objects.filter(pk=snapshot.pk).update(migrations=["0000_gone"])
        page = self.client.get(f"{ADMIN}{snapshot.pk}/restore/")
        self.assertContains(page, "Not restorable")
        self.assertNotContains(page, 'name="confirm"')
        self.client.post(f"{ADMIN}{snapshot.pk}/restore/", {"confirm": "bom"})
        self.assertFalse(DataSnapshot.objects.filter(kind="pre-restore").exists())

    def test_download_and_delete_with_files(self):
        snapshot, _ = projectdata.take_snapshot("bom", kind="manual")
        response = self.client.get(f"{ADMIN}{snapshot.pk}/download/")
        self.assertIn(f'filename="bom-snapshot-{snapshot.pk}.json.gz"', response["Content-Disposition"])
        rows = json.loads(gzip.decompress(b"".join(response.streaming_content)))
        self.assertEqual(rows[0]["fields"]["type"], "Plague")

        self.client.post(f"{ADMIN}{snapshot.pk}/delete/", {"post": "yes"})
        self.assertFalse(DataSnapshot.objects.exists())
        self.assertFalse(storages["default"].exists(snapshot.data_path))
        self.assertFalse(storages["default"].exists(snapshot.manifest_path))

    def test_superusers_only(self):
        snapshot, _ = projectdata.take_snapshot("bom", kind="manual")
        staff = User.objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        for path in (ADMIN, f"{ADMIN}take/", f"{ADMIN}{snapshot.pk}/restore/", f"{ADMIN}{snapshot.pk}/download/"):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        self.assertEqual(self.client.post(f"{ADMIN}{snapshot.pk}/restore/", {"confirm": "bom"}).status_code, 403)
        self.assertFalse(DataSnapshot.objects.filter(kind="pre-restore").exists())


class PruneTests(TestCase):
    databases = {"default", "bom_db"}

    def snapshot(self, kind, days_ago):
        snapshot, _ = projectdata.take_snapshot("bom", kind=kind)
        DataSnapshot.objects.filter(pk=snapshot.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return snapshot

    def test_prunes_only_old_automatic_unused_snapshots(self):
        manual = self.snapshot("manual", 90)
        used = self.snapshot("page", 90)
        site = Site.objects.create(project="bom", name="s")
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
