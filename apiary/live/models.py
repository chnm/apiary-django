"""
Live table definitions. They are kept in each project's own database, beside
the tables they describe, so a schema change and its new definition commit
in one transaction (PostgreSQL and SQLite both have transactional DDL).
Always use them with .using(alias); LiveRouter migrates them to every
project database and nowhere else.
"""
from django.db import models

TYPES = [
    ("text", "Text (one line)"),
    ("longtext", "Long text"),
    ("integer", "Integer"),
    ("float", "Number"),
    ("boolean", "Yes/no"),
    ("date", "Date"),
    ("datetime", "Date and time"),
    ("time", "Time"),
    ("json", "JSON"),
    ("url", "URL"),
    ("email", "Email"),
    ("relation", "Link to another table"),
]


class LiveTable(models.Model):
    name = models.CharField(max_length=48, unique=True)
    label = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "meta_live_table"
        ordering = ["name"]

    def __str__(self):
        return self.name


class LiveColumn(models.Model):
    table = models.ForeignKey(LiveTable, on_delete=models.CASCADE, related_name="columns")
    name = models.CharField(max_length=48)
    type = models.CharField(max_length=16, choices=TYPES)
    label = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    required = models.BooleanField(default=False)
    unique = models.BooleanField(default=False)
    max_length = models.PositiveIntegerField(null=True, blank=True)
    target = models.CharField(max_length=48, blank=True, help_text="The table a relation links to.")
    default = models.JSONField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "meta_live_column"
        ordering = ["table", "position", "id"]
        constraints = [models.UniqueConstraint(fields=["table", "name"], name="live_column_unique_name")]

    def __str__(self):
        return f"{self.table.name}.{self.name}"


class SchemaState(models.Model):
    """
    One row, changed with every schema change so each process rebuilds its
    models: version counts the changes, token tells apart databases that
    share a count (a restored or recreated one).
    """
    version = models.PositiveIntegerField(default=0)
    token = models.CharField(max_length=32, blank=True)

    class Meta:
        db_table = "meta_live_state"
