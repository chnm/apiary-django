from contextlib import ExitStack
from io import StringIO
from unittest.mock import patch

from allauth.account.models import EmailAddress, EmailConfirmation
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.apps import apps
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import CommandError, call_command
from django.db import connections
from django.test import TestCase

from apiary.management.commands import sanitize_for_dev
from mappingviolence.models import Witness
from relec.models import Schedule


class SanitizeForDevTests(TestCase):
    databases = "__all__"

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "jdoe", email="jdoe@gmu.edu", password="real-password", first_name="Jane", last_name="Doe"
        )
        self.client.force_login(self.user)
        primary = EmailAddress.objects.create(user=self.user, email="jdoe@gmu.edu", primary=True, verified=True)
        EmailAddress.objects.create(user=self.user, email="jane@example.org", primary=False)
        EmailConfirmation.objects.create(email_address=primary, key="confirm-key")
        app = SocialApp.objects.create(provider="orcid", name="ORCID", client_id="cid", secret="csecret")
        account = SocialAccount.objects.create(
            user=self.user, provider="orcid", uid="0000-0002-1825-0097", extra_data={"name": "Jane Doe"}
        )
        SocialToken.objects.create(app=app, account=account, token="oauth-token", token_secret="oauth-secret")
        LogEntry.objects.create(
            user=self.user, content_type=ContentType.objects.get_for_model(Schedule),
            object_id="1", object_repr="Jane's record", action_flag=ADDITION, change_message="changed notes",
        )
        Schedule.objects.create(title="A", box="1", status="done", transcriber="Ann Lee", reviewer="Bo Park")
        Schedule.objects.create(title="B", box="1", status="done", transcriber="Bo Park", reviewer="")
        Witness.objects.create(name="Historical Name", testimony_date="1910-01-01", crime="c", claim="claim")

    def sanitize(self, name="apiary_next", **names):
        """Run the command as if each alias pointed at the named database."""
        out = StringIO()
        with ExitStack() as stack:
            for alias in sanitize_for_dev.copy_aliases():
                stack.enter_context(patch.dict(connections[alias].settings_dict, {"NAME": names.get(alias, name)}))
            call_command("sanitize_for_dev", stdout=out)
        return out.getvalue()

    def test_every_model_is_classified(self):
        labels = {m._meta.label for m in apps.get_models(include_auto_created=True)}
        classified = sanitize_for_dev.KEEP | sanitize_for_dev.SANITIZED | sanitize_for_dev.NOT_IN_COPY
        self.assertEqual(labels - classified, set(), "classify new models in sanitize_for_dev")
        self.assertEqual(classified - labels, set(), "remove models that no longer exist")
        self.assertFalse(sanitize_for_dev.KEEP & sanitize_for_dev.SANITIZED)

    def test_never_touches_the_bom_sqlite_database(self):
        self.assertNotIn("bom_db", sanitize_for_dev.copy_aliases())

    def test_refuses_unless_every_database_is_a_developer_copy(self):
        for names in ({"name": "apiary-django"}, {"name": "apiary_next_old"}, {"relec_db": "apiary-django"}):
            with self.assertRaises(CommandError):
                self.sanitize(**names)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("real-password"))
        self.assertEqual(Session.objects.count(), 1)
        self.assertEqual(Schedule.objects.filter(transcriber="Ann Lee").count(), 1)

    def test_refuses_a_copy_with_an_unclassified_table(self):
        with connections["relec_db"].cursor() as cursor:
            cursor.execute("CREATE TABLE census_import (id integer, minister text)")
        try:
            with self.assertRaisesMessage(CommandError, "relec_db:census_import"):
                self.sanitize()
        finally:
            with connections["relec_db"].cursor() as cursor:
                cursor.execute("DROP TABLE census_import")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "jdoe@gmu.edu")

    def test_removes_credentials_and_personal_data(self):
        self.sanitize()

        self.user.refresh_from_db()
        uid = self.user.pk
        self.assertEqual(
            (self.user.username, self.user.first_name, self.user.last_name, self.user.email),
            (f"user{uid}", "", "", f"user{uid}@example.invalid"),
        )
        self.assertFalse(self.user.has_usable_password())
        self.assertEqual(
            sorted(EmailAddress.objects.values_list("email", flat=True)),
            sorted([f"user{uid}@example.invalid", f"address{EmailAddress.objects.get(primary=False).pk}@example.invalid"]),
        )
        for model in (Session, EmailConfirmation, SocialToken, LogEntry):
            self.assertFalse(model.objects.exists(), model)
        account = SocialAccount.objects.get()
        self.assertEqual((account.uid, account.extra_data), (str(account.pk), {}))
        app = SocialApp.objects.get()
        self.assertEqual((app.client_id, app.secret, app.key, app.settings), ("", "", "", {}))
        self.assertEqual(
            sorted(Schedule.objects.values_list("transcriber", "reviewer")),
            [("contributor-001", "contributor-002"), ("contributor-002", "")],
        )
        self.assertTrue(Witness.objects.filter(name="Historical Name").exists())

    def test_is_idempotent(self):
        self.sanitize()
        first = sorted(Schedule.objects.values_list("transcriber", "reviewer"))
        user = get_user_model().objects.values().get()
        self.sanitize()
        self.assertEqual(sorted(Schedule.objects.values_list("transcriber", "reviewer")), first)
        self.assertEqual({k: v for k, v in get_user_model().objects.values().get().items() if k != "password"},
                         {k: v for k, v in user.items() if k != "password"})

