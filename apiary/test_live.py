from django.contrib.auth.models import Group, User
from django.test import TransactionTestCase

from apiary import changes, projectdata
from apiary.live import runtime, schema
from apiary.live.testing import drop_live_tables
from apiary.models import Change
from apiary.projects import project_alias, project_models

ALL = {"default", "demo_db", "bom_db", "connthreads_db", "mappingviolence_db", "relec_db", "pinkertons_db"}

LETTERS = [
    {"op": "create_table", "table": "people", "columns": [
        {"name": "name", "type": "text", "required": True, "unique": True},
    ]},
    {"op": "create_table", "table": "letters", "label": "Letters", "columns": [
        {"name": "sent", "type": "date"},
        {"name": "subject", "type": "text", "max_length": 200},
        {"name": "pages", "type": "integer", "default": 1},
        {"name": "author", "type": "relation", "target": "people"},
    ]},
]


class LiveCase(TransactionTestCase):
    # Schema changes run outside a test transaction: SQLite's schema editor
    # refuses to run inside one.
    databases = ALL
    project = "bom"  # SQLite; the PostgreSQL projects run the same code on their schema

    def setUp(self):
        self.admin = User.objects.create_user("owner", is_staff=True)
        self.admin.groups.add(Group.objects.create(name=f"{self.project}-admin"))
        self.addCleanup(drop_live_tables)

    def change_tables(self, operations, reason="test"):
        change = changes.propose_schema(self.admin, self.project, operations, via="test", reason=reason)
        return changes.apply(change, self.admin, via="test")

    def models(self):
        return project_models(self.project)

    def rows(self, table, **filters):
        return list(self.models()[table].objects.using(project_alias(self.project)).filter(**filters).order_by("pk"))


