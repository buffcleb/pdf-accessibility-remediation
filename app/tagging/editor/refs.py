"""Tags a save makes, named by its later edits before they exist (#692).

An edit that makes tags can carry an "id" (made up by the tag manager). A later edit of the same save names one of
those tags as {"made": "<id>", "group": k}, wherever it would name a tag by its number in the tag map: the tag k of
the edit (an area's new tags are one edit making several; the others make one, group 0). So a tag made from an area
can be moved, nested into, merged, deleted or given alt text before the save, like any other.

Such an edit runs on the tree as earlier edits left it: the tag index is built again from the file as it is then
(IntegrityChecker.rewalk), and every tag it names is found by its object. So are the tags the edits after it name,
by number: each is noted before any edit runs (made indirect when it isn't, so it can be found again)."""
from __future__ import annotations

from collections.abc import Callable

from pikepdf import Array, Dictionary

from app.tagging.editor._ctx import _MARK, OPERATIONS, EditError, _Ctx, _elem, _parent_obj, _same
from app.tagging.tree import is_elem
from app.tagging.tree import items as _items

# The edits that make tags, and how many each makes
def _cells(op: dict) -> int:
    return sum(len(r) for r in op.get("rows") or [] if isinstance(r, list))


# The edits that make tags, and how many each can make (#692). A list or table edit's are its tags in the order the
# tag manager shows them: a list built from lines, each row's tag and a list where one opens; a table, the table,
# its caption, then each row and its cells (a rebuilt table's old caption put back as a paragraph first, the
# paragraphs taken out of it last); a list made of tags, the new list.
MAKERS: dict[str, Callable[[dict], int]] = {
    "tag_artifacts": lambda op: 1, "split": lambda op: 1, "take_pieces": lambda op: 1, "cut_area": lambda op: 1,
    "regroup": lambda op: len(op.get("groups") or []),
    "make_list": lambda op: 1,
    "build_list": lambda op: 2 * len(op.get("rows") or []),
    "build_table": lambda op: 1 + len(op.get("rows") or []) + _cells(op),
    "rebuild_table": lambda op: 3 + len(op.get("rows") or []) + _cells(op) + len(op.get("out") or []),
}
MAX_ID = 40
_PLACEHOLDER = 1_000_000_000  # what a made tag stands as for the shape checks, which want numbers


def is_made(v) -> bool:
    return (isinstance(v, dict) and set(v) == {"made", "group"} and isinstance(v["made"], str)
            and isinstance(v["group"], int) and not isinstance(v["group"], bool))


def map_refs(op: dict, fn: Callable) -> dict:
    """A copy of op with every tag it names (a number in the tag map, or a made tag) passed through fn."""
    out, name = dict(op), op.get("op")
    o = OPERATIONS.get(name)
    if o is not None and o.element and "element" in op:
        out["element"] = fn(op["element"])
    each = lambda xs: [fn(x) for x in xs] if isinstance(xs, list) else xs  # noqa: E731
    for key in ("target", "note", "caption"):
        if op.get(key) is not None and (key != "target" or name in ("move", "set_toc_target")):
            out[key] = fn(op[key])
    for key in ("absorb", "headers"):
        if key in op:
            out[key] = each(op[key])
    cell = lambda r: [fn(r[0]), r[1]] if isinstance(r, list) else fn(r)  # noqa: E731  (a cut cell's part)
    rows = op.get("rows")
    if isinstance(rows, list):
        if name == "build_list":
            out["rows"] = [{**r, "el": fn(r["el"])} if isinstance(r, dict) and "el" in r else r for r in rows]
        elif name in ("build_table", "rebuild_table"):
            key = "from" if name == "rebuild_table" else "parts"
            part = cell if key == "from" else (  # noqa: E731
                lambda p: {**p, "el": fn(p["el"])} if isinstance(p, dict) and "el" in p else p)
            out["rows"] = [[{**c, key: [part(x) for x in c[key]]} if isinstance(c, dict) and isinstance(c.get(key), list)
                            else c for c in row] if isinstance(row, list) else row for row in rows]
    if name == "rebuild_table":
        if isinstance(op.get("out"), list):
            out["out"] = [{**p, "from": [cell(x) for x in p["from"]]} if isinstance(p, dict)
                          and isinstance(p.get("from"), list) else p for p in op["out"]]
        if isinstance(op.get("cuts"), dict):  # by number only: a cell is cut in the file as it was
            out["cuts"] = {(str(fn(int(k))) if str(k).isdigit() else k): v for k, v in op["cuts"].items()}
    return out


