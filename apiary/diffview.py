"""
changes.diff() made readable for people who never see the data as JSON: each
value as text in pieces, the pieces a change touched marked, and spaces at the
edges of a value shown, since an extra space is a change nobody could see.
"""
import difflib
import re

from apiary.projects import project_models

TOKENS = re.compile(r"(\s+)")


def text(value):
    if value is None or value == "":
        return None
    if value is True or value is False:
        return "Yes" if value else "No"
    return str(value)


def pieces(value, marked=(), everything=False):
    """[(text, marked, space)]: words and spaces, spaces at either edge shown as visible marks."""
    tokens = [t for t in TOKENS.split(value) if t]
    result = []
    for index, token in enumerate(tokens):
        edge = index in (0, len(tokens) - 1) and token.isspace()
        result.append((token, everything or index in marked, edge))
    return result


def word_diff(before, after):
    """Pieces of both values, marking only the words that differ."""
    old, new = [t for t in TOKENS.split(before) if t], [t for t in TOKENS.split(after) if t]
    removed, added = set(), set()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag != "equal":
            removed.update(range(i1, i2))
            added.update(range(j1, j2))
    return pieces(before, removed), pieces(after, added)


def cell(value, marked):
    shown = text(value)
    return {"empty": shown is None, "pieces": pieces(shown, everything=marked) if shown else []}


def column_names(project, table):
    """Column labels from the table's own field names, where the table still exists."""
    model = project_models(project).get(table)
    if model is None:
        return {}
    return {f.name: str(f.verbose_name)[:1].upper() + str(f.verbose_name)[1:] for f in model._meta.concrete_fields}


def readable(change, found):
    names = column_names(change.project, found["table"])
    title = lambda column: names.get(column) or column.replace("_", " ").capitalize()
    rows = []
    for item in found["items"]:
        fields = []
        for field in item["fields"]:
            before, after = field["before"], field["after"]
            edited = field["changed"] and found["action"] == "update"
            if edited and isinstance(before, str) and isinstance(after, str) and before and after:
                old, new = word_diff(before, after)
                left, right = {"empty": False, "pieces": old}, {"empty": False, "pieces": new}
            else:
                left, right = cell(before, edited), cell(after, edited)
            fields.append({"name": title(field["column"]), "changed": field["changed"], "before": left, "after": right})
        if found["action"] == "update":
            shown, folded = [f for f in fields if f["changed"]], [f for f in fields if not f["changed"]]
        else:  # a whole row added or removed: its empty fields say nothing
            empty = lambda f: f["before"]["empty"] and f["after"]["empty"]
            shown, folded = [f for f in fields if not empty(f)], [f for f in fields if empty(f)]
        rows.append({**item, "fields": shown, "unchanged": folded})
    summary = [{**move, "name": title(move["column"]),
                **({} if "other" in move else {"before": cell(move["before"], False), "after": cell(move["after"], False)})}
               for move in found["summary"]]
    spaces = any(space for side in ("before", "after") for row in rows for field in row["fields"] + row["unchanged"]
                 for _, _, space in field[side]["pieces"]) or any(
        space for move in summary if "other" not in move for side in ("before", "after") for _, _, space in move[side]["pieces"])
    return {**found, "items": rows, "summary": summary, "spaces": spaces}
