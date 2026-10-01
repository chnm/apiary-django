from django.conf import settings
from django.core.files.storage import storages
from storages.backends.s3 import S3Storage
from storages.utils import clean_name


class AppServedS3Storage(S3Storage):
    """S3 storage whose URLs point at Apiary's /media/ view.

    Garage's S3 endpoint is internal-only, so browsers fetch media through the
    app, which applies the public/private access rules (apiary.views.media).
    """

    def url(self, name, parameters=None, expire=None, http_method=None):
        return f"{settings.MEDIA_URL}{self._normalize_name(clean_name(name))}"


def public_storage():
    """FileField(storage=public_storage): a callable keeps migrations free of storage config."""
    return storages["public"]