class SchemaTests(LiveCase):
    def test_creates_tables_with_a_relation_and_records_the_change(self):
        change = self.change_tables(LETTERS, reason="Start the correspondence project")
        self.assertEqual(change.status, "applied")
        self.assertEqual(change.schema_version, 1)
        self.assertEqual(change.snapshot.kind, "pre-change")
        self.assertIn("Creates table letters with 4 columns.", change.preview["effects"])
        self.assertTrue(change.preview["sql"], "the SQL is shown for review")

        changes.create_records(self.admin, self.project, "people", [{"name": "Ada"}], via="test")
        ada = self.rows("people")[0]
        changes.create_records(self.admin, self.project, "letters",
                               [{"sent": "1843-07-10", "subject": "Notes", "author": ada.pk}], via="test")
        letter = self.rows("letters")[0]
        self.assertEqual((str(letter.sent), letter.pages, letter.author_id), ("1843-07-10", 1, ada.pk))
        self.assertEqual(sorted(self.models()), ["letters", "people"])

    def test_plans_refuse_what_the_data_cannot_take(self):
        self.change_tables(LETTERS)
        changes.create_records(self.admin, self.project, "people", [{"name": "Ada"}, {"name": "Charles"}], via="test")
        changes.create_records(self.admin, self.project, "letters", [{"subject": "a"}, {"subject": "a"}], via="test")
        refusals = {
            "needs a default": [{"op": "add_column", "table": "people", "column": {"name": "born", "type": "date", "required": True}}],
            "rows have no value": [{"op": "alter_column", "table": "letters", "column": "sent", "changes": {"required": True}}],
            "more than once": [{"op": "alter_column", "table": "letters", "column": "subject", "changes": {"unique": True}}],
            "link to people": [{"op": "drop_table", "table": "people"}],
            "add a new column": [{"op": "alter_column", "table": "letters", "column": "pages", "changes": {"type": "text"}}],
            "reserved": [{"op": "create_table", "table": "meta_x", "columns": []}],
            "already has a table": [{"op": "create_table", "table": "letters", "columns": []}],
        }
        for expected, operations in refusals.items():
            with self.assertRaisesMessage(changes.ChangeError, expected):
                changes.propose_schema(self.admin, self.project, operations, via="test", reason="x")
        self.assertFalse(Change.objects.filter(kind="schema", status="proposed").exists())

    def test_renames_and_alters_keep_the_rows(self):
        self.change_tables(LETTERS)
        changes.create_records(self.admin, self.project, "people", [{"name": "Ada"}], via="test")
        ada = self.rows("people")[0]
        changes.create_records(self.admin, self.project, "letters", [{"subject": "Notes", "author": ada.pk}], via="test")
        self.change_tables([
            {"op": "rename_table", "table": "people", "to": "writers"},
            {"op": "rename_column", "table": "letters", "column": "subject", "to": "title"},
            {"op": "add_column", "table": "letters", "column": {"name": "archive", "type": "text", "required": True, "default": "BL"}},
            {"op": "alter_column", "table": "letters", "column": "title", "changes": {"required": True, "max_length": 50}},
            {"op": "drop_column", "table": "letters", "column": "pages"},
        ])
        letter = self.rows("letters")[0]
        self.assertEqual((letter.title, letter.archive, letter.author_id), ("Notes", "BL", ada.pk))
        self.assertEqual(self.models()["letters"]._meta.get_field("author").related_model._meta.model_name, "writers")
        self.assertFalse(hasattr(letter, "pages"))
        specs = {t["name"]: t for t in runtime.live_specs(self.project)}
        self.assertEqual([c["name"] for c in specs["letters"]["columns"]], ["sent", "title", "author", "archive"])

    def test_a_proposal_goes_stale_when_the_tables_move(self):
        self.change_tables(LETTERS)
        first = changes.propose_schema(self.admin, self.project, [{"op": "drop_column", "table": "letters", "column": "pages"}], via="test", reason="x")
        self.change_tables([{"op": "add_column", "table": "letters", "column": {"name": "notes", "type": "longtext"}}])
        with self.assertRaisesMessage(changes.ChangeError, "changed since"):
            changes.apply(first, self.admin, via="test")
        first.refresh_from_db()
        self.assertEqual(first.status, "failed")

    def test_only_project_admins_change_tables_and_must_say_why(self):
        editor = User.objects.create_user("editor", is_staff=True)
        editor.groups.add(Group.objects.create(name=f"{self.project}-editor"))
        with self.assertRaisesMessage(changes.ChangeError, "admin role"):
            changes.propose_schema(editor, self.project, LETTERS, via="test", reason="x")
        with self.assertRaisesMessage(changes.ChangeError, "reason is required"):
            changes.propose_schema(self.admin, self.project, LETTERS, via="test", reason=" ")

    def test_snapshots_carry_the_schema_and_restoring_rebuilds_it(self):
        self.change_tables(LETTERS)
        changes.create_records(self.admin, self.project, "people", [{"name": "Ada"}], via="test")
        ada = self.rows("people")[0]
        changes.create_records(self.admin, self.project, "letters", [{"subject": "Notes", "author": ada.pk}], via="test")
        snapshot = projectdata.take_snapshot(self.project, kind="manual")
        self.assertEqual([t["name"] for t in snapshot.schema], ["letters", "people"])
        self.assertEqual(snapshot.row_counts, {"letters": 1, "people": 1})

        self.change_tables([{"op": "drop_column", "table": "letters", "column": "subject"},
                            {"op": "create_table", "table": "places", "columns": [{"name": "name", "type": "text"}]}])
        changes.create_records(self.admin, self.project, "people", [{"name": "Charles"}], via="test")

        projectdata.restore_snapshot(snapshot)
        self.assertEqual(sorted(self.models()), ["letters", "people"])
        self.assertEqual([p.name for p in self.rows("people")], ["Ada"])
        letter = self.rows("letters")[0]
        self.assertEqual((letter.subject, letter.author_id), ("Notes", ada.pk))
        # New rows continue after the restored ids.
        changes.create_records(self.admin, self.project, "people", [{"name": "Mary"}], via="test")
        self.assertEqual(self.rows("people", name="Mary")[0].pk, ada.pk + 1)


