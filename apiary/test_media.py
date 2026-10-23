from types import SimpleNamespace
import tempfile
from io import StringIO
from unittest.mock import MagicMock

from django.contrib.auth.models import AnonymousUser
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management import call_command
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, override_settings

from apiary.storage import AppServedS3Storage
from apiary.views import media

_tmp = tempfile.mkdtemp()
STORAGES = {
    alias: {"BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": f"{_tmp}/{visibility}", "base_url": f"/media/{visibility}/"}}
    for alias, visibility in (("default", "private"), ("public", "public"))
}


@override_settings(STORAGES=STORAGES)
class MediaTests(SimpleTestCase):
    def get(self, visibility, path, user):
        request = RequestFactory().get(f"/media/{visibility}/{path}")
        request.user = user
        return media(request, visibility, path)

    def test_check_storage_command_passes(self):
        call_command("check_storage", stdout=StringIO())

    def test_private_files_need_a_role_in_the_files_project(self):
        storages["default"].save("bom/report.txt", ContentFile(b"x"))
        reader = MagicMock(is_authenticated=True, is_active=True, is_superuser=False)
        reader.groups.all.return_value = [SimpleNamespace(name="bom-viewer")]
        self.assertEqual(self.get("private", "bom/report.txt", reader).status_code, 200)
        self.assertEqual(self.get("private", "bom/report.txt", AnonymousUser()).status_code, 302)
        with self.assertRaises(PermissionDenied):
            self.get("private", "relec/report.txt", reader)

    def test_files_are_sandboxed_and_traversal_is_refused(self):
        storages["public"].save("page.html", ContentFile(b"<script>alert(1)</script>"))
        response = self.get("public", "page.html", AnonymousUser())
        self.assertEqual(response["Content-Security-Policy"], "sandbox")
        self.assertEqual(response["Cache-Control"], "public, max-age=3600")
        for path in ("../private/bom/report.txt", "missing.txt"):
            with self.assertRaises(Http404):
                self.get("public", path, AnonymousUser())

    def test_s3_urls_point_at_the_app_not_garage(self):
        storage = AppServedS3Storage(location="public", bucket_name="b", access_key="k",
                                     secret_key="s", endpoint_url="http://obj.rrchnm.internal:3900")
        self.assertEqual(storage.url("bom/a b.pdf"), "/media/public/bom/a b.pdf")

    def test_public_storage_deconstructs_to_the_callable(self):
        from django.db import models

        from apiary.storage import public_storage

        field = models.FileField(upload_to="bom/", storage=public_storage)
        self.assertIs(field.deconstruct()[3]["storage"], public_storage)
        self.assertIs(field.storage, storages["public"])
