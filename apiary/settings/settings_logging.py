import os

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

