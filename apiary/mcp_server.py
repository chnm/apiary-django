"""
Read-only MCP server for users' AI agents at /mcp, on the official MCP SDK.

It runs under uvicorn beside Gunicorn (a second container in the app pod), so
Django itself stays WSGI:

    uvicorn apiary.mcp_server:app --host 0.0.0.0 --port 8001

Agents authenticate with an APIToken as a bearer token. Every token may read
every project for now; the site tools (apiary.sites) need a staff user.
"""
import base64
import binascii
import json
import os
from typing import Any, Literal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apiary.settings")
django.setup()

from asgiref.sync import sync_to_async  # noqa: E402
from django.apps import apps  # noqa: E402
from django.conf import settings  # noqa: E402
from django.core.exceptions import FieldDoesNotExist, FieldError, ValidationError  # noqa: E402
from django.core.serializers.json import DjangoJSONEncoder  # noqa: E402
from django.db import connections, router  # noqa: E402
from django.db.models import Avg, Count, Max, Min, Sum  # noqa: E402
from mcp.server import MCPServer  # noqa: E402
from mcp.server.auth.provider import AccessToken  # noqa: E402
from mcp.server.auth.middleware.auth_context import get_access_token  # noqa: E402
from mcp.server.auth.settings import AuthSettings  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

from apiary import sites  # noqa: E402
from apiary.api.datasets import api as datasets_api  # noqa: E402
from apiary.models import APIToken  # noqa: E402
from apiary.views import PROJECT_APPS  # noqa: E402

MAX_LIMIT = 500
LOOKUPS = {
    "exact", "iexact", "contains", "icontains", "in", "gt", "gte", "lt", "lte",
    "startswith", "istartswith", "endswith", "iendswith", "range", "isnull",
    "year", "month", "day",
}
METRICS = {"count": Count, "sum": Sum, "avg": Avg, "min": Min, "max": Max}


def orm(fn):
    """
    Run fn on asgiref's single sync thread, then close its connections.
    Outside Django's request cycle nothing else closes them, and one thread
    keeps this process to one connection per database.
    """
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            for connection in connections.all(initialized_only=True):
                connection.close()
    return sync_to_async(run, thread_sensitive=True)


