"""
Filters agents and APIs may use on a project's tables: Django field lookups,
checked so a path never leaves the table's own project (no users, tokens or
another project's rows).
"""
from django.core.exceptions import FieldDoesNotExist

LOOKUPS = {
    "exact", "iexact", "contains", "icontains", "in", "gt", "gte", "lt", "lte",
    "startswith", "istartswith", "endswith", "iendswith", "range", "isnull",
    "year", "month", "day",
}


class QueryError(ValueError):
    pass


def check_path(model, path, lookups=False):
    """Allow only fields of the model's own project, so a filter or grouping cannot reach outside it."""
    parts = path.lstrip("-").split("__")
    current = model
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        try:
            field = current._meta.get_field(part)
        except FieldDoesNotExist:
            if lookups and last and i > 0 and part in LOOKUPS:
                return
            raise QueryError(f"Unknown field {part!r} on {current._meta.model_name}.")
        if field.is_relation:
            if field.related_model._meta.app_label != model._meta.app_label:
                raise QueryError(f"{part!r} leaves the {model._meta.app_label} project.")
            current = field.related_model
        elif not last:
            rest = parts[i + 1:]
            if lookups and len(rest) == 1:
                if rest[0] in LOOKUPS:
                    return
                raise QueryError(f"Unsupported lookup {rest[0]!r} on {part!r}; one of {', '.join(sorted(LOOKUPS))}.")
            raise QueryError(f"{part!r} on {current._meta.model_name} is not a relation.")


def filtered(queryset, filters):
    for path in filters or {}:
        check_path(queryset.model, path, lookups=True)
    return queryset.filter(**(filters or {}))
