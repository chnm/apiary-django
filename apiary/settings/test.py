from . import *

# The hermetic image build has no PostgreSQL; the live deployment exercises it.
DATABASES = {
    alias: {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    for alias in DATABASES
}

# The test client speaks plain HTTP; redirect-to-TLS is the edge's contract.
SECURE_SSL_REDIRECT = False

# SQLite stands in for PostgreSQL here, so its lack of table comments is expected.
SILENCED_SYSTEM_CHECKS = [*SILENCED_SYSTEM_CHECKS, "models.W046"]

# Tests never run collectstatic, so the manifest storage has nothing to read
# when a test renders the admin.
STORAGES = {**STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}

# Snapshots and sites write files: keep them out of the checkout.
import tempfile as _tempfile

_media = _tempfile.mkdtemp(prefix="apiary-test-media-")
for _alias, _visibility in (("default", "private"), ("public", "public")):
    STORAGES[_alias] = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": f"{_media}/{_visibility}", "base_url": f"/media/{_visibility}/"},
    }
