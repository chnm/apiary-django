"""Test support: one client for the MCP app, whose SDK session manager starts once per process."""
import atexit
from functools import cache


@cache
def mcp_client():
    from starlette.testclient import TestClient

    from apiary.mcp_server import app

    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    atexit.register(client.__exit__, None, None, None)
    return client
