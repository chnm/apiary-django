from .settings import *

# Media files: STORAGES["public"] is readable by anyone, STORAGES["default"]
# (private) only by users with a permission in the app named by the file's first
# path segment. Both are served through apiary.views.media at MEDIA_URL; pick
# one per field with FileField(storage=storages["public"]) or the default.
MEDIA_URL = "/media/"

OBJ_STORAGE = env.bool("OBJ_STORAGE", default=False)
if OBJ_STORAGE:
    AWS_ACCESS_KEY_ID = env("OBJ_STORAGE_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env("OBJ_STORAGE_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("OBJ_STORAGE_BUCKET_NAME")
    AWS_S3_ENDPOINT_URL = env("OBJ_STORAGE_ENDPOINT_URL")
    # Garage signs requests with its own region name and serves dotted bucket
    # names only with path-style addressing.
    AWS_S3_REGION_NAME = env("OBJ_STORAGE_REGION", default=None)
    AWS_S3_ADDRESSING_STYLE = "path"

    STORAGES["default"] = {
        "BACKEND": "apiary.storage.AppServedS3Storage",
        "OPTIONS": {"location": "private"},
    }
    STORAGES["public"] = {
        "BACKEND": "apiary.storage.AppServedS3Storage",
        "OPTIONS": {"location": "public"},
    }
else:
    MEDIA_ROOT = os.path.join(BASE_DIR, "mediafiles")
    for alias, visibility in (("default", "private"), ("public", "public")):
        STORAGES[alias] = {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {
                "location": os.path.join(MEDIA_ROOT, visibility),
                "base_url": f"{MEDIA_URL}{visibility}/",
            },
        }
