from django.contrib.auth.models import Group, User

from apiary.models import Change
from apiary.test_live import LETTERS, LiveCase

PAGE = "/admin/apiary/change/tables/bom/letters/"


class TablePageTests(LiveCase):
    """People edit live tables on their pages; every edit is a change, as an agent's would be."""

    def setUp(self):
        super().setUp()
        self.change_tables(LETTERS)
        self.editor = User.objects.create_user("editor", is_staff=True)
        self.editor.groups.add(Group.objects.create(name="bom-editor"))
        self.viewer = User.objects.create_user("viewer", is_staff=True)
        self.viewer.groups.add(Group.objects.create(name="bom-viewer"))
        self.ada = self.models()["people"].objects.using("bom_db").create(name="Ada")

    def test_an_editor_adds_and_edits_rows_and_each_save_is_a_change(self):
        self.client.force_login(self.editor)
        form = self.client.get(f"{PAGE}add/")
        self.assertContains(form, "Ada")  # the author choices come from the project's own database

        added = self.client.post(f"{PAGE}add/", {"subject": "Notes", "sent": "1850-03-01", "pages": "2",
                                                 "author": self.ada.pk, "reason": "From the ledger"})
        self.assertRedirects(added, PAGE, fetch_redirect_response=False)
        [letter] = self.rows("letters")
        self.assertEqual((letter.subject, str(letter.sent), letter.pages, letter.author_id), ("Notes", "1850-03-01", 2, self.ada.pk))
        change = Change.objects.latest("pk")
        self.assertEqual((change.proposed_via, change.reason, change.status), ("admin", "From the ledger", "applied"))

        self.client.post(f"{PAGE}{letter.pk}/", {"subject": "Field notes", "sent": "1850-03-01", "pages": "2",
                                                 "author": self.ada.pk, "reason": ""})
        self.assertEqual(self.rows("letters")[0].subject, "Field notes")
        self.assertEqual(Change.objects.latest("pk").operations[0]["values"], {"subject": "Field notes"})  # only what changed

        page = self.client.get(PAGE, {"q": "field"})
        self.assertContains(page, "Field notes")
        self.assertContains(page, "Add row")
        self.assertContains(page, ">Ada<")  # linked rows by name, not id
        self.assertEqual(self.client.get(PAGE, {"q": "nothing like it"}).context["page"].paginator.count, 0)

    def test_viewers_read_and_only_admins_delete(self):
        letter = self.models()["letters"].objects.using("bom_db").create(subject="Notes")

        self.client.force_login(self.viewer)
        self.assertNotContains(self.client.get(PAGE), "Add row")
        self.assertEqual(self.client.get(f"{PAGE}add/").status_code, 403)
        self.assertEqual(self.client.get(f"{PAGE}{letter.pk}/").status_code, 403)

        self.client.force_login(self.editor)
        self.assertNotContains(self.client.get(f"{PAGE}{letter.pk}/"), "Delete this row")
        self.assertEqual(self.client.post(f"{PAGE}{letter.pk}/delete/").status_code, 403)

        self.client.force_login(self.admin)
        self.assertContains(self.client.get(f"{PAGE}{letter.pk}/"), "Delete this row")
        self.client.post(f"{PAGE}{letter.pk}/delete/", {"reason": "Duplicate"})
        self.assertEqual(self.rows("letters"), [])
        self.assertEqual(Change.objects.latest("pk").reason, "Duplicate")

    def test_a_refused_change_is_shown_on_the_form(self):
        self.client.force_login(self.editor)
        response = self.client.post("/admin/apiary/change/tables/bom/people/add/", {"name": "Ada", "reason": ""})
        self.assertEqual(response.status_code, 200)  # back on the form, with why
        self.assertContains(response, "already exists")
        self.assertEqual(len(self.rows("people")), 1)
        self.assertEqual(Change.objects.latest("pk").status, "failed")

    def test_tables_defined_in_code_keep_their_admin_pages(self):
        self.client.force_login(User.objects.create_superuser("root", password="x"))
        self.assertRedirects(self.client.get("/admin/apiary/change/tables/demo/location/"), "/admin/demo/location/",
                             fetch_redirect_response=False)
