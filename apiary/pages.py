"""
Static sites on the pages host (settings.PAGES_HOST), at /<project>/<site>/.

Files live in private storage under each version's prefix. Tools write into
the site's draft version; publishing captures its datasets alongside a data
snapshot (apiary.projectdata) from the same moment, then makes the draft the
live version. Published versions never change, so rolling back is pointing
the site at an earlier one.

Pages are untrusted HTML, so they are served only on their own host, never
the app's, and with the ADR 0050 headers for agent-written pages: the CSP
sandbox gives every page an opaque origin, with no cookies and no storage.
"""
import hashlib
import posixpath
import re
import zipfile
from urllib.parse import urlencode

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, HttpResponsePermanentRedirect
from django.test import RequestFactory
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.utils.html import escape

from apiary import projectdata
from apiary.views import PROJECT_APPS

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".json": "application/json",
    ".csv": "text/csv; charset=utf-8", ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2",
}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SITE_BYTES = 20 * 1024 * 1024
MAX_FILES = 200
PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
SITE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
# Scripts and styles may also come from the CDNs the artifacts use, so pages
# can load D3 and friends; everything else stays on the pages host.
CDNS = "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com"


class PagesError(Exception):
    pass


def base_url():
    return settings.PAGES_BASE_URL.rstrip("/")


def site_url(site):
    return f"{base_url()}/{site.project}/{site.name}/"


def get_site(project, name):
    from apiary.models import Site

    try:
        return Site.objects.get(project=project, name=name)
    except Site.DoesNotExist:
        raise PagesError(f"No site {name!r} in {project}; create_site makes one.")


def create_site(project, name, *, title="", user=None):
    from apiary.models import Site

    if project not in PROJECT_APPS:
        raise PagesError(f"Unknown project {project!r}; one of {', '.join(PROJECT_APPS)}.")
    if not SITE_NAME.match(name):
        raise PagesError("Site names are 1-50 lowercase letters, digits and hyphens, starting with a letter or digit.")
    if Site.objects.filter(project=project, name=name).exists():
        raise PagesError(f"{project} already has a site {name!r}.")
    return Site.objects.create(project=project, name=name, title=title, created_by=user)


def write(name, data):
    """Write exactly this storage name: storages rename rather than overwrite."""
    storage = storages["default"]
    if storage.exists(name):
        storage.delete(name)
    saved = storage.save(name, ContentFile(data))
    if saved != name:
        storage.delete(saved)
        raise PagesError(f"Could not write {name}.")


def entry(path, data):
    extension = posixpath.splitext(path)[1].lower()
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "content_type": CONTENT_TYPES[extension]}


def check_path(path):
    if not PATH.match(path) or ".." in path.split("/"):
        raise PagesError(f"Bad path {path!r}: use relative paths of letters, digits, '.', '_', '-' and '/'.")
    extension = posixpath.splitext(path)[1].lower()
    if extension not in CONTENT_TYPES:
        raise PagesError(f"{path!r}: file type not allowed; one of {', '.join(sorted(CONTENT_TYPES))}.")


@transaction.atomic
def draft(site, *, user=None):
    """The site's draft version, started from its newest published version's files."""
    from apiary.models import SiteVersion

    site = type(site).objects.select_for_update().get(pk=site.pk)
    latest = site.versions.order_by("-number").first()
    if latest and latest.published_at is None:
        return latest
    version = SiteVersion.objects.create(
        site=site, number=(latest.number + 1) if latest else 1, created_by=user,
        files=dict(latest.files) if latest else {}, datasets=dict(latest.datasets) if latest else {},
    )
    storage = storages["default"]
    for path in version.files:
        with storage.open(latest.prefix + path, "rb") as source:
            write(version.prefix + path, source.read())
    return version


def put_files(site, files, *, user=None):
    """files: {path: bytes}. Adds or replaces them in the draft."""
    version = draft(site, user=user)
    for path, data in files.items():
        check_path(path)
        if len(data) > MAX_FILE_BYTES:
            raise PagesError(f"{path!r} is {len(data)} bytes; the limit is {MAX_FILE_BYTES}.")
    merged = {**version.files, **{path: entry(path, data) for path, data in files.items()}}
    if len(merged) > MAX_FILES:
        raise PagesError(f"A site holds at most {MAX_FILES} files.")
    if sum(item["size"] for item in merged.values()) > MAX_SITE_BYTES:
        raise PagesError(f"A site holds at most {MAX_SITE_BYTES} bytes.")
    for path, data in files.items():
        write(version.prefix + path, data)
    version.files = merged
    version.save(update_fields=["files"])
    return version


def delete_files(site, paths, *, user=None):
    version = draft(site, user=user)
    missing = [path for path in paths if path not in version.files and path not in version.datasets]
    if missing:
        raise PagesError(f"Not in the draft: {', '.join(missing)}.")
    for path in paths:
        version.files.pop(path, None)
        version.datasets.pop(path, None)
        storages["default"].delete(version.prefix + path)
    version.save(update_fields=["files", "datasets"])
    return version



def upload_zip(site, source, *, user=None):
    """
    Add a zip's files to the draft, with put_files' checks. A single top-level
    folder (as zipping a folder makes) is stripped; OS metadata is skipped.
    """
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile:
        raise PagesError("Not a zip file.")
    names = [n for n in archive.namelist()
             if not n.endswith("/") and not n.startswith("__MACOSX/") and posixpath.basename(n) != ".DS_Store"]
    if not names:
        raise PagesError("The zip holds no files.")
    roots = {n.split("/", 1)[0] for n in names}
    strip = len(roots) == 1 and all("/" in n for n in names)
    files = {}
    for name in names:
        info = archive.getinfo(name)
        if info.file_size > MAX_FILE_BYTES:
            raise PagesError(f"{name!r} is {info.file_size} bytes; the limit is {MAX_FILE_BYTES}.")
        files[name.split("/", 1)[1] if strip else name] = archive.read(info)
    return put_files(site, files, user=user)


