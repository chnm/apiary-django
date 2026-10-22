import gzip
import json
from io import StringIO
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apiary import projectdata
from apiary.models import DataSnapshot
from bom.models import MortalityBill
from pinkertons.models import Activity, Location
from relec.models import Denomination

class SnapshotTests(TestCase):
    databases = {"default", "bom_db", "relec_db", "pinkertons_db"}

    def test_snapshot_restore_and_undo(self):
        bank = Location.objects.create(location_name="Bank", latitude=None)
        Activity.objects.create(investigation="nyc", activity="Surveillance", notes="", location=bank)
        snapshot, _ = projectdata.take_snapshot("pinkertons", kind="manual", label="before edits")

        self.assertEqual(snapshot.row_counts, {"location": 1, "activity": 1})
        self.assertEqual(snapshot.migrations, ["0001_initial"])
        manifest = json.loads(storages["default"].open(snapshot.manifest_path).read())
        self.assertEqual((manifest["project"], manifest["data"]["sha256"]), ("pinkertons", snapshot.sha256))
        with storages["default"].open(snapshot.data_path) as handle:
            rows = json.loads(gzip.decompress(handle.read()))
        self.assertEqual({row["model"] for row in rows}, {"pinkertons.location", "pinkertons.activity"})

        Activity.objects.all().delete()
        Location.objects.create(location_name="Added later")
        before = projectdata.restore_snapshot(snapshot)

        self.assertEqual(list(Location.objects.values_list("location_name", "latitude")), [("Bank", None)])
        activity = Activity.objects.get()
        self.assertEqual((activity.notes, activity.location_id), ("", bank.pk))
        self.assertEqual((before.kind, before.row_counts), ("pre-restore", {"location": 2, "activity": 0}))

        projectdata.restore_snapshot(before)  # the restore is undoable
        self.assertEqual(sorted(Location.objects.values_list("location_name", flat=True)), ["Added later", "Bank"])
        self.assertEqual(Activity.objects.count(), 0)

    def test_consistent_read_runs_alongside(self):
        MortalityBill.objects.create(year=1665, type="Plague", count=10)
        snapshot, total = projectdata.take_snapshot(
            "bom", kind="page", during=lambda alias: MortalityBill.objects.using(alias).count())
        self.assertEqual((total, snapshot.row_counts), (1, {"mortalitybill": 1}))

    def test_refuses_another_schema(self):
        snapshot, _ = projectdata.take_snapshot("bom", kind="manual")
        DataSnapshot.objects.filter(pk=snapshot.pk).update(migrations=["0001_initial", "0002_later"])
        snapshot.refresh_from_db()
        with self.assertRaisesMessage(projectdata.SnapshotError, "then but not now: ['0002_later']"):
            projectdata.restore_snapshot(snapshot)

    def test_refuses_a_changed_file_before_touching_data(self):
        MortalityBill.objects.create(year=1665, type="Plague", count=10)
        snapshot, _ = projectdata.take_snapshot("bom", kind="manual")
        storage = storages["default"]
        storage.delete(snapshot.data_path)
        storage.save(snapshot.data_path, ContentFile(gzip.compress(b"[]")))
        with self.assertRaisesMessage(projectdata.SnapshotError, "does not match its checksum"):
            projectdata.restore_snapshot(snapshot)
        self.assertEqual(MortalityBill.objects.count(), 1)
        self.assertEqual(DataSnapshot.objects.count(), 1)  # no pre-restore snapshot either

    def test_a_failed_load_leaves_the_data(self):
        MortalityBill.objects.create(year=1665, type="Plague", count=10)
        snapshot, _ = projectdata.take_snapshot("bom", kind="manual")
        MortalityBill.objects.create(year=1666, type="Fire", count=1)
        def failing_load(name, *args, **kwargs):
            if name == "loaddata":
                raise RuntimeError("bad fixture")
            return call_command(name, *args, **kwargs)

        with patch("apiary.projectdata.call_command", side_effect=failing_load):
            with self.assertRaisesMessage(RuntimeError, "bad fixture"):
                projectdata.restore_snapshot(snapshot)
        self.assertEqual(MortalityBill.objects.count(), 2)

    def test_commands(self):
        Denomination.objects.create(name="Methodist", family="Methodist")
        out = StringIO()
        call_command("snapshot_project", project="relec", label="x", stdout=out)
        snapshot = DataSnapshot.objects.get()
        self.assertIn(f"Snapshot #{snapshot.pk} of relec", out.getvalue())

        Denomination.objects.all().delete()
        with self.assertRaisesMessage(CommandError, 'type "relec" to confirm'):
            call_command("restore_project_snapshot", snapshot=snapshot.pk, confirm="bom", stdout=StringIO())
        out = StringIO()
        call_command("restore_project_snapshot", snapshot=snapshot.pk, confirm="relec", stdout=out)
        self.assertEqual(Denomination.objects.count(), 1)
        self.assertIn("denomination: 0 -> 1 rows", out.getvalue())

    def test_reset_snapshots_first(self):
        MortalityBill.objects.create(year=1900, type="Mine", count=1)
        out = StringIO()
        call_command("reset_project_data", project="bom", confirm="bom", stdout=out)
        snapshot = DataSnapshot.objects.get(kind="pre-reset")
        self.assertIn(f"Undo with snapshot #{snapshot.pk}", out.getvalue())
        projectdata.restore_snapshot(snapshot)
        self.assertEqual(list(MortalityBill.objects.values_list("type", flat=True)), ["Mine"])