def jsonable(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


class APITokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        api_token = await orm(APIToken.authenticate)(token)
        if api_token is None:
            return None
        return AccessToken(
            token=token,
            client_id=f"apitoken-{api_token.pk}",
            scopes=[],
            subject=str(api_token.user_id),
        )


def get_model(project, model):
    if project not in PROJECT_APPS:
        raise ToolError(f"Unknown project {project!r}; one of {', '.join(PROJECT_APPS)}.")
    models = {m._meta.model_name: m for m in apps.get_app_config(project).get_models()}
    if model not in models:
        raise ToolError(f"Unknown model {model!r} in {project}; one of {', '.join(models)}.")
    return models[model]


def check_path(model, path, lookups=False):
    """
    Allow only fields of the model's own project, so a filter or grouping
    cannot reach users, tokens or another project.
    """
    parts = path.lstrip("-").split("__")
    current = model
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        try:
            field = current._meta.get_field(part)
        except FieldDoesNotExist:
            if lookups and last and i > 0 and part in LOOKUPS:
                return
            raise ToolError(f"Unknown field {part!r} on {current._meta.model_name}.")
        if field.is_relation:
            if field.related_model._meta.app_label != model._meta.app_label:
                raise ToolError(f"{part!r} leaves the {model._meta.app_label} project.")
            current = field.related_model
        elif not last:
            rest = parts[i + 1:]
            if lookups and len(rest) == 1:
                if rest[0] in LOOKUPS:
                    return
                raise ToolError(f"Unsupported lookup {rest[0]!r} on {part!r}; one of {', '.join(sorted(LOOKUPS))}.")
            raise ToolError(f"{part!r} on {current._meta.model_name} is not a relation.")


def filtered(model, filters):
    for path in filters or {}:
        check_path(model, path, lookups=True)
    return model.objects.filter(**(filters or {}))


def evaluate(fn):
    """
    Report bad filters and values to the agent. The SDK passes only a
    ToolError's message to the client; any other exception reaches it as a
    bare "Error executing tool".
    """
    try:
        return fn()
    except (FieldError, ValidationError, TypeError, ValueError) as error:
        raise ToolError(str(error)) from error


def describe(project):
    config = apps.get_app_config(project)
    models = list(config.get_models())
    return {
        "project": project,
        "name": config.verbose_name,
        "database": connections[router.db_for_read(models[0])].display_name,
        "models": {
            model._meta.model_name: {
                field.name: (
                    f"{type(field).__name__} -> {field.related_model._meta.model_name}"
                    if field.is_relation else type(field).__name__
                ) + (" (nullable)" if field.null else "")
                for field in model._meta.concrete_fields
            }
            for model in models
        },
    }


mcp = MCPServer(
    name="apiary",
    instructions=(
        "Read access to the Apiary digital humanities projects' data: call list_projects first "
        "for each project's models and fields. Staff can also publish static sites, each on its "
        "own host (<name>.sites.rrchnm.org): create_site, put_files (index.html required), attach_dataset to freeze a dataset "
        "into the site as JSON, then publish_site. Sites are versioned: rollback_site restores one."
    ),
    token_verifier=APITokenVerifier(),
    # The SDK only installs its bearer middleware with AuthSettings. Without a
    # resource_server_url it publishes no OAuth metadata, so issuer_url is
    # never used: tokens are issued in the Django admin, not by OAuth.
    auth=AuthSettings(issuer_url="https://localhost", resource_server_url=None),
)


@mcp.tool()
async def list_projects() -> dict:
    """List the projects with their database backend and each model's fields."""
    return await orm(lambda: {"projects": [describe(project) for project in PROJECT_APPS]})()


@mcp.tool()
async def query_records(
    project: str,
    model: str,
    filters: dict[str, Any] | None = None,
    order_by: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    Read a page of records. filters use Django field lookups within the
    project, e.g. {"year__gte": 1665, "type__icontains": "plague"} or
    {"location__locality": "Atlanta"}; order_by takes field names, "-" for
    descending. Returns the total count and up to limit records (max 500).
    """
    def run():
        target = get_model(project, model)
        for path in order_by or []:
            check_path(target, path)
        records = filtered(target, filters).order_by(*(order_by or ["pk"]))
        names = [field.name for field in target._meta.concrete_fields]
        page = slice(max(offset, 0), max(offset, 0) + min(max(limit, 1), MAX_LIMIT))
        return {"count": records.count(), "results": jsonable(list(records.values(*names)[page]))}
    return await orm(lambda: evaluate(run))()


@mcp.tool()
async def get_record(project: str, model: str, id: int) -> dict:
    """Read one record by its id."""
    def run():
        target = get_model(project, model)
        names = [field.name for field in target._meta.concrete_fields]
        record = target.objects.filter(pk=id).values(*names).first()
        if record is None:
            raise ToolError(f"No {model} with id {id} in {project}.")
        return jsonable(record)
    return await orm(lambda: evaluate(run))()


@mcp.tool()
async def aggregate(
    project: str,
    model: str,
    metric: Literal["count", "sum", "avg", "min", "max"] = "count",
    field: str | None = None,
    group_by: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
) -> dict:
    """
    Count records, or sum/average/min/max a field, optionally grouped by
    fields (related fields too, e.g. "location__locality"), largest first.
    filters work as in query_records.
    """
    def run():
        target = get_model(project, model)
        if metric != "count" and not field:
            raise ToolError(f"{metric} needs a field.")
        for path in [*(group_by or []), *([field] if field else [])]:
            check_path(target, path)
        records = filtered(target, filters)
        value = METRICS[metric](field or "pk")
        if not group_by:
            return {"rows": jsonable([records.aggregate(value=value)])}
        rows = records.values(*group_by).annotate(value=value).order_by("-value", *group_by)
        return {"rows": jsonable(list(rows[:min(max(limit, 1), MAX_LIMIT)]))}
    return await orm(lambda: evaluate(run))()


def current_user(staff=False):
    """The token's user. The site tools publish under it, so they need staff."""
    from django.contrib.auth import get_user_model

    token = get_access_token()
    user = get_user_model().objects.filter(pk=token.subject, is_active=True).first() if token else None
    if user is None:
        raise ToolError("No active user for this token.")
    if staff and not user.is_staff:
        raise ToolError("Publishing sites needs a staff account.")
    return user


def site_tool(fn):
    """Run fn(user, ...) for a staff user, reporting site errors to the agent."""
    def run(*args, **kwargs):
        try:
            return fn(current_user(staff=True), *args, **kwargs)
        except sites.SiteError as error:
            raise ToolError(str(error)) from error
    return orm(run)


@mcp.tool()
async def list_datasets(project: str) -> dict:
    """List a project's derived datasets (aggregates, time series), for attach_dataset."""
    def run():
        if project not in PROJECT_APPS:
            raise ToolError(f"Unknown project {project!r}; one of {', '.join(PROJECT_APPS)}.")
        prefix = f"/api/{project}/v1/datasets/"
        schema = datasets_api.get_openapi_schema()
        return {"datasets": [
            {"name": path[len(prefix):].strip("/"), "description": operation.get("description", "").strip(),
             "params": [param["name"] for param in operation.get("parameters", [])]}
            for path, item in schema["paths"].items() if path.startswith(prefix)
            for operation in item.values()
        ]}
    return await orm(run)()


@mcp.tool()
async def list_sites(project: str | None = None) -> dict:
    """List the static sites, with their public URL, live version and versions."""
    def run():
        from apiary.models import Site

        found = Site.objects.order_by("project", "name").prefetch_related("versions")
        if project:
            found = found.filter(project=project)
        return {"sites": [sites.describe(site) for site in found]}
    return await orm(run)()


@mcp.tool()
async def create_site(project: str, name: str, title: str = "") -> dict:
    """
    Create an empty static site for a project, served at <name>.sites.rrchnm.org.
    Names are unique across projects: lowercase letters, digits and inner
    hyphens. Staff only.
    """
    def run(user):
        return sites.describe(sites.create_site(project, name, title=title, user=user))
    return await site_tool(run)()


@mcp.tool()
async def put_files(project: str, site: str, files: list[dict[str, str]]) -> dict:
    """
    Add or replace files in the site's draft. Each file is {"path": "index.html",
    "content": "..."}; add "encoding": "base64" for images and fonts. Paths are
    relative (css/site.css). Allowed: html css js json csv txt md svg png jpg gif
    webp ico woff2; 2 MB a file, 20 MB and 200 files a site. Staff only.
    """
    def run(user):
        decoded = {}
        for item in files:
            if "path" not in item or "content" not in item:
                raise sites.SiteError('Each file needs "path" and "content".')
            try:
                decoded[item["path"]] = (base64.b64decode(item["content"], validate=True)
                                         if item.get("encoding") == "base64" else item["content"].encode())
            except binascii.Error:
                raise sites.SiteError(f"{item['path']!r}: content is not valid base64.")
        version = sites.put_files(sites.get_site(project, site), decoded, user=user)
        return {"draft_version": version.number, "files": sorted(version.files)}
    return await site_tool(run)()


@mcp.tool()
async def delete_files(project: str, site: str, paths: list[str]) -> dict:
    """Remove files (or attached datasets) from the site's draft. Staff only."""
    def run(user):
        version = sites.delete_files(sites.get_site(project, site), paths, user=user)
        return {"draft_version": version.number, "files": sorted(version.files), "datasets": version.datasets}
    return await site_tool(run)()


@mcp.tool()
async def attach_dataset(project: str, site: str, dataset: str,
                         params: dict[str, Any] | None = None, path: str | None = None) -> dict:
    """
    Freeze a dataset (list_datasets) into the site as JSON, at path (default
    data/<dataset>.json), captured anew each time the site is published. The
    page fetches it relatively, e.g. fetch("data/deaths-by-year.json"). Staff only.
    """
    def run(user):
        version = sites.attach_dataset(sites.get_site(project, site), dataset, params=params, path=path, user=user)
        return {"draft_version": version.number, "datasets": version.datasets}
    return await site_tool(run)()


@mcp.tool()
async def publish_site(project: str, site: str) -> dict:
    """
    Publish the draft: capture its datasets with a snapshot of the project's data
    from the same moment, then make it the live version. Returns the URL. Staff only.
    """
    def run(user):
        target = sites.get_site(project, site)
        version = sites.publish(target, user=user)
        return {"url": sites.site_url(target), "version": version.number,
                "data_snapshot": version.data_snapshot_id, "files": sorted(version.files)}
    return await site_tool(run)()


@mcp.tool()
async def rollback_site(project: str, site: str, version: int) -> dict:
    """Make an earlier published version live again. Staff only."""
    def run(user):
        target = sites.get_site(project, site)
        sites.rollback(target, version)
        return {"url": sites.site_url(target), "live_version": version}
    return await site_tool(run)()


@mcp.tool()
async def unpublish_site(project: str, site: str) -> dict:
    """Take the site offline; its versions are kept. Staff only."""
    def run(user):
        target = sites.get_site(project, site)
        sites.unpublish(target)
        return {"url": sites.site_url(target), "live_version": None}
    return await site_tool(run)()


def transport_security():
    """
    Django's ALLOWED_HOSTS also guards the SDK's DNS-rebinding check. A
    wildcard there can't be expressed in the SDK's patterns; bearer auth
    still protects the endpoint.
    """
    # The site hosts never serve /mcp: leave them out, or their wildcard would
    # switch the check off.
    hosts = [host for host in settings.ALLOWED_HOSTS if host != f".{settings.SITES_DOMAIN}"]
    if not hosts or any(host == "*" or host.startswith(".") for host in hosts):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[pattern for host in hosts for pattern in (host, f"{host}:*")],
        allowed_origins=[f"https://{host}" for host in hosts],
    )


# Stateless JSON: every request stands alone, so revoking a token takes effect
# on the next call and no session state outlives a request.
app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=transport_security(),
)
