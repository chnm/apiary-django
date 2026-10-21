import csv
import io
import json
import zipfile
from datetime import date, time
from unittest.mock import MagicMock

from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.test import RequestFactory, TestCase

from apiary.export import write_project_zip
from apiary.views import export_project
from bom.models import MortalityBill
from pinkertons.models import Activity, Location


def export(project):
    buffer = io.BytesIO()
    write_project_zip(project, buffer)
    return zipfile.ZipFile(buffer)


def rows(archive, name):
    return list(csv.reader(io.TextIOWrapper(archive.open(name), encoding="utf-8")))


class ExportTests(TestCase):
    databases = {"default", "bom_db", "pinkertons_db"}

    def test_one_csv_per_table_described_by_a_data_package(self):
        bank = Location.objects.create(location_name="Bank", locality="Manhattan, NYC", latitude=40.7)
        Activity.objects.create(investigation="nyc", date=date(1934, 9, 22), time=time(11, 45),
                                operative="H. McCabe", activity="Surveillance", location=bank)
        Activity.objects.create(investigation="hobart", activity="Shadowing")

        archive = export("pinkertons")
        self.assertEqual(sorted(archive.namelist()), ["activity.csv", "datapackage.json", "location.csv"])

        package = json.loads(archive.read("datapackage.json"))
        self.assertEqual((package["profile"], package["name"], package["title"]),
                         ("tabular-data-package", "apiary-pinkertons", "Pinkertons"))
        activity = next(r for r in package["resources"] if r["name"] == "activity")
        # A tabular-data-package requires it of every resource (Frictionless validate).
        self.assertEqual({r["profile"] for r in package["resources"]}, {"tabular-data-resource"})
        types = {field["name"]: field["type"] for field in activity["schema"]["fields"]}
        self.assertEqual(types, {"id": "integer", "investigation": "string", "date": "date", "time": "time",
                                 "operative": "string", "activity": "string", "mode": "string",
                                 "subject": "string", "notes": "string", "location_id": "integer"})
        self.assertEqual(activity["schema"]["primaryKey"], "id")
        self.assertEqual(activity["schema"]["foreignKeys"],
                         [{"fields": "location_id", "reference": {"resource": "location", "fields": "id"}}])

        header, *data = rows(archive, "activity.csv")
        self.assertEqual(header, list(types))
        self.assertEqual(data[0][1:4] + data[0][-1:], ["nyc", "1934-09-22", "11:45:00", str(bank.pk)])
        self.assertEqual((data[1][2], data[1][-1]), ("", ""))  # no date, no location

        header, *data = rows(archive, "location.csv")
        self.assertEqual(dict(zip(header, data[0]))["latitude"], "40.7")
        self.assertEqual(dict(zip(header, data[0]))["longitude"], "")

    def test_exports_only_that_project(self):
        MortalityBill.objects.create(year=1665, type="Plague", count=10)
        archive = export("bom")
        self.assertEqual(sorted(archive.namelist()), ["datapackage.json", "mortalitybill.csv"])
        self.assertEqual(rows(archive, "mortalitybill.csv"), [["id", "year", "type", "count"], ["1", "1665", "Plague", "10"]])


class ExportViewTests(TestCase):
    databases = {"default", "bom_db"}

    def get(self, user, project="bom"):
        request = RequestFactory().get("/apiary/export/", {"project": project})
        request.user = user
        return export_project(request)

    def test_superusers_download_a_zip(self):
        MortalityBill.objects.create(year=1665, type="Plague", count=10)
        response = self.get(MagicMock(is_active=True, is_superuser=True))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertRegex(response["Content-Disposition"], r'^attachment; filename="bom-\d{8}-\d{6}\.zip"$')
        self.assertEqual(response["Cache-Control"], "private, no-store")
        archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
        self.assertIn("mortalitybill.csv", archive.namelist())

    def test_no_one_else(self):
        self.assertEqual(self.get(AnonymousUser()).status_code, 302)
        staff = MagicMock(is_active=True, is_staff=True, is_superuser=False)
        self.assertEqual(self.get(staff).status_code, 302)

    def test_unknown_project(self):
        with self.assertRaises(Http404):
            self.get(MagicMock(is_active=True, is_superuser=True), project="auth")
