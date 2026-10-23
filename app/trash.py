"""Trash for deleted questions: kept TRASH_DAYS days, then removed for good. A question with real answers deleted in
a survey opened for editing again stays until someone deletes it there for good (audit 2026-10-18, FUNC-05): its
answers are still stored, and the entry is what brings them back into the data set.

Deleting a question (or a block with questions) stores a copy of each item here; the
editor shows them in a light red block below the other blocks, where they can be put
back or deleted for good. Image files stay on disk while an item is in the trash.
Page breaks are not kept (they are recreated with one click). The consent is kept: its text and
key facts are often agreed with an ethics committee. It comes back as its own block at the start."""
from datetime import timedelta

from app import media
from app.extensions import db
from app.models import Asset, Block, Item, TrashedItem, utcnow

TRASH_DAYS = 7
SKIP = ("page_break",)

ASSET_FIELDS = ("key", "filename", "source", "prompt", "model", "status", "style", "rights")


def put(item, answered=False):
    """Move `item` into the trash (the caller removes it from its block and commits). answered: it has real answers
    (answer_lock.fixed); the entry then does not run out."""
    survey = item.block.survey
    if item.type in SKIP:
        for a in item.assets:
            media.delete_after_commit(survey, a.filename)  # a failed commit keeps them (code review 2026-10-15, D5)
        return None
    entry = TrashedItem(data={
        "id": item.id,  # answers are stored under it: the question comes back under the same id
        "type": item.type, "code": item.code, "answer_mode": item.answer_mode,
        "config": item.config or {}, "texts": item.texts or {},
        "block_id": item.block_id, "position": item.position,
        "block_title": (item.block.titles or {}).get(survey.default_language, ""),
        "block_titles": dict(item.block.titles or {}),
        "assets": [{f: getattr(a, f) for f in ASSET_FIELDS} for a in item.assets],
        **({"answered": True} if answered else {}),
    })
    survey.trash.append(entry)
    return entry


def expires(entry):
    """When the entry goes by itself; None for a question with real answers (put, answered)."""
    if (entry.data or {}).get("answered"):
        return None
    return entry.deleted_at + timedelta(days=TRASH_DAYS)


def purge(survey, now=None):
    """Remove entries older than TRASH_DAYS (with their files). Returns True if anything changed."""
    now = now or utcnow()
    old = [e for e in survey.trash if expires(e) is not None and expires(e) <= now]
    for e in old:
        destroy(survey, e)
    return bool(old)


def destroy(survey, entry):
    for a in entry.data.get("assets", []):
        media.delete_after_commit(survey, a["filename"])  # once the entry is gone for good (code review 2026-10-15, D5)
    survey.trash.remove(entry)


def _old_id(entry):
    """The id the question had, if it is still free (ids of deleted questions are never given again,
    models.IdFloor; entries from before that hold no id). Answers of earlier participations, the choice
    designs shown to them and the design lock hang on this id."""
    old = entry.data.get("id")
    return old if old is not None and db.session.get(Item, old) is None else None


def label(entry, lang):
    """Short description for the trash list: question text (or type) and variable name."""
    from app import items

    d = entry.data
    texts = d.get("texts", {})
    text = (texts.get(lang) or {}).get("question") or next(
        (t.get("question") for t in texts.values() if t.get("question")), "")
    kind = items.get(d["type"])
    return text or (kind.label if kind else d["type"])


def restore_consent(survey, entry, researcher=None):
    """Put the consent back as it was, alone in a new block at the start. The caller checks that the
    survey has no consent, and places the block in the flow. Returns the Item."""
    from app.items.consent import ensure_consent_block

    d, old_id = entry.data, _old_id(entry)  # looked up before the new consent exists (the lookup flushes)
    ensure_consent_block(survey, researcher)
    item = survey.consent_item
    if old_id is not None:
        item.id = old_id
    item.config, item.texts = d.get("config", {}), d.get("texts", {})
    item.assets = [Asset(**a) for a in d.get("assets", [])]
    survey.trash.remove(entry)
    db.session.flush()
    return item


def _block_again(survey, entry):
    """A new block at the end with the name of the deleted one. The other questions in the trash that
    came from the same block now point to it, so they come back into the same block."""
    d = entry.data
    titles = d.get("block_titles") or ({survey.default_language: d["block_title"]} if d.get("block_title") else {})
    block = Block(position=len(survey.blocks), titles=titles or {survey.default_language: survey.next_block_title()})
    survey.blocks.append(block)
    db.session.flush()
    for other in survey.trash:
        if other.data.get("block_id") == d.get("block_id"):
            other.data = {**other.data, "block_id": block.id}
    return block


def restore(survey, entry, used_codes, fallback_block):
    """Put an item back into its old block at its old position. If the block was deleted, it comes back
    as a new block at the end (`_block_again`); entries without a block go to `fallback_block`.

    Returns the Item, under its old id; a variable name that is taken in the meantime gets a number."""
    d = entry.data
    block = next((b for b in survey.blocks if b.id == d.get("block_id")), None)
    if block is None:
        block = _block_again(survey, entry) if d.get("block_id") is not None else fallback_block
        d = entry.data
    code = d["code"]
    n = 2
    while code in used_codes:
        code, n = f"{d['code']}_{n}", n + 1
    from app import linked

    item = Item(id=_old_id(entry), type=d["type"], code=code, answer_mode=d.get("answer_mode", "optional"),
                config=linked.without_gone(d.get("config", {})), texts=d.get("texts", {}))
    item.assets = [Asset(**a) for a in d.get("assets", [])]
    rows = list(block.items)
    rows.insert(max(0, min(d.get("position", len(rows)), len(rows))), item)
    block.items = rows
    for i, r in enumerate(rows):
        r.position = i
    survey.trash.remove(entry)
    db.session.flush()
    return item
