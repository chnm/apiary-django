"""
Schema changes to a project's live tables, as a list of operations:

    {"op": "create_table", "table": "letters", "label": "", "description": "", "columns": [column, ...]}
    {"op": "drop_table", "table": "letters"}
    {"op": "rename_table", "table": "letters", "to": "correspondence"}
    {"op": "add_column", "table": "letters", "column": column}
    {"op": "drop_column", "table": "letters", "column": "sent"}
    {"op": "rename_column", "table": "letters", "column": "sent", "to": "sent_on"}
    {"op": "alter_column", "table": "letters", "column": "sent", "changes": {"required": true, ...}}

where a column is {"name", "type", and optionally "label", "description",
"required", "unique", "max_length" (text), "target" (relation), "default"}.

plan() checks them against the current definitions and the data (a required
column needs a default when the table has rows, etc.) without touching
anything; apply() runs the DDL and stores the new definitions in one
transaction on the project's database. Column types never change in place:
add a column, copy the values, drop the old one.
"""
import copy

from django.core.exceptions import ValidationError
from django.db import connections
from django.db.models import Count
from django.db.models.functions import Length

from apiary.live import runtime
from apiary.live.models import TYPES, LiveColumn, LiveTable
from apiary.live.runtime import SchemaError, build_models, check_name, column_spec, db_table, make_field
from apiary.projects import code_models, project_alias

OPERATIONS = ("create_table", "drop_table", "rename_table", "add_column", "drop_column", "rename_column", "alter_column")
COLUMN_FIELDS = {"name", "type", "label", "description", "required", "unique", "max_length", "target", "default"}
ALTERABLE = {"label", "description", "required", "unique", "max_length", "default"}
TYPE_NAMES = {name for name, _ in TYPES}
MAX_COLUMNS = 100


def check_column(column, tables, table):
    """A complete column spec, checked; tables are the table names it may link to."""
    if not isinstance(column, dict):
        raise SchemaError("A column is an object with at least name and type.")
    unknown = set(column) - COLUMN_FIELDS
    if unknown:
        raise SchemaError(f"Unknown column keys: {', '.join(sorted(unknown))}.")
    spec = column_spec(column)
    check_name("Column", spec["name"])
    if spec["type"] not in TYPE_NAMES:
        raise SchemaError(f"Column {spec['name']!r}: type must be one of {', '.join(sorted(TYPE_NAMES))}.")
    if spec["type"] == "text":
        if not isinstance(spec["max_length"], int) or not 1 <= spec["max_length"] <= runtime.TEXT_LIMIT:
            raise SchemaError(f"Column {spec['name']!r}: max_length must be 1 to {runtime.TEXT_LIMIT}.")
    elif spec["max_length"] is not None:
        raise SchemaError(f"Column {spec['name']!r}: only text columns take max_length.")
    if spec["type"] == "relation":
        if spec["target"] not in tables:
            raise SchemaError(f"Column {spec['name']!r}: target must be one of this project's live tables.")
        if spec["unique"] or spec["default"] is not None:
            raise SchemaError(f"Column {spec['name']!r}: relations take neither unique nor default.")
    elif spec["target"]:
        raise SchemaError(f"Column {spec['name']!r}: only relation columns take a target.")
    if spec["default"] is not None:
        try:
            field = make_field(spec)
            field.clean(spec["default"], None)
        except ValidationError as error:
            raise SchemaError(f"Column {spec['name']!r}: default {spec['default']!r}: {' '.join(error.messages)}")
    return spec


