from django.contrib.admin.sites import site
from django.test import TestCase

from apiary import changes, diffview
from apiary.models import Change


def location(id, **values):
    return {"id": id, "locality": "Long Branch, NJ", "street_address": "", "location_name": "", **values}


def update(before, after=(), values=None, status="applied"):
    operation = {"op": "update", "table": "location", "ids": [r["id"] for r in before]}
    if values is not None:
        operation["values"] = values
    return Change(project="demo", kind="records", status=status, operations=[operation], before=list(before), after=list(after))


class DiffTests(TestCase):
    databases = "__all__"  # columns are ordered by the project's own tables

    def test_an_update_shows_only_what_changed_and_names_each_row_by_what_stayed(self):
        change = update([location(20, street_address="Newbury 5 & 10 cent store")],
                        [location(20, location_name="Newbury 5 & 10 Cent Store")])

        found = changes.diff(change)

        self.assertEqual((found["action"], found["rows"], found["applied"], found["more_rows"]), ("update", 1, True, 0))
        [item] = found["items"]
        self.assertEqual((item["id"], item["label"]), (20, "Long Branch, NJ"))
        self.assertEqual({f["column"]: (f["before"], f["after"]) for f in item["fields"] if f["changed"]}, {
            "street_address": ("Newbury 5 & 10 cent store", ""),
            "location_name": ("", "Newbury 5 & 10 Cent Store"),
        })

    def test_rows_are_named_by_text_that_tells_them_apart_not_by_dates(self):
        rows = [{"id": 1, "date": "1939-07-27", "investigation": "el-paso", "activity": "Search"},
                {"id": 2, "date": "1939-07-28", "investigation": "el-paso", "activity": "Contact"}]

        naming = changes.naming_columns(rows, ["date", "investigation", "activity"])

        self.assertEqual(naming, ["activity", "investigation"])
        self.assertEqual([changes.label(r, [], naming) for r in rows], ["Search", "Contact"])

    def test_a_proposal_shows_what_applying_would_do(self):
        change = update([location(1, locality=" Atlanta"), location(2, locality=" Atlanta")],
                        values={"locality": "Atlanta"}, status="proposed")

        found = changes.diff(change)

        self.assertFalse(found["applied"])
        self.assertEqual(found["summary"], [{"column": "locality", "before": " Atlanta", "after": "Atlanta", "rows": 2}])

    def test_a_big_change_stays_short(self):
        before = [location(i, locality=f"Town {i % 15}") for i in range(60)]
        change = update(before, [{**r, "locality": "Atlanta"} for r in before])

        found = changes.diff(change)

        self.assertEqual((len(found["items"]), found["more_rows"]), (changes.DIFF_ROWS, 10))
        self.assertEqual(len(found["summary"]), changes.DIFF_VALUES + 1)
        self.assertEqual(found["summary"][-1], {"column": "locality", "other": 5, "rows": 20})
        self.assertEqual(sum(move["rows"] for move in found["summary"]), 60)

    def test_a_revert_is_its_change_the_other_way_round(self):
        original = update([location(21, locality="")], [location(21)])
        original.pk = 7
        undo = Change(project="demo", kind="records", status="applied",
                      operations=[{"op": "revert", "of": 2, "table": "location"}], reverts=original)

        [field] = [f for f in changes.diff(undo)["items"][0]["fields"] if f["changed"]]

        self.assertEqual((field["before"], field["after"]), ("Long Branch, NJ", ""))
        self.assertEqual(changes.diff(undo)["undoes"], 7)

    def test_added_and_removed_rows_show_whole(self):
        added = Change(project="demo", kind="records", status="applied",
                       operations=[{"op": "create", "table": "location", "rows": [{"locality": "Bronx"}]}],
                       after=[location(31, locality="Bronx")])
        removed = Change(project="demo", kind="records", status="proposed",
                         operations=[{"op": "delete", "table": "location", "ids": [15]}], before=[location(15)])

        self.assertEqual(changes.diff(added)["items"][0]["fields"][0], {"column": "locality", "before": None, "after": "Bronx", "changed": True})
        self.assertEqual((changes.diff(removed)["action"], changes.diff(removed)["summary"]), ("delete", []))

    def test_table_changes_have_no_rows_to_show(self):
        self.assertIsNone(changes.diff(Change(kind="schema", operations=[{"op": "create_table"}])))

    def test_agents_get_each_rows_changed_columns_only(self):
        change = update([location(19, street_address="Woolworth 5 and 10 sent Store")], [location(19)])

        found = changes.compact_diff(change, rows=10)

        self.assertEqual(found["items"], [{"id": 19, "label": "Long Branch, NJ",
                                           "changes": {"street_address": ["Woolworth 5 and 10 sent Store", ""]}}])

    def test_long_text_marks_only_the_words_that_differ_and_shows_edge_spaces(self):
        old, new = diffview.word_diff(" Pancho Martinez", "Pancho Martinez")

        self.assertEqual(old, [(" ", True, True), ("Pancho", False, False), (" ", False, False), ("Martinez", False, False)])
        self.assertEqual([marked for _, marked, _ in new], [False, False, False])

        old, new = diffview.word_diff("took up watch fro the father", "took up watch for the father")
        self.assertEqual([text for text, marked, _ in old if marked], ["fro"])
        self.assertEqual([text for text, marked, _ in new if marked], ["for"])


class ChangeAdminViewTests(TestCase):
    databases = "__all__"  # column labels come from the project's own tables

    def test_shows_rows_side_by_side_with_field_labels(self):
        change = update([location(13, locality=" Smeltertown")], [location(13, locality="Smeltertown")])

        html = site._registry[Change].rows_view(change)

        self.assertIn("Before", html)
        self.assertIn("After", html)
        self.assertIn("Locality", html)
        self.assertIn('title="A space"', html)
        self.assertIn("␣ marks a space", html)
        self.assertIn("Row 13", html)
