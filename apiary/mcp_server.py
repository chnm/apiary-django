"""
Read-only MCP server for users' AI agents at /mcp, on the official MCP SDK.

It runs under uvicorn beside Gunicorn (a second container in the app pod), so
Django itself stays WSGI:

    uvicorn apiary.mcp_server:app --host 0.0.0.0 --port 8001

Agents authenticate with an APIToken as a bearer token. Every token may read
every project for now.
"""
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
from mcp.server.auth.settings import AuthSettings  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

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
        "Read-only access to the Apiary digital humanities projects' data. "
        "Call list_projects first for each project's models and fields."
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


def transport_security():
    """
    Django's ALLOWED_HOSTS also guards the SDK's DNS-rebinding check. A
    wildcard there can't be expressed in the SDK's patterns; bearer auth
    still protects the endpoint.
    """
    hosts = list(settings.ALLOWED_HOSTS)
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
