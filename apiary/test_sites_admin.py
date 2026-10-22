import gzip
import io
import json
import zipfile

from django.contrib.auth.models import User
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apiary import projectdata
from apiary.models import DataSnapshot, Site
from bom.models import MortalityBill

SITES = "/admin/apiary/site/"
SNAPSHOTS = "/admin/apiary/datasnapshot/"
SITES_SETTINGS = {"SITES_DOMAIN": "sites.test", "SITES_URL": "https://{name}.sites.test/",
         "ALLOWED_HOSTS": ["testserver", ".sites.test"]}


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return SimpleUploadedFile("site.zip", buffer.getvalue(), content_type="application/zip")


@override_settings(**SITES_SETTINGS)
class SiteAdminTests(TestCase):
    databases = "__all__"

    def setUp(self):
        self.root = User.objects.create_superuser("root", password="x")
        self.client.force_login(self.root)

    def create(self):
        response = self.client.post(f"{SITES}add/", {"project": "bom", "name": "plague", "title": "Plague"})
        return response, Site.objects.filter(name="plague").first()

    def test_create_upload_publish_rollback_unpublish_delete(self):
        response, site = self.create()
        self.assertEqual((site.project, site.created_by), ("bom", self.root))
        self.assertContains(self.client.post(f"{SITES}add/", {"project": "bom", "name": "Bad Name"}), "Site names are")
        # The same checks as MCP: unique across projects, with the owner named.
        self.assertContains(self.client.post(f"{SITES}add/", {"project": "relec", "name": "plague"}),
                            "taken (by bom/plague)")

        upload = f"{SITES}{site.pk}/upload/"
        self.client.post(upload, {"archive": zipped({
            "mysite/index.html": "one", "mysite/css/a.css": "a", "__MACOSX/mysite/._index.html": "x",
        })})
        draft = site.versions.get()
        self.assertEqual(sorted(draft.files), ["css/a.css", "index.html"])  # folder stripped, metadata skipped
        bad = self.client.post(upload, {"archive": zipped({"run.exe": "x"})})
        self.assertContains(bad, "file type not allowed")

        publish = f"{SITES}{site.pk}/publish/"
        self.assertContains(self.client.get(publish), "Publishes draft v1 (2 files)")
        site.refresh_from_db()
        self.assertIsNone(site.live_version)  # GET only confirms
        self.client.post(publish)
        site.refresh_from_db()
        self.assertEqual(site.live_version.number, 1)

        self.client.post(upload, {"archive": zipped({"index.html": "two"})})
        self.client.post(publish)
        page = self.client.get(f"{SITES}{site.pk}/change/").content.decode()
        self.assertIn("live", page)
        self.assertIn("index.html (3 B)", page)

        self.client.post(f"{SITES}{site.pk}/rollback/", {"version": 1})
        site.refresh_from_db()
        self.assertEqual(site.live_version.number, 1)

        self.assertContains(self.client.get(f"{SITES}{site.pk}/unpublish/"), "Takes https://plague.sites.test/")
        self.client.post(f"{SITES}{site.pk}/unpublish/")
        site.refresh_from_db()
        self.assertIsNone(site.live_version)

        names = [v.prefix + path for v in site.versions.all() for path in v.files]
        self.client.post(f"{SITES}{site.pk}/delete/", {"post": "yes"})
        self.assertFalse(Site.objects.exists())
        self.assertFalse(any(storages["default"].exists(name) for name in names))

    def test_per_project_list_and_superusers_only(self):
        _, site = self.create()
        Site.objects.create(project="relec", name="other")  # bypasses the checks; only the list is tested
        listing = self.client.get(f"{SITES}?project__exact=bom").content.decode()
        self.assertIn("bom/plague", listing)
        self.assertNotIn("relec/other", listing)

        staff = User.objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        for path in (SITES, f"{SITES}add/", f"{SITES}{site.pk}/upload/", f"{SITES}{site.pk}/publish/",
                     f"{SITES}{site.pk}/rollback/", f"{SITES}{site.pk}/unpublish/"):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        self.assertEqual(self.client.post(f"{SITES}{site.pk}/publish/").status_code, 403)


class SnapshotBundleTests(TestCase):
    databases = "__all__"

    def setUp(self):
        self.root = User.objects.create_superuser("root", password="x")
        self.client.force_login(self.root)
        MortalityBill.objects.create(year=1665, type="Plague", count=10)
        self.snapshot, _ = projectdata.take_snapshot("bom", kind="manual", label="original")

    def export(self):
        response = self.client.get(f"{SNAPSHOTS}{self.snapshot.pk}/export/")
        self.assertIn(f'filename="bom-snapshot-{self.snapshot.pk}.zip"', response["Content-Disposition"])
        return b"".join(response.streaming_content)

    def upload(self, data):
        return self.client.post(f"{SNAPSHOTS}import/", {"bundle": SimpleUploadedFile("b.zip", data)})

    def rebundle(self, data, *, records=None, manifest=None):
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            current = json.loads(bundle.read("manifest.json"))
            payload = bundle.read("data.json.gz")
        if records is not None:
            payload = gzip.compress(json.dumps(records).encode())
            current["data"]["sha256"] = __import__("hashlib").sha256(payload).hexdigest()
        current.update(manifest or {})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("manifest.json", json.dumps(current))
            bundle.writestr("data.json.gz", payload)
        return buffer.getvalue()

    def test_export_import_and_restore(self):
        bundle = self.export()
        response = self.upload(bundle)
        imported = DataSnapshot.objects.get(kind="imported")
        self.assertRedirects(response, f"{SNAPSHOTS}{imported.pk}/change/")
        self.assertEqual((imported.row_counts, imported.sha256), (self.snapshot.row_counts, self.snapshot.sha256))
        self.assertIn(f"Imported #{self.snapshot.pk}", imported.label)

        MortalityBill.objects.all().delete()
        projectdata.restore_snapshot(imported)
        self.assertEqual(list(MortalityBill.objects.values_list("type", flat=True)), ["Plague"])

    def test_import_refuses_bad_bundles(self):
        bundle = self.export()
        foreign = [{"model": "auth.user", "pk": 99, "fields": {"username": "intruder", "is_superuser": True}}]
        for data, message in [
            (b"not a zip", "Not a snapshot bundle"),
            (self.rebundle(bundle, manifest={"project": "auth"}), "Unknown project"),
            (self.rebundle(bundle, manifest={"data": {"sha256": "0" * 64}}), "checksum"),
            (self.rebundle(bundle, records=foreign), "records outside bom: auth.user"),
            (self.rebundle(bundle, records=[{"model": "relec.denomination", "pk": 1, "fields": {}}]),
             "records outside bom"),
        ]:
            self.assertContains(self.upload(data), message)
        self.assertFalse(DataSnapshot.objects.filter(kind="imported").exists())
        self.assertFalse(User.objects.filter(username="intruder").exists())

    def test_restore_rechecks_records(self):
        MortalityBill.objects.create(year=1666, type="Fire", count=1)
        storage = storages["default"]
        payload = gzip.compress(json.dumps([{"model": "auth.user", "pk": 99, "fields": {}}]).encode())
        storage.delete(self.snapshot.data_path)
        storage.save(self.snapshot.data_path, io.BytesIO(payload))
        DataSnapshot.objects.filter(pk=self.snapshot.pk).update(sha256=__import__("hashlib").sha256(payload).hexdigest())
        self.snapshot.refresh_from_db()
        with self.assertRaisesMessage(projectdata.SnapshotError, "records outside bom"):
            projectdata.restore_snapshot(self.snapshot)
        self.assertEqual(MortalityBill.objects.count(), 2)
