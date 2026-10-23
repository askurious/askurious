"""Answers fixed by feedback (Philipp and Ralf, 2026-10-11): a feedback element with the tick "Antworten danach nicht
mehr ändern" (config "lock_answers", on for new elements) fixes the answers to the questions it shows, so nobody moves
an answer towards the majority after seeing it.

- A source on an earlier page is fixed once the feedback has been shown; its page says so beforehand (hint).
- A source on the same page is fixed once the page has been sent with that answer. A crowd on the same page comes
  without its figures and waits for "Antwort festlegen": that sends the answer to the server (fix_on_page), which
  stores and fixes it and only then gives the figures (audit 2026-10-11, N8/TN4: before, the figures stood in the page
  and only the browser held the answer). Until then the answer can change. On a first page that is not stored yet,
  the crowd comes with its figures and the browser holds the answer, as before.
- Fixed answers are kept by the server: whatever a page sends for them is dropped, "Back" shows them read-only.

The fixed answers are kept as answer keys ("12", "12#r3ab" in a loop) in response.layout["locked"], not in the
answers, so the export stays as it was. A new layout (the questionnaire changed under a running participation) starts
without them."""
from app.runtime import answer_key

LOCK_KEY = "locked"


def locking(item):
    """Does this element fix the answers it shows?"""
    return getattr(item.kind, "category", None) == "feedback" and bool((item.config or {}).get("lock_answers"))


def _locked(response):
    return set(((getattr(response, "layout", None) or {}).get(LOCK_KEY)) or [])


def _keep(response, keys):
    """Add answer keys to the fixed ones of this participation."""
    layout = getattr(response, "layout", None)
    if not keys or not isinstance(layout, dict):
        return
    have = set(layout.get(LOCK_KEY) or [])
    if keys <= have:
        return
    response.layout = {**layout, LOCK_KEY: sorted(have | keys)}


def _source_keys(feedback, source, row, answers):
    """The answer keys of `source` that `feedback` (shown in pass `row`) shows: the same pass inside a loop, every
    answered pass of a loop for an element after it, else the plain key."""
    if row and source.block_id == feedback.block_id:
        return {answer_key(source.id, row)}
    passes = {k for k in answers if isinstance(k, str) and k.startswith(f"{source.id}#")}
    return passes or {answer_key(source.id)}


def frozen(response, page_items, row):
    """Ids of the questions on this page whose answers are fixed."""
    keys = _locked(response)
    return {i.id for i in page_items if answer_key(i.id, row) in keys} if keys else set()


def hinted(response, page_items, row, survey):
    """Ids of the questions on this page that a feedback element will fix (not fixed yet): their page says so."""
    keys = _locked(response)
    sources = set()
    for block in survey.blocks:
        for item in block.items:
            if locking(item):
                sources.update(item.config.get("sources") or [])
    return {i.id for i in page_items if i.id in sources and answer_key(i.id, row) not in keys}


def drop(result, ids):
    """Forget what a page sent for fixed questions: the stored answer stays, no message is shown for them."""
    for item_id in ids:
        for part in (result.answers, result.errors, result.nudges, result.display):
            part.pop(item_id, None)


def note_shown(response, page_items, row, answers):
    """The page with the feedback is shown: the sources on earlier pages are fixed from now on."""
    on_page = {i.id for i in page_items}
    keys = set()
    for item in page_items:
        if locking(item):
            for source in item.kind.sources(item):
                if source.id not in on_page:
                    keys |= _source_keys(item, source, row, answers)
    _keep(response, keys)


def note_sent(response, page_items, row, answers):
    """The page with the feedback was sent: its sources are fixed, those on this page only with an answer."""
    on_page = {i.id for i in page_items}
    keys = set()
    for item in page_items:
        if locking(item):
            for source in item.kind.sources(item):
                if source.id in on_page:
                    key = answer_key(source.id, row)
                    if answers.get(key if row else source.id) not in (None, "", {}, []):
                        keys.add(key)
                else:
                    keys |= _source_keys(item, source, row, answers)
    _keep(response, keys)


def waiting(item, source, page_items, locked_ids):
    """Does the crowd `item` wait for "Antwort festlegen" before it may show anything (its source on this page, not
    fixed yet)?"""
    return locking(item) and source is not None and source in page_items and source.id not in (locked_ids or ())


def fix_on_page(survey, response, form, item_id, lang):
    """"Antwort festlegen" of the crowd `item_id` on the current page of `response`: its source's answer from `form`
    stored and fixed, then {"avatars", "own"} of the crowd. {"error": text} for a missing or invalid answer; None if
    the form is not of the current page or the element does not wait there (the page changed, a forged id)."""
    from app import runtime

    layout = getattr(response, "layout", None) or {}
    pages = layout.get("pages") or []
    position = response.position or 0
    if not 0 <= position < len(pages) or runtime.is_marker(pages[position]) or not pages[position].get("items"):
        return None
    if form.get("pos") != runtime.page_token(response, position):
        return None
    spec = pages[position]
    row = spec.get("row")
    block, all_items = runtime.page_at(survey, response, position)
    item = next((i for i in all_items if i is not None and i.id == item_id), None)
    if item is None or not locking(item) or not hasattr(item.kind, "crowd"):
        return None
    source = next(iter(item.kind.sources(item)), None)
    if source is None or source not in all_items:
        return None
    key = answer_key(source.id, row)
    if key not in _locked(response):
        result = runtime.evaluate([source], form, lang, nudge_acknowledged=True, meta=spec,
                                  answers=runtime.row_view(runtime.load_answers(response), row))
        answer = result.answers.get(source.id)
        if source.id in result.errors or answer in (None, "", {}, []):
            from app.ui_i18n import tr

            return {"error": result.errors.get(source.id) or tr(lang, "Bitte beantworten Sie diese Frage.",
                                                                "Please answer this question.")}
        runtime.store_answers(response, {source.id: answer}, row)
        _keep(response, {key})
    answers = runtime.load_answers(response)
    view = dict(runtime.row_view(answers, row))
    if row:
        for other in block.items:
            view[other.id] = answers.get(answer_key(other.id, row))
    crowd = item.kind.crowd(item, lang, view, seed=response.id, row=row)
    if crowd is None:
        return None
    return {"avatars": crowd["avatars"], "own": crowd["own"] if crowd["show_own"] else None}
