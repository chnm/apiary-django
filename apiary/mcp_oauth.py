"""
OAuth 2.1 for MCP clients, so an AI agent connects to /mcp with its user's
approval instead of a token pasted from the admin.

The MCP container is the authorization server: the SDK serves the metadata,
/register (RFC 7591), /authorize, /token and /revoke, validates the requests
and checks PKCE (apiary.mcp_server). This module keeps their state in Django,
and the consent page (apiary.views.mcp_authorize), behind Django's login,
issues the authorization codes. Only active staff users may connect, because
every MCP token can read every project.
"""
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from mcp.server.auth.provider import construct_redirect_uri

from apiary.models import OAuthClient, OAuthGrant, OAuthToken

REDIRECT_URIS_MAX = 10
# Registration is open, so drop clients nobody approved within a day.
UNUSED_CLIENT_AGE = timedelta(days=1)
REQUEST_SALT = "apiary.mcp_oauth.authorize"
REQUEST_MAX_AGE = 600
# Never hand an authorization code to a scheme a browser would run or read locally.
FORBIDDEN_SCHEMES = {"javascript", "data", "vbscript", "file", "blob", "about", "ftp"}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OAuthError(Exception):
    def __init__(self, code, description):
        super().__init__(description)
        self.code = code
        self.description = description


def resource_url():
    """The MCP endpoint every token is issued for (its RFC 8707 resource)."""
    return settings.MCP_URL.rstrip("/") + "/mcp"


def may_connect(user):
    return user.is_active and user.is_staff


def check_redirect_uri(uri):
    """
    HTTPS anywhere, plain HTTP only on loopback (RFC 8252 §7.3), or an app's
    own scheme (RFC 8252 §7.1), which PKCE protects from interception.
    """
    parts = urlsplit(uri)
    scheme = parts.scheme.lower()
    if parts.fragment:
        raise OAuthError("invalid_redirect_uri", f"{uri!r} must not have a fragment.")
    if scheme == "https" and parts.hostname:
        return
    if scheme == "http" and parts.hostname in LOOPBACK_HOSTS:
        return
    if scheme and scheme not in {"http", "https"} | FORBIDDEN_SCHEMES:
        return
    raise OAuthError("invalid_redirect_uri", f"{uri!r} must be HTTPS, loopback HTTP or an app's own scheme.")


def register(info):
    """Store a client the SDK validated at /register, after checking its redirect URIs."""
    uris = info.get("redirect_uris") or []
    if not uris or len(uris) > REDIRECT_URIS_MAX:
        raise OAuthError("invalid_redirect_uri", f"Register between 1 and {REDIRECT_URIS_MAX} redirect URIs.")
    for uri in uris:
        check_redirect_uri(str(uri))
    OAuthClient.objects.filter(
        created_at__lt=timezone.now() - UNUSED_CLIENT_AGE, tokens__isnull=True, grants__isnull=True,
    ).delete()
    OAuthClient.objects.create(client_id=info["client_id"], name=(info.get("client_name") or "")[:200], info=info)


def get_client(client_id):
    client = OAuthClient.objects.filter(client_id=client_id).first()
    return client.info if client else None


def consent_url(request):
    """Where /authorize sends the browser: the consent page, carrying the signed request."""
    token = signing.dumps(request, salt=REQUEST_SALT, compress=True)
    return f"{settings.APP_URL.rstrip('/')}{reverse('apiary_mcp_authorize')}?{urlencode({'request': token})}"


def load_request(token):
    """The authorization request signed by consent_url; raises signing.BadSignature."""
    return signing.loads(token or "", salt=REQUEST_SALT, max_age=REQUEST_MAX_AGE)


def approve(user, client, request):
    """Issue an authorization code for user; return the client's redirect URL carrying it."""
    if not may_connect(user):
        raise OAuthError("access_denied", "Only active staff can connect MCP clients.")
    code = secrets.token_urlsafe(32)
    now = timezone.now()
    OAuthGrant.objects.filter(expires_at__lt=now).delete()
    OAuthGrant.objects.create(
        client=client, user=user, digest=OAuthToken.hash(code), expires_at=now + OAuthGrant.LIFETIME,
        request={key: request[key] for key in (
            "redirect_uri", "redirect_uri_provided_explicitly", "code_challenge", "scopes")},
    )
    return construct_redirect_uri(request["redirect_uri"], code=code, state=request["state"])


def deny(request):
    return construct_redirect_uri(request["redirect_uri"], error="access_denied", state=request["state"])


def load_grant(client_id, code):
    return OAuthGrant.objects.filter(
        digest=OAuthToken.hash(code), client__client_id=client_id, expires_at__gt=timezone.now(),
    ).first()


def exchange_grant(client_id, code):
    """Redeem a code once; return (OAuthToken, access token, refresh token)."""
    issued = _exchange_grant(client_id, code)
    if issued is None:
        raise OAuthError("invalid_grant", "This user can no longer connect MCP clients.")
    return issued


def _exchange_grant(client_id, code):
    # None when the user may no longer connect: raising inside the transaction
    # would roll back the code's deletion.
    with transaction.atomic():
        grant = OAuthGrant.objects.select_for_update().select_related("client", "user").filter(
            digest=OAuthToken.hash(code), client__client_id=client_id,
        ).first()
        if grant is None or grant.expires_at <= timezone.now():
            raise OAuthError("invalid_grant", "The authorization code is invalid or has expired.")
        grant.delete()
        if not may_connect(grant.user):
            return None
        token = OAuthToken(client=grant.client, user=grant.user, scopes=grant.request["scopes"])
        access, refresh = token.rotate()
        token.save()
    return token, access, refresh


def load_refresh(client_id, raw):
    return OAuthToken.objects.filter(
        refresh_digest=OAuthToken.hash(raw), client__client_id=client_id,
        refresh_expires_at__gt=timezone.now(),
    ).first()


def rotate(client_id, raw):
    """Rotate a connection's tokens for its refresh token; return (OAuthToken, access token, refresh token)."""
    issued = _rotate(client_id, raw)
    if issued is None:
        raise OAuthError("invalid_grant", "This user can no longer connect MCP clients.")
    return issued


def _rotate(client_id, raw):
    # None when the user may no longer connect, after ending the connection.
    with transaction.atomic():
        token = OAuthToken.objects.select_for_update().select_related("user").filter(
            refresh_digest=OAuthToken.hash(raw), client__client_id=client_id,
        ).first()
        if token is None or token.refresh_expires_at <= timezone.now():
            raise OAuthError("invalid_grant", "The refresh token is invalid or has expired.")
        if not may_connect(token.user):
            token.delete()
            return None
        access, refresh = token.rotate()
        token.save()
    return token, access, refresh


def authenticate(access):
    """The connection for this access token, if it and its user are still valid; else None."""
    now = timezone.now()
    token = OAuthToken.objects.select_related("client", "user").filter(
        access_digest=OAuthToken.hash(access), access_expires_at__gt=now,
    ).first()
    if token is None or not may_connect(token.user):
        return None
    if token.last_used_at is None or now - token.last_used_at > OAuthToken.LAST_USED_RESOLUTION:
        OAuthToken.objects.filter(pk=token.pk).update(last_used_at=now)
    return token


def revoke(raw):
    """End the connection holding this access or refresh token (RFC 7009)."""
    digest = OAuthToken.hash(raw)
    OAuthToken.objects.filter(Q(access_digest=digest) | Q(refresh_digest=digest)).delete()
