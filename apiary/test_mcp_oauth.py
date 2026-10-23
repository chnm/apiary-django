import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apiary import mcp_oauth
from apiary.mcp_testing import mcp_client
from apiary.models import OAuthClient, OAuthGrant, OAuthToken

REDIRECT_URI = "http://127.0.0.1:33418/callback"
RESOURCE = "http://localhost/mcp"
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


# Transactional: the MCP app reads on its own thread, which can't see a
# TestCase's uncommitted rows.
@override_settings(SECURE_SSL_REDIRECT=False)
class OAuthFlowTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mcp = mcp_client()

    def setUp(self):
        self.staff = User.objects.create_user("agent-owner", is_staff=True)
        self.client.force_login(self.staff)

    def register(self, **metadata):
        body = {"redirect_uris": [REDIRECT_URI], "client_name": "Test agent", "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], **metadata}
        return self.mcp.post("/register", json=body)

    def authorize(self, client_id, challenge, **params):
        """Start at the MCP app's /authorize; return the consent page's path and query."""
        response = self.mcp.get("/authorize", params={
            "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
            "resource": RESOURCE, **params,
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.text)
        return response.headers["location"]

    def consent(self, location, decision="approve", scopes=()):
        url = urlsplit(location)
        self.assertEqual(f"{url.scheme}://{url.netloc}", "http://testserver")
        page = self.client.get(f"{url.path}?{url.query}")
        self.assertEqual(page.status_code, 200)
        return self.client.post(url.path, {"request": query(location)["request"], decision: "", "scope": list(scopes)})

    def token(self, **form):
        return self.mcp.post("/token", data=form)

    def connect(self):
        client_id = self.register().json()["client_id"]
        verifier, challenge = pkce()
        code = query(self.consent(self.authorize(client_id, challenge))["Location"])["code"]
        tokens = self.token(grant_type="authorization_code", code=code, redirect_uri=REDIRECT_URI,
                            client_id=client_id, code_verifier=verifier, resource=RESOURCE).json()
        return client_id, tokens

    def tools(self, access):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        return self.mcp.post("/mcp", json=body, headers={**ACCEPT, "Authorization": f"Bearer {access}"})

    def test_publishes_metadata_and_points_unauthenticated_clients_to_it(self):
        server = self.mcp.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(server["issuer"], "http://localhost")
        self.assertEqual(server["registration_endpoint"], "http://localhost/register")
        self.assertEqual(server["code_challenge_methods_supported"], ["S256"])
        resource = self.mcp.get("/.well-known/oauth-protected-resource/mcp").json()
        self.assertEqual(resource["resource"], RESOURCE)
        self.assertEqual(resource["authorization_servers"], ["http://localhost"])
        challenge = self.mcp.post("/mcp", json={}, headers=ACCEPT).headers["www-authenticate"]
        self.assertIn('resource_metadata="http://localhost/.well-known/oauth-protected-resource/mcp"', challenge)

    def test_connects_with_consent_then_refreshes_and_revokes(self):
        client_id = self.register().json()["client_id"]
        verifier, challenge = pkce()
        location = self.authorize(client_id, challenge)
        self.assertContains(self.client.get(location.removeprefix("http://testserver")), "Test agent")
        callback = self.consent(location)["Location"]
        self.assertTrue(callback.startswith(REDIRECT_URI))
        self.assertEqual(query(callback)["state"], "xyz")
        code = query(callback)["code"]

        wrong = self.token(grant_type="authorization_code", code=code, redirect_uri=REDIRECT_URI,
                           client_id=client_id, code_verifier=pkce()[0])
        self.assertEqual(wrong.json()["error"], "invalid_grant")
        exchange = dict(grant_type="authorization_code", code=code, redirect_uri=REDIRECT_URI,
                        client_id=client_id, code_verifier=verifier, resource=RESOURCE)
        tokens = self.token(**exchange).json()
        self.assertEqual(tokens["token_type"], "Bearer")
        self.assertEqual(tokens["expires_in"], 3600)
        self.assertEqual(self.tools(tokens["access_token"]).status_code, 200)
        self.assertEqual(self.token(**exchange).json()["error"], "invalid_grant", "a code works once")

        connection = OAuthToken.objects.get()
        self.assertEqual(connection.user, self.staff)
        self.assertNotIn(tokens["access_token"], connection.access_digest)

        renewed = self.token(grant_type="refresh_token", refresh_token=tokens["refresh_token"], client_id=client_id).json()
        self.assertEqual(self.tools(renewed["access_token"]).status_code, 200)
        self.assertEqual(self.tools(tokens["access_token"]).status_code, 401, "refreshing rotates the access token")
        stale = self.token(grant_type="refresh_token", refresh_token=tokens["refresh_token"], client_id=client_id)
        self.assertEqual(stale.json()["error"], "invalid_grant", "and the refresh token")

        # SDK 2.3's revocation request requires the client_secret field, empty for a public client.
        revoke = {"token": renewed["refresh_token"], "client_id": client_id, "client_secret": ""}
        self.assertEqual(self.mcp.post("/revoke", data=revoke).status_code, 200)
        self.assertEqual(self.tools(renewed["access_token"]).status_code, 401)
        self.assertFalse(OAuthToken.objects.exists())

    def test_the_user_grants_scopes_on_the_consent_page(self):
        client_id = self.register().json()["client_id"]
        verifier, challenge = pkce()
        location = self.authorize(client_id, challenge, scope="mcp:read mcp:schema")
        page = self.client.get(location.removeprefix("http://testserver")).content.decode()
        self.assertIn('value="mcp:schema" checked', page, "what the client asked for starts checked")
        self.assertNotIn('value="mcp:write" checked', page)
        code = query(self.consent(location, scopes=["mcp:write", "bogus"])["Location"])["code"]
        tokens = self.token(grant_type="authorization_code", code=code, redirect_uri=REDIRECT_URI,
                            client_id=client_id, code_verifier=verifier, resource=RESOURCE).json()
        self.assertEqual(tokens["scope"], "mcp:read mcp:write")
        self.assertEqual(OAuthToken.objects.get().scopes, ["mcp:read", "mcp:write"])

    def test_deny_returns_access_denied(self):
        client_id = self.register().json()["client_id"]
        callback = self.consent(self.authorize(client_id, pkce()[1]), decision="deny")["Location"]
        self.assertEqual(query(callback), {"error": "access_denied", "state": "xyz"})
        self.assertFalse(OAuthGrant.objects.exists())

    def test_only_active_staff_stay_connected(self):
        client_id, tokens = self.connect()
        self.staff.is_staff = False
        self.staff.save()
        self.assertEqual(self.tools(tokens["access_token"]).status_code, 401)
        refresh = self.token(grant_type="refresh_token", refresh_token=tokens["refresh_token"], client_id=client_id)
        self.assertEqual(refresh.json()["error"], "invalid_grant")
        self.assertFalse(OAuthToken.objects.exists(), "a refused refresh ends the connection")

    def test_non_staff_are_sent_to_log_in(self):
        client_id = self.register().json()["client_id"]
        location = self.authorize(client_id, pkce()[1])
        self.client.force_login(User.objects.create_user("reader"))
        page = self.client.get(location.removeprefix("http://testserver"))
        self.assertEqual(page.status_code, 302)
        self.assertIn("/admin/login/", page["Location"])

    def test_refuses_other_resources_and_unsafe_redirect_uris(self):
        client_id = self.register().json()["client_id"]
        response = self.mcp.get("/authorize", params={
            "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI, "state": "xyz",
            "code_challenge": pkce()[1], "code_challenge_method": "S256", "resource": "https://elsewhere.example/mcp",
        }, follow_redirects=False)
        self.assertEqual(query(response.headers["location"])["error"], "invalid_target")
        for uri in ("http://attacker.example/cb", "javascript:alert(1)", "https://app.example/cb#frag"):
            refused = self.register(redirect_uris=[uri])
            self.assertEqual(refused.status_code, 400, uri)
            self.assertEqual(refused.json()["error"], "invalid_redirect_uri")
        for uri in ("https://claude.ai/api/mcp/auth_callback", "cursor://anysphere.cursor-retrieval/oauth/callback"):
            self.assertEqual(self.register(redirect_uris=[uri]).status_code, 201, uri)

    def test_a_tampered_or_expired_request_shows_an_error(self):
        page = self.client.get("/apiary/mcp/authorize/", {"request": "forged"})
        self.assertEqual(page.status_code, 400)
        self.assertContains(page, "expired", status_code=400)


class OAuthHousekeepingTests(TestCase):
    def test_registering_drops_clients_nobody_connected_within_a_day(self):
        stale = OAuthClient.objects.create(client_id="stale", info={})
        OAuthClient.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(days=2))
        user = User.objects.create_user("owner", is_staff=True)
        used = OAuthClient.objects.create(client_id="used", info={})
        OAuthClient.objects.filter(pk=used.pk).update(created_at=timezone.now() - timedelta(days=2))
        token = OAuthToken(client=used, user=user)
        token.rotate()
        token.save()
        mcp_oauth.register({"client_id": "new", "redirect_uris": [REDIRECT_URI]})
        self.assertEqual(sorted(OAuthClient.objects.values_list("client_id", flat=True)), ["new", "used"])


@override_settings(SECURE_SSL_REDIRECT=False)
class OAuthAdminTests(TestCase):
    def test_only_superusers_see_and_end_connections(self):
        owner = User.objects.create_user("owner", is_staff=True)
        token = OAuthToken(client=OAuthClient.objects.create(client_id="c", name="Agent", info={}), user=owner)
        token.rotate()
        token.save()
        self.client.force_login(owner)
        self.assertEqual(self.client.get("/admin/apiary/oauthtoken/").status_code, 403)
        self.client.force_login(User.objects.create_superuser("root"))
        self.assertContains(self.client.get("/admin/apiary/oauthtoken/"), "Agent")
        self.client.post("/admin/apiary/oauthtoken/", {"action": "delete_selected", "_selected_action": [token.pk], "post": "yes"})
        self.assertFalse(OAuthToken.objects.exists())