class Planner:
    """Applies operations to a copy of the definitions, checking each against them and the data."""

    def __init__(self, project, specs, models):
        self.project = project
        self.tables = {table["name"]: copy.deepcopy(table) for table in specs}
        self.models = models  # the current tables, to look at their data
        self.code_names = set(code_models(project))
        # Each table's and column's name in the database now; None if this change makes it.
        self.origin = {name: name for name in self.tables}
        self.column_origin = {(table["name"], column["name"]): column["name"]
                              for table in specs for column in table["columns"]}

    def existing(self, table, column=None):
        """The current model (and field name) behind a table (and column), or None if new."""
        model = self.models.get(self.origin.get(table))
        if model is None:
            return None
        if column is None:
            return model
        name = self.column_origin.get((table, column))
        return (model, name) if name else None

    def rows(self, table):
        model = self.existing(table)
        return model.objects.using(project_alias(self.project)).count() if model else 0

    def table(self, name):
        if name not in self.tables:
            raise SchemaError(f"No live table {name!r} in {self.project}.")
        return self.tables[name]

    def column(self, table, name):
        for column in self.table(table)["columns"]:
            if column["name"] == name:
                return column
        raise SchemaError(f"No column {name!r} in {table}.")

    def links_to(self, name):
        return [f"{table['name']}.{column['name']}" for table in self.tables.values()
                for column in table["columns"] if column["type"] == "relation" and column["target"] == name]

    def new_table_name(self, name):
        check_name("Table", name)
        if name in self.tables or name in self.code_names:
            raise SchemaError(f"{self.project} already has a table {name!r}.")
        return name

    def apply(self, op):
        """Apply one operation; return it normalized and what it does to existing data."""
        if not isinstance(op, dict) or op.get("op") not in OPERATIONS:
            raise SchemaError(f"Each operation needs op: one of {', '.join(OPERATIONS)}.")
        kind = op["op"]
        return getattr(self, kind)(op)

    def create_table(self, op):
        name = self.new_table_name(op.get("table"))
        columns = op.get("columns") or []
        if len(columns) > MAX_COLUMNS:
            raise SchemaError(f"A table has at most {MAX_COLUMNS} columns.")
        self.tables[name] = {"name": name, "label": op.get("label") or "", "description": op.get("description") or "", "columns": []}
        self.origin[name] = None
        names = set()
        for column in columns:
            spec = check_column(column, self.tables, name)
            if spec["name"] in names:
                raise SchemaError(f"Column {spec['name']!r} appears twice.")
            names.add(spec["name"])
            self.tables[name]["columns"].append(spec)
        return {"op": "create_table", "table": name, "label": self.tables[name]["label"],
                "description": self.tables[name]["description"], "columns": self.tables[name]["columns"]}, \
            f"Creates table {name} with {len(columns)} columns."

    def drop_table(self, op):
        name = op.get("table")
        self.table(name)
        links = [link for link in self.links_to(name) if not link.startswith(f"{name}.")]
        if links:
            raise SchemaError(f"{', '.join(links)} link to {name}; drop or change them first.")
        rows = self.rows(name)
        del self.tables[name]
        self.origin.pop(name)
        return {"op": "drop_table", "table": name}, f"Drops table {name} and its {rows} rows."

    def rename_table(self, op):
        name, to = op.get("table"), op.get("to")
        table = self.table(name)
        self.new_table_name(to)
        table["name"] = to
        self.tables[to] = self.tables.pop(name)
        for other in self.tables.values():
            for column in other["columns"]:
                if column["type"] == "relation" and column["target"] == name:
                    column["target"] = to
        self.origin[to] = self.origin.pop(name)
        self.column_origin = {(to if table == name else table, column): origin
                              for (table, column), origin in self.column_origin.items()}
        return {"op": "rename_table", "table": name, "to": to}, f"Renames table {name} to {to}; its rows stay."

    def add_column(self, op):
        name = op.get("table")
        table = self.table(name)
        if len(table["columns"]) >= MAX_COLUMNS:
            raise SchemaError(f"A table has at most {MAX_COLUMNS} columns.")
        spec = check_column(op.get("column"), self.tables, name)
        if any(column["name"] == spec["name"] for column in table["columns"]):
            raise SchemaError(f"{name} already has a column {spec['name']!r}.")
        rows = self.rows(name)
        if spec["required"] and spec["default"] is None and rows:
            raise SchemaError(f"{name} has {rows} rows: a required column {spec['name']!r} needs a default for them.")
        if spec["unique"] and rows > 1 and spec["default"] is not None:
            raise SchemaError(f"{name} has {rows} rows: a unique column cannot give them all one default.")
        table["columns"].append(spec)
        self.column_origin[(name, spec["name"])] = None
        effect = f"Adds column {name}.{spec['name']} ({spec['type']})"
        if rows and spec["default"] is not None:
            effect += f", set to {spec['default']!r} on its {rows} rows"
        return {"op": "add_column", "table": name, "column": spec}, effect + "."

    def drop_column(self, op):
        name, column_name = op.get("table"), op.get("column")
        table = self.table(name)
        self.column(name, column_name)
        filled = self.filled(name, column_name)
        table["columns"] = [column for column in table["columns"] if column["name"] != column_name]
        self.column_origin.pop((name, column_name), None)
        return {"op": "drop_column", "table": name, "column": column_name}, \
            f"Drops column {name}.{column_name} and its {filled} non-empty values."

    def rename_column(self, op):
        name, column_name, to = op.get("table"), op.get("column"), op.get("to")
        column = self.column(name, column_name)
        check_name("Column", to)
        if any(other["name"] == to for other in self.table(name)["columns"]):
            raise SchemaError(f"{name} already has a column {to!r}.")
        column["name"] = to
        self.column_origin[(name, to)] = self.column_origin.pop((name, column_name), None)
        return {"op": "rename_column", "table": name, "column": column_name, "to": to}, \
            f"Renames column {name}.{column_name} to {to}; its values stay."

    def alter_column(self, op):
        name, column_name = op.get("table"), op.get("column")
        column = self.column(name, column_name)
        changes = op.get("changes") or {}
        if not isinstance(changes, dict) or not changes:
            raise SchemaError("alter_column needs changes, e.g. {\"required\": true}.")
        unknown = set(changes) - ALTERABLE
        if unknown:
            raise SchemaError(f"alter_column changes only {', '.join(sorted(ALTERABLE))}; to change "
                              f"{', '.join(sorted(unknown))}, add a new column, copy the values and drop this one.")
        altered = check_column({**column, **changes}, self.tables, name)
        problems = self.alter_problems(name, column_name, column, altered)
        if problems:
            raise SchemaError(f"Cannot alter {name}.{column_name}: {problems}")
        column.update(altered)
        return {"op": "alter_column", "table": name, "column": column_name, "changes": changes}, \
            f"Changes {name}.{column_name}: {', '.join(f'{key} = {value!r}' for key, value in changes.items())}."

    def filled(self, table, column):
        found = self.existing(table, column)
        if not found:
            return 0
        model, field = found
        return model.objects.using(project_alias(self.project)).exclude(**{f"{field}__isnull": True}).count()

    def alter_problems(self, table, column, before, after):
        found = self.existing(table, column)
        if not found:
            return None
        model, field = found
        rows = model.objects.using(project_alias(self.project))
        if after["required"] and not before["required"] and after["default"] is None:
            empty = rows.filter(**{f"{field}__isnull": True}).count()
            if empty:
                return f"{empty} rows have no value; give a default to fill them."
        if after["unique"] and not before["unique"]:
            duplicates = (rows.exclude(**{f"{field}__isnull": True}).values(field)
                          .annotate(n=Count("pk")).filter(n__gt=1).count())
            if duplicates:
                return f"{duplicates} values appear more than once."
        if after["type"] == "text" and (after["max_length"] or 0) < (before["max_length"] or 0):
            longer = rows.annotate(length=Length(field)).filter(length__gt=after["max_length"]).count()
            if longer:
                return f"{longer} values are longer than {after['max_length']} characters."
        return None


