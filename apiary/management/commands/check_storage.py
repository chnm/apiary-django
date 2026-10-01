import uuid

from django.contrib.auth.models import AnonymousUser
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory
from django.urls import resolve


class _Permitted:
    is_authenticated = True

    def has_module_perms(self, app_label):
        return True


class Command(BaseCommand):
    help = "Write, read, serve and delete a probe file in the public and private media storages"

    def handle(self, *args, **options):
        failures = 0
        for visibility, alias in (("public", "public"), ("private", "default")):
            storage = storages[alias]
            payload = uuid.uuid4().hex.encode()
            name = None
            checks = []
            try:
                name = storage.save(f"apiary/storage-check/{payload.decode()}.txt", ContentFile(payload))
                checks.append((f"wrote {name} ({storage.size(name)} bytes)",
                               storage.exists(name) and storage.size(name) == len(payload)))
                with storage.open(name) as f:
                    checks.append(("read back", f.read() == payload))
                url = storage.url(name)
                checks.append((f"url {url}", url.startswith(f"/media/{visibility}/")))
                match = resolve(url)
                for who, user, expected in (
                    ("anonymous", AnonymousUser(), 200 if visibility == "public" else 302),
                    ("permitted", _Permitted(), 200),
                ):
                    request = RequestFactory().get(url)
                    request.user = user
                    response = match.func(request, *match.args, **match.kwargs)
                    body = b"".join(response.streaming_content) if response.status_code == 200 else None
                    checks.append((f"{who} gets {expected}", response.status_code == expected
                                   and (body is None or body == payload)))
            except Exception as e:  # report every storage, not just the first failure
                checks.append((f"error: {e!r}", False))
            finally:
                if name:
                    storage.delete(name)
                    checks.append(("deleted", not storage.exists(name)))

            for label, ok in checks:
                failures += not ok
                self.stdout.write(f"{visibility:8} {'ok  ' if ok else 'FAIL'} {label}")
        if failures:
            raise CommandError(f"{failures} storage check(s) failed")
        self.stdout.write(self.style.SUCCESS("Public and private storage OK"))
