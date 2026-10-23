"""Replace restricted instrument wording at the public boundary, without changing a shared cached state."""
import hashlib

from app import constructs, export
from app.boards.base import item_ref
from app.boards.tiles import _median, split_vars


def _scale(tile, item, suffix):
    """The numeric result, identified by the same variables as the data export; never question/anchor texts."""
    safe = {k: tile[k] for k in ("key", "view", "w", "x", "pct", "n", "by", "shape", "few", "first", "ordinal",
                                "merged", "unsplit", "sample") if k in tile}
    safe.update(title=item.code + suffix, code=item.code + suffix)
    # The grouping question is independent of the restricted instrument. Its labels are not instrument wording.
    if "by_title" in tile:
        safe["by_title"] = tile["by_title"]
    first = tile.get("first", 1)
    levels = [str(first + n) for n in range(len(tile.get("levels") or []))]
    if "levels" in tile:
        safe["levels"] = levels

    def row(own, n, split=False):
        kept = {k: own[k] for k in ("n", "few", "reverse", "mean", "sd", "segs", "means") if k in own}
        variable = own.get("variable") or f"{item.code}_{n + 1}"
        kept.update(variable=variable + suffix, label=variable + suffix)
        if "median" in own:
            k = len(levels)
            groups = [(s["a"], s["b"], s["count"]) for s in own.get("segs", [])]
            order = [(k - 1 - b, k - 1 - a, count) for a, b, count in reversed(groups)] if own.get("reverse") else groups
            kept["median"] = _median(order, levels[::-1] if own.get("reverse") else levels, first)[1]
        if split and "texts" in own:
            # means contains the displayed median's numeric position for ordinal comparisons, without anchors.
            kept["texts"] = [None if value is None else str(value) for value in own.get("means", [])]
        return kept

    if "rows" in tile:
        safe["rows"] = [row(r, n) for n, r in enumerate(tile["rows"])]
    if tile.get("split"):
        split = tile["split"]
        safe["split"] = {k: split[k] for k in ("groups", "merged_groups", "ordinal", "lo", "hi") if k in split}
        safe["split"]["rows"] = [row(r, n, split=True) for n, r in enumerate(split.get("rows", []))]
        safe["split"]["ends"] = [str(split.get("lo", 1)), str(split.get("hi", 1))]
    return safe


def without_wording(board, state):
    """Public HTML, iframe and polling use this projection; member/participant views keep their own wording.

    Only the existing NOT_SHARED policy applies. No licence judgement is inferred from names or text similarity.
    The state hash changes as well so an already open public page replaces its former, unrestricted HTML.
    """
    items = {i.id: i for b in board.survey.blocks for i in b.items if constructs.wording_withheld(i)}
    if not items:
        return state
    grouping = split_vars(board.survey, state.get("lang")) if any(t.get("by") for t in state.get("tiles") or []) else {}
    tiles = []
    for tile in state.get("tiles") or []:
        key = tile.get("key") or ""
        item_id, row_id = item_ref(key) if key.startswith("item:") else (None, None)
        item = items.get(item_id)
        by = grouping.get(tile.get("by"))
        if by is not None and by.item is not None and by.item.id in items:
            tile = {**tile, "by_title": by.code}
            if tile.get("split"):
                split = tile["split"]
                tile["split"] = {**split, "groups": [{**group, "label": f"{by.code}:group_{group.get('id', n + 1)}"}
                                                     for n, group in enumerate(split.get("groups", []))]}
        if item is None:
            tiles.append(tile)
            continue
        number = next((n for n, rid in export._loop_rows(item) or [] if rid == row_id), None)
        suffix = f"_{number}" if number is not None else ""
        if tile.get("shape") == "scale":
            tiles.append(_scale(tile, item, suffix))
        else:
            # All currently restricted instruments are scales. If a new type is restricted, fail closed until
            # it has an explicit numeric projection, rather than letting its unfamiliar text fields through.
            tiles.append({k: tile[k] for k in ("key", "view", "w", "x", "pct", "n", "shape") if k in tile}
                         | {"title": item.code + suffix, "code": item.code + suffix, "few": True})
    key = hashlib.sha256((str(state.get("key", "")) + "|public-wording:1|" + ",".join(map(str, sorted(items)))).encode()).hexdigest()
    return {**state, "key": key, "tiles": tiles}
