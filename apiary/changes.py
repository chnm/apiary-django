"""
Changes to a project's tables (schema) and records, each recorded as a Change:
who, through what (an MCP client, the API, the admin), when, why, what it
did, and enough to undo it.

- Records: create applies at once. Update and delete apply at once up to
  DIRECT_LIMIT rows; more become a proposal, applied with apply() after
  review, and verified against the rows as they were proposed. Every records
  change can be reverted while its rows are as it left them.
- Tables: always a proposal (apiary.live.schema plans it, with its effects
  and SQL), applied with apply(). Applying snapshots the project first, so
  restoring that snapshot undoes it.

Roles (apiary.projects): editors create and update records; admins delete
records and change tables.
"""
import json
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apiary import projectdata, queries
from apiary.live import schema
from apiary.live.runtime import SchemaError
from apiary.models import Change
from apiary.projects import has_role, project_alias, project_models

DIRECT_LIMIT = 25
RECORDS_MAX = 5000
CREATE_MAX = 500
PROPOSAL_LIFETIME = timedelta(days=1)
NEEDS = {"create": "editor", "update": "editor", "delete": "admin"}


class ChangeError(Exception):
    pass


def require(user, project, needed):
    if not has_role(user, project, needed):
        raise ChangeError(f"This needs the {needed} role in {project}.")


def table_model(project, table):
    models = project_models(project)
    if table not in models:
        raise ChangeError(f"No table {table!r} in {project}; one of {', '.join(models) or 'none yet'}.")
    return models[table]