class RecordsTests(LiveCase):
    def setUp(self):
        super().setUp()
        self.change_tables(LETTERS)
        self.editor = User.objects.create_user("editor", is_staff=True)
        self.editor.groups.add(Group.objects.create(name=f"{self.project}-editor"))

    def test_small_changes_apply_at_once_and_revert(self):
        created = changes.create_records(self.editor, self.project, "letters", [{"subject": "a"}, {"subject": "b"}], via="MCP: test")
        self.assertEqual((created.status, created.decided_by, created.proposed_via), ("applied", self.editor, "MCP: test"))
        updated = changes.change_records(self.editor, self.project, "letters", "update", ids=[r["id"] for r in created.after],
                                         values={"pages": 3}, via="test", reason="Counted the pages")
        self.assertEqual(updated.status, "applied")
        self.assertEqual([l.pages for l in self.rows("letters")], [3, 3])

        changes.revert(updated, self.editor, via="test")
        self.assertEqual([l.pages for l in self.rows("letters")], [1, 1])
        updated.refresh_from_db()
        self.assertEqual(updated.status, "reverted")

    def test_deletes_need_admins_and_revert_brings_rows_back(self):
        changes.create_records(self.editor, self.project, "letters", [{"subject": "a"}], via="test")
        with self.assertRaisesMessage(changes.ChangeError, "admin role"):
            changes.change_records(self.editor, self.project, "letters", "delete", filters={"subject": "a"}, via="test")
        deleted = changes.change_records(self.admin, self.project, "letters", "delete", filters={"subject": "a"}, via="test")
        self.assertEqual(self.rows("letters"), [])
        changes.revert(deleted, self.admin, via="test")
        self.assertEqual([l.subject for l in self.rows("letters")], ["a"])

    def test_large_changes_are_proposed_then_checked_against_the_rows(self):
        changes.create_records(self.editor, self.project, "letters", [{"subject": f"s{i}"} for i in range(30)], via="test")
        proposal = changes.change_records(self.editor, self.project, "letters", "update", filters={"subject__startswith": "s"},
                                          values={"pages": 2}, via="test")
        self.assertEqual((proposal.status, proposal.preview["rows"], len(proposal.preview["sample"])), ("proposed", 30, 5))
        self.assertEqual({l.pages for l in self.rows("letters")}, {1}, "nothing changes until applied")

        stale = changes.change_records(self.editor, self.project, "letters", "update", filters={"subject__startswith": "s"},
                                       values={"pages": 9}, via="test")
        changes.apply(proposal, self.editor, via="test")
        self.assertEqual({l.pages for l in self.rows("letters")}, {2})
        self.assertEqual(proposal.snapshot.kind, "pre-change")
        with self.assertRaisesMessage(changes.ChangeError, "changed since"):
            changes.apply(stale, self.editor, via="test")

    def test_deletes_follow_each_links_on_delete(self):
        people = changes.create_records(self.admin, self.project, "people", [{"name": "Ada"}], via="test").after
        changes.create_records(self.editor, self.project, "letters", [{"subject": "a", "author": people[0]["id"]}], via="test")
        changes.change_records(self.admin, self.project, "people", "delete", ids=[people[0]["id"]], via="test")
        self.assertIsNone(self.rows("letters")[0].author_id, "an optional link clears")

        self.change_tables([{"op": "create_table", "table": "copies", "columns": [
            {"name": "letter", "type": "relation", "target": "letters", "required": True}]}])
        letter = self.rows("letters")[0]
        changes.create_records(self.editor, self.project, "copies", [{"letter": letter.pk}], via="test")
        with self.assertRaisesMessage(changes.ChangeError, "Other rows link to these"):
            changes.change_records(self.admin, self.project, "letters", "delete", ids=[letter.pk], via="test")

    def test_bad_values_fail_without_writing(self):
        with self.assertRaisesMessage(changes.ChangeError, "sent"):
            changes.create_records(self.editor, self.project, "letters", [{"subject": "ok"}, {"sent": "not a date"}], via="test")
        self.assertEqual(self.rows("letters"), [])
        self.assertEqual(Change.objects.get(kind="records").status, "failed")
        with self.assertRaisesMessage(changes.ChangeError, "No column"):
            changes.create_records(self.editor, self.project, "letters", [{"nope": 1}], via="test")
        viewer = User.objects.create_user("viewer", is_staff=True)
        with self.assertRaisesMessage(changes.ChangeError, "editor role"):
            changes.create_records(viewer, self.project, "letters", [{"subject": "x"}], via="test")

    def test_reverting_refuses_to_overwrite_newer_edits(self):
        created = changes.create_records(self.editor, self.project, "letters", [{"subject": "a"}], via="test")
        changes.change_records(self.editor, self.project, "letters", "update", ids=[created.after[0]["id"]], values={"subject": "b"}, via="test")
        with self.assertRaisesMessage(changes.ChangeError, "changed since"):
            changes.revert(created, self.editor, via="test")
        self.assertEqual([l.subject for l in self.rows("letters")], ["b"])


