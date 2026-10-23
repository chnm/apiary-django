"""
MCP server for users' AI agents at /mcp, on the official MCP SDK.

It runs under uvicorn beside Gunicorn (a second container in the app pod), so
Django itself stays WSGI:

    uvicorn apiary.mcp_server:app --host 0.0.0.0 --port 8001

Agents authenticate with a bearer token: an OAuth access token, which an MCP
client gets with its user's approval (this app is the authorization server,
apiary.mcp_oauth), or an APIToken from the admin. A token's scopes say what
it may do: mcp:read reads every project, mcp:write changes records (as the
user's project role allows, apiary.changes) and publishes sites (staff),
mcp:schema changes live tables (apiary.live).
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
from django.core.exceptions import FieldError, ValidationError  # noqa: E402
from django.core.serializers.json import DjangoJSONEncoder  # noqa: E402
from django.db import connections  # noqa: E402
from django.db.models import Avg, Count, Max, Min, Sum  # noqa: E402
from mcp.server import MCPServer  # noqa: E402
from mcp.server.auth.middleware.auth_context import get_access_token  # noqa: E402
from mcp.server.auth.provider import (  # noqa: E402
    AccessToken,
    AuthorizationCode,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402
from mcp.shared.auth import OAuthToken as TokenResponse  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from apiary import changes, mcp_oauth, queries, sites  # noqa: E402
from apiary.live.runtime import live_specs  # noqa: E402
from apiary.models import APIToken, Change, OAuthClient  # noqa: E402
from apiary.projects import (  # noqa: E402
    PROJECT_APPS,
    UnknownProject,
    code_models,
    project_alias,
    project_models,
    role,
)

MAX_LIMIT = 500
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


class OAuthProvider:
    """
    The SDK's authorization server hooks, over apiary.mcp_oauth. The SDK
    validates each request (client, redirect URI, PKCE); these load and store
    its state, and send /authorize on to Django's consent page.
    """

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = await orm(mcp_oauth.get_client)(client_id)
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        try:
            await orm(mcp_oauth.register)(client_info.model_dump(mode="json", exclude_none=True))
        except mcp_oauth.OAuthError as error:
            raise RegistrationError(error.code, error.description) from error

    async def authorize(self, client: OAuthClientInformationFull, params) -> str:
        if params.resource and params.resource.rstrip("/") != mcp_oauth.resource_url():
            raise AuthorizeError("invalid_target", f"This server issues tokens only for {mcp_oauth.resource_url()}.")
        return mcp_oauth.consent_url({
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "code_challenge": params.code_challenge,
            "scopes": params.scopes or [],
            "state": params.state,
        })

    async def load_authorization_code(self, client, authorization_code: str) -> AuthorizationCode | None:
        grant = await orm(mcp_oauth.load_grant)(client.client_id, authorization_code)
        if grant is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            client_id=client.client_id,
            expires_at=grant.expires_at.timestamp(),
            resource=mcp_oauth.resource_url(),
            subject=str(grant.user_id),
            **grant.request,
        )

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode) -> TokenResponse:
        return await self._issue(mcp_oauth.exchange_grant, client.client_id, authorization_code.code)

    async def load_refresh_token(self, client, refresh_token: str) -> RefreshToken | None:
        token = await orm(mcp_oauth.load_refresh)(client.client_id, refresh_token)
        if token is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=token.scopes,
            expires_at=int(token.refresh_expires_at.timestamp()),
            resource=mcp_oauth.resource_url(),
            subject=str(token.user_id),
        )

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> TokenResponse:
        return await self._issue(mcp_oauth.rotate, client.client_id, refresh_token.token)

    async def load_access_token(self, token: str) -> AccessToken | None:
        def find():
            connection = mcp_oauth.authenticate(token)
            if connection is not None:
                return AccessToken(
                    token=token,
                    client_id=connection.client.client_id,
                    scopes=connection.scopes,
                    expires_at=int(connection.access_expires_at.timestamp()),
                    resource=mcp_oauth.resource_url(),
                    subject=str(connection.user_id),
                )
            api_token = APIToken.authenticate(token)
            if api_token is not None:
                return AccessToken(
                    token=token,
                    client_id=f"apitoken-{api_token.pk}",
                    scopes=api_token.scopes,
                    resource=mcp_oauth.resource_url(),
                    subject=str(api_token.user_id),
                )
            return None
        return await orm(find)()

    async def revoke_token(self, token) -> None:
        await orm(mcp_oauth.revoke)(token.token)

    @staticmethod
    async def _issue(exchange, client_id, raw) -> TokenResponse:
        try:
            connection, access, refresh = await orm(exchange)(client_id, raw)
        except mcp_oauth.OAuthError as error:
            raise TokenError(error.code, error.description) from error
        return TokenResponse(
            access_token=access,
            token_type="Bearer",
            expires_in=int(connection.ACCESS_LIFETIME.total_seconds()),
            refresh_token=refresh,
            scope=" ".join(connection.scopes) or None,
        )


def get_model(project, table):
    try:
        models = project_models(project)
    except UnknownProject as error:
        raise ToolError(str(error))
    if table not in models:
        raise ToolError(f"No table {table!r} in {project}; one of {', '.join(models) or 'none yet'}.")
    return models[table]


def check_path(model, path, lookups=False):
    try:
        queries.check_path(model, path, lookups=lookups)
    except queries.QueryError as error:
        raise ToolError(str(error))


def filtered(model, filters):
    for path in filters or {}:
        check_path(model, path, lookups=True)
    return model.objects.using(project_alias(model._meta.app_label)).filter(**(filters or {}))


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
    except changes.ChangeError as error:
        raise ToolError(str(error)) from error


def column_type(field):
    if field.is_relation:
        return f"relation -> {field.related_model._meta.model_name}"
    return type(field).__name__


def describe(project, user):
    alias = project_alias(project)
    code = code_models(project)
    live = {table["name"]: table for table in live_specs(project)}
    tables = {}
    for name, model in project_models(project).items():
        if name in live:
            spec = live[name]
            tables[name] = {"kind": "live", "label": spec["label"], "description": spec["description"],
                            "columns": {c["name"]: {key: c[key] for key in ("type", "required", "unique", "max_length", "target", "default", "label")
                                                    if c[key] not in (None, "", False)} for c in spec["columns"]}}
        else:
            tables[name] = {"kind": "code", "columns": {
                field.name: column_type(field) + (" (nullable)" if field.null else "")
                for field in model._meta.concrete_fields}}
    return {
        "project": project,
        "name": str(apps.get_app_config(project).verbose_name),
        "database": connections[alias].display_name,
        "your_role": role(user, project),
        "tables": tables,
        "code_tables_note": "Code tables (demo's) change only through the codebase." if code else None,
    }


mcp = MCPServer(
    name="apiary",
    instructions=(
        "Apiary holds digital humanities projects, each with its own tables. Call list_projects first: "
        "it lists each project's tables and columns and your role there. Read with query_records, "
        "get_record and aggregate. With the write scope, editors change rows with create_records and "
        "update_records, admins with delete_records; changes over 25 rows come back as a proposal to "
        "show the user and then apply_change. Every change is recorded (list_changes) and records "
        "changes can be undone with revert_change. With the schema scope, project admins change "
        "tables with propose_table_change (create, drop, rename tables; add, drop, rename, alter "
        "columns), always a proposal: show its effects and SQL, then apply_change. Staff can also "
        "publish static sites, each on its own host (<name>.sites.rrchnm.org): create_site, "
        "put_files (index.html required), then publish_site. Sites are versioned: rollback_site restores one."
    ),
    auth_server_provider=OAuthProvider(),
    # The SDK serves the authorization server's metadata, /authorize, /token,
    # /register and /revoke at MCP_URL, and the protected resource metadata
    # for /mcp. Every token is issued for /mcp, so the SDK can refuse others.
    auth=AuthSettings(
        issuer_url=settings.MCP_URL,
        resource_server_url=mcp_oauth.resource_url(),
        validate_token_resource=True,
        # Clients may ask for any scope; the user grants scopes on the consent page.
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=list(mcp_oauth.SCOPES), default_scopes=list(mcp_oauth.SCOPES)),
        revocation_options=RevocationOptions(enabled=True),
    ),
)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)


def current_user(scope="mcp:read", staff=False):
    """The token's user, once the token has the scope."""
    from django.contrib.auth import get_user_model

    token = get_access_token()
    if token is None:
        raise ToolError("No token.")
    if scope not in (token.scopes or []):
        raise ToolError(f"This token lacks the {scope} scope; reconnect and grant it, or ask for a token with it.")
    user = get_user_model().objects.filter(pk=token.subject, is_active=True).first()
    if user is None:
        raise ToolError("No active user for this token.")
    if staff and not user.is_staff:
        raise ToolError("Publishing sites needs a staff account.")
    return user


