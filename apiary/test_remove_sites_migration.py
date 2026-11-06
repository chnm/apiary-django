from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("apiary", "0008_changes_scopes_live_snapshots")]
AFTER = [("apiary", "0009_remove_sites")]


class RemoveSitesMigrationTests(TransactionTestCase):
    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        self.migrate(executor_leaf())

    def test_deletes_site_files_and_tables(self):
        apps = self.migrate(BEFORE)
        site = apps.get_model("apiary", "Site").objects.create(project="demo", name="old")
        apps.get_model("apiary", "SiteVersion").objects.create(
            site=site, number=1, files={"index.html": {"size": 2}, "missing.css": {"size": 1}})
        storage = storages["default"]
        name = f"sites/demo/{site.key}/v1/index.html"
        storage.save(name, ContentFile(b"hi"))
        other = storage.save("snapshots/demo/k/data.json.gz", ContentFile(b"keep"))

        self.migrate(AFTER)

        self.assertFalse(storage.exists(name))
        self.assertTrue(storage.exists(other))  # only site files go
        self.assertNotIn("apiary_site", connection.introspection.table_names())
        storage.delete(other)


def executor_leaf():
    executor = MigrationExecutor(connection)
    return executor.loader.graph.leaf_nodes()