class RuntimeTests(LiveCase):
    def test_each_schema_change_rebuilds_the_models(self):
        self.assertEqual(self.models(), {})
        self.change_tables(LETTERS[:1])
        first = self.models()["people"]
        self.assertIs(self.models()["people"], first, "cached while the schema stands")
        self.change_tables([{"op": "add_column", "table": "people", "column": {"name": "born", "type": "date"}}])
        self.assertIsNot(self.models()["people"], first)
        self.assertIn("born", [f.name for f in self.models()["people"]._meta.fields])

    def test_live_models_stay_out_of_djangos_registry(self):
        from django.apps import apps

        self.change_tables(LETTERS[:1])
        self.assertEqual([m for m in apps.get_models() if m._meta.app_label == self.project], [])

    def test_names_are_checked(self):
        for bad in ("Letters", "1st", "id", "class", "meta_tables", "x" * 41, "has-dash"):
            with self.assertRaises(runtime.SchemaError, msg=bad):
                schema.plan(self.project, [{"op": "create_table", "table": bad, "columns": []}])


class AdminTests(LiveCase):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser("root", password="x")
        self.client.force_login(self.root)

    def test_tables_page_shows_live_tables_to_project_members(self):
        self.change_tables(LETTERS)
        changes.create_records(self.admin, self.project, "letters", [{"subject": "Notes"}], via="test")
        page = self.client.get(f"/admin/apiary/change/tables/{self.project}/")
        self.assertContains(page, "letters")
        self.assertNotContains(page, "Live table")  # one list for every table, however it was made
        self.assertContains(page, "Notes")
        self.assertContains(page, "link to people")
        self.assertEqual(self.client.get("/admin/apiary/change/tables/nope/").status_code, 404)

    def test_superusers_apply_and_revert_from_the_admin(self):
        proposal = changes.propose_schema(self.admin, self.project, LETTERS, via="MCP: agent", reason="Start")
        page = self.client.get(f"/admin/apiary/change/{proposal.pk}/change/")
        self.assertContains(page, "Creates table letters with 4 columns.")
        self.assertContains(page, "CREATE TABLE")
        self.assertContains(self.client.get(f"/admin/apiary/change/{proposal.pk}/apply/"), "Apply")  # confirm first
        self.assertEqual(Change.objects.get(pk=proposal.pk).status, "proposed")
        self.client.post(f"/admin/apiary/change/{proposal.pk}/apply/")
        proposal.refresh_from_db()
        self.assertEqual((proposal.status, proposal.decided_by, proposal.decided_via), ("applied", self.root, "admin"))

        created = changes.create_records(self.admin, self.project, "people", [{"name": "Ada"}], via="test")
        self.client.post(f"/admin/apiary/change/{created.pk}/revert/")
        self.assertEqual(self.rows("people"), [])
        self.assertEqual(self.client.get(f"/admin/apiary/change/{created.pk}/delete/").status_code, 403)