def via():
    """The client acting, for the change record."""
    token = get_access_token()
    client_id = token.client_id if token else ""
    if client_id.startswith("apitoken-"):
        name = APIToken.objects.filter(pk=client_id.removeprefix("apitoken-")).values_list("name", flat=True).first()
        return f"MCP: API token {name or client_id}"
    name = OAuthClient.objects.filter(client_id=client_id).values_list("name", flat=True).first()
    return f"MCP: {name or client_id or 'unknown client'}"


def tool(scope, fn):
    """Run fn(user, ...) for the token's user with this scope, on the ORM thread."""
    def run(*args, **kwargs):
        user = current_user(scope)
        return evaluate(lambda: fn(user, *args, **kwargs))
    return orm(run)


@mcp.tool(annotations=READ)
async def list_projects() -> dict:
    """List the projects: each table with its columns, the database, and your role there."""
    def run(user):
        return {"projects": [describe(project, user) for project in PROJECT_APPS]}
    return await tool("mcp:read", run)()


@mcp.tool(annotations=READ)
async def query_records(
    project: str,
    table: str,
    filters: dict[str, Any] | None = None,
    order_by: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    Read a page of rows. filters use Django field lookups within the
    project, e.g. {"year__gte": 1665, "type__icontains": "plague"} or
    {"location__locality": "Atlanta"}; order_by takes column names, "-" for
    descending. Returns the total count and up to limit rows (max 500).
    """
    def run(user):
        target = get_model(project, table)
        for path in order_by or []:
            check_path(target, path)
        records = filtered(target, filters).order_by(*(order_by or ["pk"]))
        names = [field.name for field in target._meta.concrete_fields]
        page = slice(max(offset, 0), max(offset, 0) + min(max(limit, 1), MAX_LIMIT))
        return {"count": records.count(), "results": jsonable(list(records.values(*names)[page]))}
    return await tool("mcp:read", run)()


@mcp.tool(annotations=READ)
async def get_record(project: str, table: str, id: int) -> dict:
    """Read one row by its id."""
    def run(user):
        target = get_model(project, table)
        names = [field.name for field in target._meta.concrete_fields]
        record = filtered(target, {}).filter(pk=id).values(*names).first()
        if record is None:
            raise ToolError(f"No row {id} in {project}.{table}.")
        return jsonable(record)
    return await tool("mcp:read", run)()


@mcp.tool(annotations=READ)
async def aggregate(
    project: str,
    table: str,
    metric: Literal["count", "sum", "avg", "min", "max"] = "count",
    field: str | None = None,
    group_by: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
) -> dict:
    """
    Count rows, or sum/average/min/max a column, optionally grouped by
    columns (related ones too, e.g. "location__locality"), largest first.
    filters work as in query_records.
    """
    def run(user):
        target = get_model(project, table)
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
    return await tool("mcp:read", run)()


@mcp.tool(annotations=WRITE)
async def create_records(project: str, table: str, rows: list[dict[str, Any]], reason: str = "") -> dict:
    """
    Add rows (up to 500), each {column: value}; relations take the linked
    row's id. All or none are added. Needs the editor role and the write scope.
    """
    def run(user):
        change = changes.create_records(user, project, table, rows, via=via(), reason=reason)
        return {"change": change.pk, "ids": [r["id"] for r in change.after]}
    return await tool("mcp:write", run)()


@mcp.tool(annotations=WRITE)
async def update_records(project: str, table: str, values: dict[str, Any], ids: list[int] | None = None,
                         filters: dict[str, Any] | None = None, reason: str = "") -> dict:
    """
    Set values ({column: new value}) on the rows given by ids or filters (as
    in query_records). Up to 25 rows apply at once; more return a proposal
    with a sample to show the user, applied with apply_change. Needs the
    editor role and the write scope.
    """
    def run(user):
        change = changes.change_records(user, project, table, "update", via=via(), ids=ids, filters=filters,
                                        values=values, reason=reason)
        return changes.describe(change)
    return await tool("mcp:write", run)()


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_records(project: str, table: str, ids: list[int] | None = None,
                         filters: dict[str, Any] | None = None, reason: str = "") -> dict:
    """
    Delete the rows given by ids or filters. Up to 25 rows apply at once;
    more return a proposal, applied with apply_change. revert_change brings
    them back. Needs the admin role and the write scope.
    """
    def run(user):
        change = changes.change_records(user, project, table, "delete", via=via(), ids=ids, filters=filters,
                                        reason=reason)
        return changes.describe(change)
    return await tool("mcp:write", run)()


@mcp.tool(annotations=WRITE)
async def propose_table_change(project: str, operations: list[dict[str, Any]], reason: str) -> dict:
    """
    Propose changes to a project's live tables, applied together with
    apply_change once the user has seen the effects and SQL. operations, in order:
    {"op": "create_table", "table": "letters", "label": "", "columns": [column, ...]},
    {"op": "drop_table", "table": t}, {"op": "rename_table", "table": t, "to": new},
    {"op": "add_column", "table": t, "column": column}, {"op": "drop_column", "table": t, "column": c},
    {"op": "rename_column", "table": t, "column": c, "to": new},
    {"op": "alter_column", "table": t, "column": c, "changes": {"required": true, ...}}.
    A column is {"name", "type": text|longtext|integer|float|boolean|date|datetime|time|json|url|email|relation,
    and optionally "label", "description", "required", "unique", "max_length" (text), "target" (relation:
    the linked table), "default"}. Types never change in place. reason (required) says why. Needs the
    admin role and the schema scope.
    """
    def run(user):
        change = changes.propose_schema(user, project, operations, via=via(), reason=reason)
        return changes.describe(change)
    return await tool("mcp:schema", run)()


def change_or_error(id):
    change = Change.objects.filter(pk=id).select_related("proposed_by", "decided_by").first()
    if change is None:
        raise ToolError(f"No change #{id}.")
    return change


@mcp.tool(annotations=DESTRUCTIVE)
async def apply_change(id: int) -> dict:
    """
    Apply a proposed change once the user agrees. Table changes need the
    schema scope and snapshot the project first; records changes need the
    write scope and check the rows have not changed since.
    """
    def run(user):
        change = change_or_error(id)
        current_user("mcp:schema" if change.kind == "schema" else "mcp:write")
        return changes.describe(changes.apply(change, user, via=via()))
    return await tool("mcp:read", run)()


@mcp.tool(annotations=WRITE)
async def reject_change(id: int) -> dict:
    """Reject a proposed change, e.g. when the user declines it."""
    def run(user):
        change = change_or_error(id)
        current_user("mcp:schema" if change.kind == "schema" else "mcp:write")
        return changes.describe(changes.reject(change, user, via=via()))
    return await tool("mcp:read", run)()


@mcp.tool(annotations=DESTRUCTIVE)
async def revert_change(id: int, reason: str = "") -> dict:
    """Undo an applied records change while its rows are still as it left them. Needs the write scope."""
    def run(user):
        return changes.describe(changes.revert(change_or_error(id), user, via=via(), reason=reason))
    return await tool("mcp:write", run)()


@mcp.tool(annotations=READ)
async def list_changes(project: str | None = None, status: str | None = None, limit: int = 20) -> dict:
    """The latest changes (newest first): proposals awaiting a decision, applied, rejected, failed, reverted."""
    def run(user):
        found = Change.objects.select_related("proposed_by", "decided_by")
        if project:
            found = found.filter(project=project)
        if status:
            found = found.filter(status=status)
        return {"changes": [changes.describe(change, diff_rows=0) for change in found[:min(max(limit, 1), 100)]]}
    return await tool("mcp:read", run)()


def site_tool(fn):
    """Run fn(user, ...) for a staff user with the write scope, reporting site errors to the agent."""
    def run(*args, **kwargs):
        try:
            return fn(current_user("mcp:write", staff=True), *args, **kwargs)
        except sites.SiteError as error:
            raise ToolError(str(error)) from error
    return orm(run)


@mcp.tool(annotations=READ)
async def list_sites(project: str | None = None) -> dict:
    """List the static sites, with their public URL, live version and versions."""
    def run(user):
        from apiary.models import Site

        found = Site.objects.order_by("project", "name").prefetch_related("versions")
        if project:
            found = found.filter(project=project)
        return {"sites": [sites.describe(site) for site in found]}
    return await tool("mcp:read", run)()


@mcp.tool(annotations=WRITE)
async def create_site(project: str, name: str, title: str = "") -> dict:
    """
    Create an empty static site for a project, served at <name>.sites.rrchnm.org.
    Names are unique across projects: lowercase letters, digits and inner
    hyphens. Staff only.
    """
    def run(user):
        return sites.describe(sites.create_site(project, name, title=title, user=user))
    return await site_tool(run)()


@mcp.tool(annotations=WRITE)
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


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_files(project: str, site: str, paths: list[str]) -> dict:
    """Remove files from the site's draft. Staff only."""
    def run(user):
        version = sites.delete_files(sites.get_site(project, site), paths, user=user)
        return {"draft_version": version.number, "files": sorted(version.files)}
    return await site_tool(run)()


@mcp.tool(annotations=WRITE)
async def publish_site(project: str, site: str) -> dict:
    """Publish the draft: make it the live version. Returns the URL. Staff only."""
    def run(user):
        target = sites.get_site(project, site)
        version = sites.publish(target, user=user)
        return {"url": sites.site_url(target), "version": version.number, "files": sorted(version.files)}
    return await site_tool(run)()


@mcp.tool(annotations=WRITE)
async def rollback_site(project: str, site: str, version: int) -> dict:
    """Make an earlier published version live again. Staff only."""
    def run(user):
        target = sites.get_site(project, site)
        sites.rollback(target, version)
        return {"url": sites.site_url(target), "live_version": version}
    return await site_tool(run)()


@mcp.tool(annotations=DESTRUCTIVE)
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
