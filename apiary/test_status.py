import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.db import OperationalError
from django.test import RequestFactory, SimpleTestCase

from apiary import views
from apiary.status import snapshot


class StatusTests(SimpleTestCase):
    databases = "__all__"

    def call(self, view, user):
        request = RequestFactory().get("/")
        request.user = user
        return view(request)

    def test_snapshot_reports_every_database_and_storage(self):
        s = snapshot()
        self.assertTrue(s["ok"], s)
        self.assertEqual(set(s["databases"]), {"default", "bom_db", "connthreads_db", "mappingviolence_db", "relec_db"})
        self.assertEqual(set(s["media"]), {"public", "private"})

    def test_a_failing_check_degrades_the_snapshot_instead_of_raising(self):
        with patch("apiary.status._select_one", side_effect=OperationalError("secret dsn")):
            s = snapshot()
        self.assertFalse(s["ok"])
        self.assertEqual(s["databases"]["default"]["error"], "OperationalError")
        self.assertNotIn("secret dsn", json.dumps(s))

    def test_endpoints_are_superuser_only(self):
        for view in (views.status_page, views.status_json, views.status_stream):
            self.assertEqual(self.call(view, AnonymousUser()).status_code, 302, view.__name__)
            staff = MagicMock(is_authenticated=True, is_active=True, is_superuser=False)
            self.assertEqual(self.call(view, staff).status_code, 302, view.__name__)

    def test_stream_sends_a_retry_hint_then_bounded_snapshot_events(self):
        superuser = MagicMock(is_authenticated=True, is_active=True, is_superuser=True)
        with patch.object(views, "STATUS_STREAM_INTERVAL", 0):
            response = self.call(views.status_stream, superuser)
            chunks = [c.decode() for c in response.streaming_content]
        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertEqual(chunks[0], "retry: 0\n\n")
        events = chunks[1:]
        self.assertEqual(len(events), views.STATUS_STREAM_EVENTS)
        self.assertTrue(all(e.startswith("data: ") and e.endswith("\n\n") for e in events))
        self.assertIn("databases", json.loads(events[0][len("data: "):]))