def plan(project, operations):
    """
    Check the operations against the project's live tables and data. Returns
    {"base": schema version, "steps": [{op, effect, before, after}], "after": specs}.
    """
    if not isinstance(operations, list) or not operations:
        raise SchemaError("Give a list of one or more operations.")
    alias = project_alias(project)
    specs = runtime.read_specs(alias)
    planner = Planner(project, specs, runtime.live_models(project))
    steps = []
    for op in operations:
        before = copy.deepcopy(list(planner.tables.values()))
        normalized, effect = planner.apply(op)
        steps.append({"op": normalized, "effect": effect, "before": before,
                      "after": copy.deepcopy(list(planner.tables.values()))})
    return {"base": runtime.schema_state(alias)[0], "steps": steps, "after": list(planner.tables.values())}


def run_step(editor, project, step):
    before = build_models(project, step["before"])
    after = build_models(project, step["after"])
    op = step["op"]
    kind, table = op["op"], op["table"]
    if kind == "create_table":
        editor.create_model(after[table])
    elif kind == "drop_table":
        editor.delete_model(before[table])
    elif kind == "rename_table":
        editor.alter_db_table(after[op["to"]], db_table(table), db_table(op["to"]))
    elif kind == "add_column":
        model = after[table]
        editor.add_field(model, model._meta.get_field(op["column"]["name"]))
    elif kind == "drop_column":
        model = before[table]
        editor.remove_field(model, model._meta.get_field(op["column"]))
    else:  # rename_column, alter_column
        old = before[table]._meta.get_field(op["column"])
        new = after[table]._meta.get_field(op.get("to") or op["column"])
        editor.alter_field(before[table], old, new)


def sql(project, steps):
    """The SQL the steps would run, for review; None where the backend cannot show it."""
    connection = connections[project_alias(project)]
    try:
        with connection.schema_editor(collect_sql=True, atomic=False) as editor:
            for step in steps:
                run_step(editor, project, step)
        return [statement for statement in editor.collected_sql]
    except Exception:
        return None


def write_specs(alias, specs):
    """Replace the stored definitions with these."""
    LiveTable.objects.using(alias).all().delete()
    for table in specs:
        row = LiveTable.objects.using(alias).create(
            name=table["name"], label=table.get("label") or "", description=table.get("description") or "")
        LiveColumn.objects.using(alias).bulk_create([
            LiveColumn(table=row, position=position, **{key: column_spec(column)[key] for key in runtime.COLUMN_KEYS})
            for position, column in enumerate(table["columns"])
        ])


def apply(project, planned):
    """
    Run a plan's DDL and store its definitions, in one transaction on the
    project's database. Refuses if the schema moved since it was planned.
    Returns the new schema version.
    """
    alias = project_alias(project)
    if runtime.schema_state(alias)[0] != planned["base"]:
        raise SchemaError("The tables changed since this was planned; plan it again.")
    connection = connections[alias]
    with connection.schema_editor(atomic=True) as editor:
        for step in planned["steps"]:
            run_step(editor, project, step)
        write_specs(alias, planned["after"])
        version = runtime.bump(alias)
    runtime.forget(project)
    return version


def replace(editor, project, specs):
    """
    Make the project's live tables exactly these specs, empty: drop every
    live table and create these. For restoring a snapshot: run it in the
    caller's schema_editor(atomic=True), which loads the rows in the same
    transaction. Returns the new models.
    """
    alias = project_alias(project)
    for model in build_models(project, runtime.read_specs(alias)).values():
        editor.delete_model(model)  # CASCADE on PostgreSQL; SQLite's checks are off here
    wanted = build_models(project, specs)
    for model in wanted.values():
        editor.create_model(model)
    write_specs(alias, specs)
    runtime.bump(alias)
    runtime.forget(project)
    return wanted
