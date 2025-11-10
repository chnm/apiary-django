from .settings import *

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
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


# allauth: this is required for slack
ACCOUNT_DEFAULT_HTTP_PROTOCOL='https'

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
