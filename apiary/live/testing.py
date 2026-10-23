"""Test support: TransactionTestCase flushes only the tables Django's registry knows, never live ones."""
from django.db import connections

from apiary.live import runtime
from apiary.projects import PROJECT_APPS, PROJECT_DATABASES


def drop_live_tables():
    for project in PROJECT_APPS:
        connection = connections[PROJECT_DATABASES[project]]
        with connection.cursor() as cursor:
            for table in connection.introspection.table_names(cursor):
                if table.startswith("live_"):
                    cursor.execute(f"DROP TABLE {connection.ops.quote_name(table)}")
        runtime.forget(project)