def jsonable(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def row(obj):
    """A row as plain JSON: id, then each column by name (relations as the linked id)."""
    return jsonable({"id": obj.pk, **{f.name: getattr(obj, f.attname) for f in obj._meta.concrete_fields if not f.primary_key}})


def assign(obj, values):
    """Set columns from a {name: value} dict, relations by id."""
    for name, value in values.items():
        try:
            field = obj._meta.get_field(name)
        except Exception:
            raise ChangeError(f"No column {name!r} in {obj._meta.model_name}.")
        if field.primary_key or not field.concrete:
            raise ChangeError(f"{name!r} cannot be set.")
        setattr(obj, field.attname, value)


def save(obj, alias):
    try:
        obj.full_clean()
    except ValidationError as error:
        raise ChangeError(f"Row {obj.pk or '(new)'}: " + "; ".join(
            f"{field}: {' '.join(messages)}" for field, messages in error.message_dict.items()))
    obj.save(using=alias)


def select(model, alias, ids=None, filters=None):
    if (ids is None) == (filters is None):
        raise ChangeError("Give either ids or filters.")
    queryset = model._default_manager.using(alias).order_by("pk")
    if ids is not None:
        return queryset.filter(pk__in=ids)
    try:
        return queries.filtered(queryset, filters)
    except (queries.QueryError, ValidationError, ValueError, TypeError) as error:
        raise ChangeError(str(error))


def record(project, kind, summary, *, user, via, reason="", **fields):
    return Change.objects.create(project=project, kind=kind, summary=summary[:300], reason=reason or "",
                                 proposed_by=user, proposed_via=via, **fields)


def decided(change, status, *, user, via, **fields):
    change.status = status
    change.decided_by, change.decided_via, change.decided_at = user, via, timezone.now()
    for name, value in fields.items():
        setattr(change, name, value)
    change.save()
    return change


# Records

def create_records(user, project, table, rows, *, via, reason=""):
    require(user, project, "editor")
    model = table_model(project, table)
    if not isinstance(rows, list) or not rows or len(rows) > CREATE_MAX:
        raise ChangeError(f"Give 1 to {CREATE_MAX} rows, each a {{column: value}} object.")
    alias = project_alias(project)
    op = {"op": "create", "table": table, "rows": rows}
    change = record(project, "records", f"Add {len(rows)} rows to {table}", user=user, via=via, reason=reason,
                    operations=[op], preview={"rows": len(rows)})
    try:
        with transaction.atomic(using=alias):
            created = []
            for values in rows:
                if not isinstance(values, dict):
                    raise ChangeError("Each row is a {column: value} object.")
                obj = model()
                assign(obj, values)
                save(obj, alias)
                created.append(row(obj))
    except (ChangeError, IntegrityError) as error:
        decided(change, "failed", user=user, via=via, error=str(error))
        raise ChangeError(str(error)) from error
    return decided(change, "applied", user=user, via=via, after=created)


def change_records(user, project, table, op, *, via, ids=None, filters=None, values=None, reason=""):
    """Update (with values) or delete the selected rows, or propose it when it is more than DIRECT_LIMIT rows."""
    require(user, project, NEEDS[op])
    model = table_model(project, table)
    alias = project_alias(project)
    if op == "update":
        if not isinstance(values, dict) or not values:
            raise ChangeError("Give values: {column: new value}.")
        assign(model(), values)  # checks the names
    selected = select(model, alias, ids=ids, filters=filters)
    count = selected.count()
    if not count:
        raise ChangeError("No rows match.")
    if count > RECORDS_MAX:
        raise ChangeError(f"{count} rows match; one change takes at most {RECORDS_MAX}.")
    before = [row(obj) for obj in selected]
    operation = {"op": op, "table": table, "ids": [r["id"] for r in before]}
    if op == "update":
        operation["values"] = jsonable(values)
    verb = "Update" if op == "update" else "Delete"
    change = record(project, "records", f"{verb} {count} rows in {table}", user=user, via=via, reason=reason,
                    operations=[operation], before=before,
                    preview={"rows": count, "sample": before[:5], **({"values": operation["values"]} if op == "update" else {})})
    if count > DIRECT_LIMIT:
        change.expires_at = timezone.now() + PROPOSAL_LIFETIME
        change.save(update_fields=["expires_at"])
        return change
    return apply(change, user, via=via)


def apply_records(change, user, via):
    operation = change.operations[0]
    require(user, change.project, NEEDS[operation["op"]])
    model = table_model(change.project, operation["table"])
    alias = project_alias(change.project)
    snapshot = None
    if len(change.before) > DIRECT_LIMIT:
        snapshot = projectdata.take_snapshot(change.project, kind="pre-change", user=user, label=f"Before change #{change.pk}")
    try:
        with transaction.atomic(using=alias):
            current = list(model._default_manager.using(alias).select_for_update().filter(pk__in=operation["ids"]).order_by("pk"))
            if [row(obj) for obj in current] != change.before:
                raise ChangeError("These rows changed since this was proposed; propose it again.")
            after = []
            if operation["op"] == "update":
                for obj in current:
                    assign(obj, operation["values"])
                    save(obj, alias)
                    after.append(row(obj))
            else:
                try:
                    model._default_manager.using(alias).filter(pk__in=operation["ids"]).delete()
                except ProtectedError as error:
                    raise ChangeError(f"Other rows link to these and must keep them: {error.args[0]}")
    except (ChangeError, IntegrityError) as error:
        decided(change, "failed", user=user, via=via, error=str(error), snapshot=snapshot)
        raise ChangeError(str(error)) from error
    return decided(change, "applied", user=user, via=via, after=after, snapshot=snapshot)


def revert(change, user, *, via, reason=""):
    """Undo an applied records change, if its rows are still as it left them."""
    if change.kind != "records" or change.status != "applied":
        raise ChangeError("Only applied records changes can be reverted; restore a snapshot to undo a table change.")
    operation = change.operations[0]
    if operation["op"] == "revert":
        raise ChangeError("Revert the original change's revert by applying the change again.")
    require(user, change.project, NEEDS[operation["op"]])
    model = table_model(change.project, operation["table"])
    alias = project_alias(change.project)
    ids = [r["id"] for r in (change.after if operation["op"] != "delete" else change.before)]
    undo = record(change.project, "records", f"Revert change #{change.pk}", user=user, via=via, reason=reason,
                  operations=[{"op": "revert", "of": change.pk, "table": operation["table"]}], reverts=change)
    try:
        with transaction.atomic(using=alias):
            current = [row(obj) for obj in model._default_manager.using(alias).select_for_update().filter(pk__in=ids).order_by("pk")]
            if operation["op"] == "delete":
                if current:
                    raise ChangeError(f"Rows {', '.join(str(r['id']) for r in current)} exist again; not recreating them.")
                for values in change.before:
                    obj = model(pk=values["id"])
                    assign(obj, {k: v for k, v in values.items() if k != "id"})
                    save(obj, alias)
            else:
                if current != change.after:
                    raise ChangeError("The rows changed since; not reverting over newer edits.")
                if operation["op"] == "create":
                    model._default_manager.using(alias).filter(pk__in=ids).delete()
                else:
                    for values, obj in zip(change.before, model._default_manager.using(alias).filter(pk__in=ids).order_by("pk")):
                        assign(obj, {k: v for k, v in values.items() if k != "id"})
                        save(obj, alias)
    except (ChangeError, ProtectedError, IntegrityError) as error:
        decided(undo, "failed", user=user, via=via, error=str(error))
        raise ChangeError(str(error)) from error
    change.status = "reverted"
    change.save(update_fields=["status"])
    return decided(undo, "applied", user=user, via=via)


# Tables

def propose_schema(user, project, operations, *, via, reason):
    require(user, project, "admin")
    if not (reason or "").strip():
        raise ChangeError("Say why: reason is required for table changes.")
    try:
        planned = schema.plan(project, operations)
    except SchemaError as error:
        raise ChangeError(str(error))
    effects = [step["effect"] for step in planned["steps"]]
    summary = effects[0] if len(effects) == 1 else f"{len(effects)} table changes: {effects[0]} ..."
    return record(project, "schema", summary, user=user, via=via, reason=reason,
                  operations=[step["op"] for step in planned["steps"]],
                  preview={"effects": effects, "sql": schema.sql(project, planned["steps"]), "plan": planned},
                  expires_at=timezone.now() + PROPOSAL_LIFETIME)


def apply_schema(change, user, via):
    require(user, change.project, "admin")
    snapshot = projectdata.take_snapshot(change.project, kind="pre-change", user=user, label=f"Before change #{change.pk}")
    try:
        version = schema.apply(change.project, change.preview["plan"])
    except Exception as error:
        decided(change, "failed", user=user, via=via, error=str(error), snapshot=snapshot)
        raise ChangeError(f"Not applied: {error}")
    return decided(change, "applied", user=user, via=via, snapshot=snapshot, schema_version=version)


def apply(change, user, *, via):
    """Apply a proposal."""
    if change.status != "proposed":
        raise ChangeError(f"Change #{change.pk} is {change.status}, not proposed.")
    if change.expires_at and change.expires_at < timezone.now():
        raise ChangeError(f"Change #{change.pk} expired; propose it again.")
    if change.kind == "schema":
        return apply_schema(change, user, via)
    return apply_records(change, user, via)


def reject(change, user, *, via):
    if change.status != "proposed":
        raise ChangeError(f"Change #{change.pk} is {change.status}, not proposed.")
    require(user, change.project, "admin" if change.kind == "schema" else NEEDS[change.operations[0]["op"]])
    return decided(change, "rejected", user=user, via=via)


def describe(change):
    """A change as an agent or API sees it."""
    return {
        "id": change.pk, "project": change.project, "kind": change.kind, "status": change.status,
        "summary": change.summary, "reason": change.reason, "operations": change.operations,
        "preview": {key: value for key, value in change.preview.items() if key != "plan"},
        "proposed_by": getattr(change.proposed_by, "username", None), "proposed_via": change.proposed_via,
        "proposed_at": change.proposed_at.isoformat(),
        "expires_at": change.expires_at.isoformat() if change.expires_at else None,
        "decided_by": getattr(change.decided_by, "username", None), "decided_via": change.decided_via,
        "decided_at": change.decided_at.isoformat() if change.decided_at else None,
        "snapshot": change.snapshot_id, "schema_version": change.schema_version, "error": change.error or None,
        "rows_after": len(change.after) if change.kind == "records" else None,
    }


def log_records(user, project, table, op, *, before=(), after=(), via):
    """Record a records change made elsewhere (the API), already applied."""
    rows = list(after or before)
    summary = f"Add {len(rows)} rows to {table}" if op == "create" else f"{op.title()} {len(rows)} rows in {table}"
    return record(project, "records", summary, user=user, via=via, status="applied",
                  operations=[{"op": op, "table": table, "ids": [r["id"] for r in rows]}],
                  before=list(before), after=list(after), decided_by=user, decided_via=via, decided_at=timezone.now())