def names_made(op: dict) -> bool:
    found = []
    map_refs(op, lambda v: found.append(v) or v)
    return any(is_made(v) for v in found)


def check(ops: list[dict]) -> list[dict]:
    """The edits as the shape checks see them: each made tag a number of its own past any real one. A made tag has to
    be one an earlier edit of the save makes, and an id names one edit."""
    made: dict[str, int] = {}
    numbers: dict[tuple[str, int], int] = {}
    out = []
    for n, op in enumerate(ops, 1):
        if not isinstance(op, dict):
            out.append(op)
            continue
        if "id" in op:
            if op.get("op") not in MAKERS or not isinstance(op["id"], str) or not 1 <= len(op["id"]) <= MAX_ID:
                raise EditError(f"Edit {n}: only an edit that makes a tag can be named.")
            if op["id"] in made:
                raise EditError(f"Edit {n}: two edits have the same name.")

        def number(v, n=n):
            if not isinstance(v, dict):
                return v
            if not is_made(v) or v["made"] not in made or not 0 <= v["group"] < made[v["made"]]:
                raise EditError(f"Edit {n}: it names a new tag that no earlier change in this save makes.")
            return numbers.setdefault((v["made"], v["group"]), _PLACEHOLDER + len(numbers))
        out.append(map_refs(op, number))
        if "id" in op:
            made[op["id"]] = MAKERS[op["op"]](op)
    return out


def pin(ctx: _Ctx, op: dict, n: int) -> None:
    """Note the object of each tag an edit names by number, before any edit runs, checking the type it expects."""
    o = OPERATIONS[op["op"]]
    if not o.element:
        return
    expect = {"element": op.get("expect_role"), "target": op.get("expect_target_role")}

    def note(v, key=None):
        if is_made(v):
            return v
        e = _elem(ctx, v, expect.get(key), n)
        if not e.obj.is_indirect:
            _make_indirect(ctx, e)
        ctx.pinned[v] = e.obj
        return v
    note(op["element"], "element")
    if op["op"] == "move":
        note(op["target"], "target")
    map_refs(op, note)


def _make_indirect(ctx: _Ctx, e) -> None:
    """A tag written inside its parent becomes an object of its own, in the same place, so it can be found again."""
    parent = _parent_obj(ctx, e)
    e.obj[_MARK] = True
    kids = _items(parent.get("/K"))
    at = next((i for i, k in enumerate(kids) if _same(k, e.obj)), None)
    del e.obj[_MARK]
    if at is None:
        return
    obj = ctx.pdf.make_indirect(Dictionary(e.obj))
    kids[at] = obj
    parent.K = Array(kids)
    for k in _items(obj.get("/K")):
        if is_elem(k):
            k.P = obj
    e.obj = obj


def now(ctx: _Ctx, v, n: int) -> int:
    """Where a tag an edit names is in the tag index as it is now."""
    if is_made(v):
        got = ctx.named.get(v["made"]) or []
        obj = got[v["group"]] if v["group"] < len(got) else None
        what = "a new tag it names"
    else:
        obj, what = ctx.pinned.get(v), f"element {v}"
    i = _index_of(ctx, obj) if isinstance(obj, Dictionary) else None
    if i is None:
        raise EditError(f"Edit {n}: {what} was removed by an earlier change in this save.")
    return i


def _index_of(ctx: _Ctx, obj) -> int | None:
    key = (id(ctx.elems), len(ctx.elems))
    if ctx.where is None or ctx.where[0] != key:
        ctx.where = (key, {e.obj.objgen: e.index for e in reversed(ctx.elems) if e.obj.is_indirect})
    return ctx.where[1].get(obj.objgen)


def reindex(ctx: _Ctx) -> None:
    """The tag index built again from the tree as earlier edits left it."""
    ctx.checker.rewalk()
    ctx.elems = ctx.checker.index.elements
