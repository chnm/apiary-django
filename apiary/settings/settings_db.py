from .settings import *

# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        "NAME": env("DB_NAME", default="apiary-django"),
        "USER": env("DB_USER", default="apiary-django"),
        "PASSWORD": env("DB_PASS", default="password"),
        "OPTIONS": {
            "options": "-c search_path=public"
        },
    },
#   "bom_db": {
#       "ENGINE": "django.db.backends.postgresql",
#       "HOST": env("DB_HOST", default="localhost"),
#       "PORT": env("DB_PORT", default="5432"),
#       "NAME": env("DB_NAME", default="apiary-django"),
#       "USER": env("DB_USER", default="apiary-django"),
#       "PASSWORD": env("DB_PASS", default="password"),
#       "OPTIONS": {
#           "options": "-c search_path=bom"
#       },
#   },
    "bom_db": {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': env('BOM_DB_PATH', default=str(BASE_DIR / 'bom.sqlite3')),
    },
    "connthreads_db": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        "NAME": env("DB_NAME", default="apiary-django"),
        "USER": env("DB_USER", default="apiary-django"),
        "PASSWORD": env("DB_PASS", default="password"),
        "OPTIONS": {
            "options": "-c search_path=connthreads"
        },
    },
    "mappingviolence_db": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        "NAME": env("DB_NAME", default="apiary-django"),
        "USER": env("DB_USER", default="apiary-django"),
        "PASSWORD": env("DB_PASS", default="password"),
        "OPTIONS": {
            "options": "-c search_path=mappingviolence"
        },
    },
    "relec_db": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        "NAME": env("DB_NAME", default="apiary-django"),
        "USER": env("DB_USER", default="apiary-django"),
        "PASSWORD": env("DB_PASS", default="password"),
        "OPTIONS": {
            "options": "-c search_path=relec"
        },
    },
}
# The migrate Job sets this so a migration blocked behind the nightly pg_dump
# fails and retries instead of holding the app's queries behind its lock.
if DB_LOCK_TIMEOUT := env("DB_LOCK_TIMEOUT", default=""):
    for _db in DATABASES.values():
        if _db["ENGINE"] == "django.db.backends.postgresql":
            _db["OPTIONS"]["options"] += f" -c lock_timeout={DB_LOCK_TIMEOUT}"

DATABASE_ROUTERS = [
    'apiary.routers.db.BomRouter',
    'apiary.routers.db.ConnThreadsRouter',
    'apiary.routers.db.MappingViolenceRouter',
    'apiary.routers.db.RelecRouter',

    'apiary.routers.db.AdminRouter',
    'apiary.routers.db.DefaultRouter',
]
