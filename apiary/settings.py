import os

import environ
from pathlib import Path

env = environ.Env()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="django-insecure-j0ooyy9thm4g#k%9%16egn(y(un=e)h^6j7xy5*9plt+=pzao3"
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG", default=False)

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INTERNAL_IPS = ["127.0.0.1"]

# show debug toolbar callback for docker doesn't work, so this hack works
if DEBUG:
    INTERNAL_IPS = type("c", (), {"__contains__": lambda *a: True})()

# Application definition

INSTALLED_APPS = [
    # unfold
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "unfold.contrib.import_export",
    # defaults
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # all-auth
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.orcid',
    'allauth.socialaccount.providers.github',
    'allauth.socialaccount.providers.slack',
    # debug toolbar
    "debug_toolbar",
    # rest framework
    'rest_framework',
    'drf_spectacular',
    # obj storage
    'storages',

    # apps
    'bom',
    'connthreads',
    'mappingviolence',
    'relec'
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# allauth: provider specific settings
SOCIALACCOUNT_PROVIDERS = {
    'orcid': {
        'BASE_DOMAIN':'orcid.org',
        'MEMBER_API': False,
        "APP": {
            "client_id": env("ALLAUTH_ORCID_CLIENT_ID", default="PLACEHOLDER"),
            "secret": env("ALLAUTH_ORCID_CLIENT_SECRET", default="PLACEHOLDER"),
        },
    },
    'github': {
        "VERIFIED_EMAIL": True,
        "APP": {
            "client_id": env("ALLAUTH_GITHUB_CLIENT_ID", default="PLACEHOLDER"),
            "secret": env("ALLAUTH_GITHUB_CLIENT_SECRET", default="PLACEHOLDER"),
        },
    },
    'slack': {
        'VERIFIED_EMAIL': True,
        'APP': {
            "client_id": env("ALLAUTH_SLACK_CLIENT_ID", default="PLACEHOLDER"),
            "secret": env("ALLAUTH_SLACK_CLIENT_SECRET", default="PLACEHOLDER"),
            "key": "",
            "settings": {
                "scope": [
                    "openid",
                    "profile",
                    "email"
                ]
            }
        }
    }
}

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

UNFOLD = {
    "SITE_TITLE": "Apiary",
    "SITE_HEADER": "Apiary",
    "SITE_SYMBOL": "speed",
}

ROOT_URLCONF = "apiary.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "apiary.wsgi.application"


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
    "bom_db": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        "NAME": env("DB_NAME", default="apiary-django"),
        "USER": env("DB_USER", default="apiary-django"),
        "PASSWORD": env("DB_PASS", default="password"),
        "OPTIONS": {
            "options": "-c search_path=bom"
        },
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
DATABASE_ROUTERS = [
    'bom.routers.db.DatabaseRouter',

    'apiary.routers.db.AdminRouter',
    'apiary.routers.db.DefaultRouter',
]


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = "static/"

# Storage backend
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Media files
OBJ_STORAGE = env("OBJ_STORAGE", default=False)
if OBJ_STORAGE:
    AWS_ACCESS_KEY_ID = env("OBJ_STORAGE_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env("OBJ_STORAGE_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("OBJ_STORAGE_BUCKET_NAME")
    AWS_S3_ENDPOINT_URL = env("OBJ_STORAGE_ENDPOINT_URL")

    MEDIA_URL = f"{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/"

    # override default storage backend for media
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
    }
else:
    MEDIA_URL = "media/"
    MEDIA_ROOT = os.path.join(BASE_DIR, "mediafiles")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            #"format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "format": "{asctime} {levelname} {process:d} [{className}.{funcName}] {message}",
            "style": "{",
            'defaults': {
                'className': '',
                'funcName': '',
            }
        },
        "simple": {
            "format": "{asctime} {levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console_simple": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
        "console_verbose": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console_simple"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console_simple"],
            "level": "INFO",
            "propagate": False,
        },
        "apiary.models": {
            "handlers": ["console_verbose"],
            "level": os.getenv("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
