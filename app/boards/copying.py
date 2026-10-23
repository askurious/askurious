"""Copying dashboards with a duplicated survey."""
from app.boards.base import FIELD, item_ref
from app.boards.tiles import is_text
from app.models import Board


# --- copies ---------------------------------------------------------------------------------------------


def copy_to(source, new, items, quotas):
    """The dashboards of `source` for its copy `new` (app/duplicate.py): the tiles point at the questions and
    quotas of the copy; links are never copied."""
    for board in source.boards:
        tiles = []
        for tile in board.tiles or []:
            key = remap(tile.get("key") if isinstance(tile, dict) else None, items, quotas)
            if key:
                tiles.append({**tile, "key": key, "by": remap_split(tile.get("by"), items, quotas) or ""})
        settings = {k: v for k, v in (board.settings or {}).items() if k != "end_share"}  # names a link of the original
        new.boards.append(Board(name=board.name, tiles=tiles, settings=settings, position=board.position))


def remap_split(key, items, quotas):
    """The key a tile is split by (split_vars) on other ids; None if what it names has no twin."""
    kind, _, ref = str(key or "").partition(":")
    if kind == "item":
        return remap(key, items, quotas)
    if kind in ("klassen", "mc"):
        item_id, _, rest = ref.partition(":")
        twin = items.get(int(item_id)) if item_id.isascii() and item_id.isdigit() and len(item_id) < 12 else None
        return f"{kind}:{twin}" + (f":{rest}" if rest else "") if twin else None
    if kind in ("rand", "quota") and ref and ":" not in ref:  # by variable name and quota code, which copies keep
        return key
    return None


def remap(key, items, quotas):
    """The key of a tile on other ids ({old: new}); None if what it names has no twin."""
    kind, _, ref = str(key or "").partition(":")
    if kind == "text":
        return key if is_text(key) else None
    if kind == "field":
        return key if ref in FIELD else None
    if kind == "item":  # a pass of a loop keeps its row: copies and files carry the loop's table with its ids
        item_id, row = item_ref(key)
        twin = items.get(item_id)
        return (f"item:{twin}@{row}" if row else f"item:{twin}") if twin else None
    if kind == "quota" and ref.isascii() and ref.isdigit() and len(ref) < 12:
        twin = quotas.get(int(ref))
        return f"{kind}:{twin}" if twin else None
    return None