def delete_site(site):
    """Delete the site, its versions and all their files."""
    storage = storages["default"]
    for version in site.versions.all():
        for path in version.files:
            if storage.exists(version.prefix + path):
                storage.delete(version.prefix + path)
    site.delete()

def dataset_url(project, dataset, params=None):
    url = f"/api/{project}/v1/datasets/{dataset}/"
    try:
        match = resolve(url)
    except Resolver404:
        match = None
    if match is None or match.namespace != "datasets":
        raise PagesError(f"No dataset {dataset!r} in {project}; list_datasets shows them.")
    return f"{url}?{urlencode(params)}" if params else url


def attach_dataset(site, dataset, *, params=None, path=None, user=None):
    """Capture a dataset into the site as JSON each time it is published."""
    url = dataset_url(site.project, dataset, params)
    path = path or f"data/{dataset}.json"
    check_path(path)
    if not path.endswith(".json"):
        raise PagesError("Dataset files are JSON: give the path a .json name.")
    version = draft(site, user=user)
    version.datasets[path] = url
    version.files.pop(path, None)
    version.save(update_fields=["files", "datasets"])
    return version


def read_dataset(url, user):
    """
    Run the dataset's API view with the publisher's own permissions, so a page
    holds only what its publisher could read. In-process, inside the caller's
    consistent read.
    """
    request = RequestFactory().get(url)
    request.user = user
    match = resolve(request.path)
    response = match.func(request, *match.args, **match.kwargs)
    if response.status_code != 200:
        raise PagesError(f"{url} answered {response.status_code}: {response.content.decode()[:200]}")
    return response.content


def publish(site, *, user):
    version = draft(site, user=user)
    if "index.html" not in version.files:
        raise PagesError("A site needs an index.html to publish.")
    snapshot = None
    if version.datasets:
        def capture(alias):
            return {path: read_dataset(url, user) for path, url in version.datasets.items()}

        snapshot, captured = projectdata.take_snapshot(
            site.project, kind="page", user=user, label=f"{site} v{version.number}", during=capture)
        for path, data in captured.items():
            write(version.prefix + path, data)
            version.files[path] = entry(path, data)
    with transaction.atomic():
        version.published_at = timezone.now()
        version.data_snapshot = snapshot
        version.save(update_fields=["files", "published_at", "data_snapshot"])
        site.live_version = version
        site.save(update_fields=["live_version"])
    return version


def rollback(site, number):
    version = site.versions.filter(number=number, published_at__isnull=False).first()
    if version is None:
        raise PagesError(f"{site} has no published version {number}.")
    site.live_version = version
    site.save(update_fields=["live_version"])
    return version


def unpublish(site):
    site.live_version = None
    site.save(update_fields=["live_version"])


def describe(site):
    live = site.live_version
    return {
        "project": site.project, "name": site.name, "title": site.title,
        "url": site_url(site), "live_version": live.number if live else None,
        "versions": [
            {"number": v.number, "published_at": v.published_at.isoformat() if v.published_at else None,
             "files": sorted(v.files), "datasets": v.datasets,
             "data_snapshot": v.data_snapshot_id}
            for v in site.versions.all()
        ],
    }


# Serving, on the pages host only.

def headers(request, response):
    origin = f"{request.scheme}://{request.get_host()}"
    response["Content-Security-Policy"] = (
        f"default-src 'none'; script-src {origin} 'unsafe-inline' {CDNS}; "
        f"style-src {origin} 'unsafe-inline' {CDNS}; img-src {origin} data: blob:; "
        f"font-src {origin} {CDNS}; connect-src {origin}; media-src {origin}; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
        "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-downloads"
    )
    # The sandbox makes the page's own origin opaque: fetching its data files
    # is then cross-origin.
    response["Access-Control-Allow-Origin"] = "*"
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    response["Cross-Origin-Opener-Policy"] = "same-origin"
    response["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "serial=(), bluetooth=(), hid=(), display-capture=()"
    )
    response["Cache-Control"] = "no-cache"
    return response


def index(request):
    from apiary.models import Site

    items = "".join(
        f'<li><a href="{escape(site.project)}/{escape(site.name)}/">{escape(site.title or site.name)}</a>'
        f" ({escape(site.project)})</li>"
        for site in Site.objects.filter(live_version__isnull=False).order_by("project", "name")
    )
    html = f"<!DOCTYPE html><html lang=en><title>Apiary pages</title><h1>Apiary pages</h1><ul>{items}</ul></html>"
    return headers(request, HttpResponse(html))


def serve(request, project, site, path=""):
    from apiary.models import Site

    found = Site.objects.filter(project=project, name=site).select_related("live_version").first()
    if found is None or found.live_version is None:
        raise Http404
    if path == "" or path.endswith("/"):
        path += "index.html"
    version = found.live_version
    item = version.files.get(path)
    if item is None:
        raise Http404
    response = FileResponse(storages["default"].open(version.prefix + path, "rb"), content_type=item["content_type"])
    return headers(request, response)


def redirect_to_site(request, project, site):
    return HttpResponsePermanentRedirect(f"/{project}/{site}/")


class PagesHostMiddleware:
    """Route the pages host to the pages URLs, and nothing else."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.PAGES_HOST and request.get_host().split(":")[0] == settings.PAGES_HOST:
            request.urlconf = "apiary.pages_urls"
        return self.get_response(request)
