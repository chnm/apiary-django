"""Live status snapshot for the System Status page (apiary.views.status_*)."""
import os
import platform
import socket
import time
from datetime import datetime, timezone

import django
from django.core.files.storage import storages
from django.db import connections

_STARTED = time.monotonic()


def _timed(check):
    start = time.perf_counter()
    try:
        check()
        error = None
    except Exception as e:  # a status page reports failures; it must not raise them
        error = type(e).__name__
    return {"ok": error is None, "ms": round((time.perf_counter() - start) * 1000, 1), "error": error}


def _select_one(alias):
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT 1")


def snapshot():
    databases = {
        alias: {"vendor": connections[alias].vendor, **_timed(lambda a=alias: _select_one(a))}
        for alias in connections
    }
    # exists() on a fixed missing key is one cheap round trip (a HEAD on S3).
    media = {
        visibility: _timed(lambda s=storages[alias]: s.exists("apiary/status-probe"))
        for visibility, alias in (("public", "public"), ("private", "default"))
    }
    return {
        "ok": all(c["ok"] for c in (*databases.values(), *media.values())),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app": {
            "commit": os.environ.get("SOURCE_SHA", "")[:12] or "dev",
            "python": platform.python_version(),
            "django": django.get_version(),
            "pod": socket.gethostname(),
            "pid": os.getpid(),
            "uptime_s": int(time.monotonic() - _STARTED),
        },
        "databases": databases,
        "media": media,
    }
