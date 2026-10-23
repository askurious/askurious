"""Simple blocks loaded at once: the survey setting "Schneller blättern, auch bei schwachem Netz" (Survey.block_load; experimental
until 2026-10-16; concept konzept-webmcp-prefetch-demografie-2026-10-15.md, part 2, decisions 3 and 4a).

A block is *simple* when nothing on one of its pages depends on what is answered on an earlier page of the same
block. Then all its pages are known when its first page is drawn, and they can come to the browser in one document
and be switched there; going back inside the block needs no server either. Dependencies on earlier blocks are no
obstacle: those answers are fixed when the block is drawn.

What makes a block not simple (reasons(), in the editor per block):
  - only one page: nothing to gain, the block is shown as always;
  - a question that reads an answer of an earlier page of the same block: its own condition ("Nur zeigen, wenn …",
    app/item_condition.py), conditions of its statements, rows or options (app/rows.py), options carried from it
    (app/carry.py), a total taken from it (constant sum), a placeholder {{code}} of it in a text (app/piping.py), and
    each of these through a Rechenwert (app/items/calc.py) that reads such an answer;
  - pages whose logic runs on the server page by page: the consent, tasks with pages of their own (choice tasks,
    adaptive pages, timed tasks), elements that show results of others or fix answers once feedback is seen, voice
    messages (files go with the page), video or audio with a playback log (timed from the moment the page shows), pages
    that go on by themselves without buttons, and the speed hint (it reacts to each page as it is sent);
  - a whole survey: a live vote, a diary study (the app keeps its own pages) or the setting being off.
Page times with a minimum or maximum are no obstacle (decision 4a): the browser measures each page and holds or sends
it as the server would, and the server checks the sum of the block against its own clock (app/participate.py,
finish_block).

Quota calculations, web services, external sections, ends and branches stand between blocks in the flow, never
inside one: they are reached when the block is sent at its end, exactly as before.
"""
from app.lang import de_en

# item types whose page logic needs the server page by page (see the module text), with why
_TYPES = {
    "voice": ("Sprachnachricht (die Aufnahme geht mit der Seite)", "voice message (the recording goes with the page)"),
    "media": ("Video / Audio mit Abspielprotokoll (gemessen ab dem Zeigen der Seite)",
              "video / audio with a playback log (timed from when the page shows)"),
}


def on(survey):
    """The survey loads its simple blocks at once: the setting is on, and the survey is a plain questionnaire."""
    return bool(getattr(survey, "block_load", False)) and survey_reasons(survey) == []


def survey_reasons(survey):
    """Why no block of this survey is loaded at once, whatever the blocks are ([] if the survey allows it)."""
    out = []
    if getattr(survey, "live", None) is not None:
        out.append(de_en("Live-Abstimmung", "live poll"))
    if getattr(survey, "sampling", None) is not None:
        out.append(de_en("Tagebuchstudie (die App hält ihre eigenen Seiten)", "diary survey (the app keeps its own pages)"))
    if getattr(survey, "interview", None) is not None:
        out.append(de_en("Interviewleitfaden", "interview guide"))
    return out


def _pages(block):
    from app.runtime import split_alone

    return split_alone(block.pages())


def _refs(item, sources, items):
    """The ids of the questions whose answers `item` reads: through its conditions, carried options, totals and
    placeholders, and through Rechenwerte (followed to what they read)."""
    from app import flow, item_condition
    from app.items.base import _walk_strings, placeholders

    found = set()

    def twin(old):  # the copy hooks of the types name every question a setting reads (ItemType.remap_config)
        if isinstance(old, int) and not isinstance(old, bool):
            found.add(old)
        return old

    config = item.config or {}
    try:
        item.kind.remap_config(dict(config), twin)
        item_condition.remap(dict(config), twin)
    except (TypeError, ValueError, AttributeError, KeyError):
        pass

    def walk(value):  # every condition, also several joined ({"conds": [...]}) and those of rows
        if isinstance(value, dict):
            ref = value.get("item")
            source = int(ref) if isinstance(ref, str) and ref.isdigit() else flow.source_id(ref)
            if source is not None:
                found.add(source)
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(config)
    for text in (*_walk_strings(item.texts or {}), *_walk_strings(config)):
        for name in placeholders(text):
            source = sources.get(name)
            if source is not None:
                found.add(source.id)
    # a Rechenwert reads other answers: what it reads counts as read here (a chain to its end)
    seen = set()
    todo = [r for r in found if r in items and items[r].kind.computed]
    while todo:
        calc = items[todo.pop()]
        if calc.id in seen:
            continue
        seen.add(calc.id)
        more = set()
        try:
            calc.kind.remap_config(dict(calc.config or {}), lambda old: more.add(old) or old)
        except (TypeError, ValueError, AttributeError, KeyError):
            pass
        found |= more
        todo += [r for r in more if r in items and items[r].kind.computed]
    found.discard(item.id)
    return found


def reasons(survey, block, sources=None, items=None):
    """Why this block is not loaded at once, as texts for the editor; [] if it is simple."""
    from app import piping, tempo
    from app.items.base import ItemType
    from app.runtime import auto_next_ok

    pages = _pages(block)
    if len(pages) < 2:
        return [de_en("nur eine Seite", "only one page")]
    out = []
    if tempo.block_on(survey, block):
        out.append(de_en("Tempo-Hinweis: Er reagiert auf jede Seite, sobald sie abgeschickt wird",
                         "speed hint: it reacts to each page as soon as it is sent"))
    sources = piping.sources(survey) if sources is None else sources
    items = {i.id: i for b in survey.blocks for i in b.items} if items is None else items
    page_of = {i.id: n for n, page in enumerate(pages) for i in page}
    seen_types = set()
    for n, page in enumerate(pages, start=1):
        for item in page:
            kind = item.kind
            if item.type == "consent":
                why = de_en("Einwilligung", "consent")
            elif kind.alone_on_page or type(kind).expand_pages is not ItemType.expand_pages:
                why = de_en("Aufgabe mit eigenen Seiten ({type})", "task with pages of its own ({type})", type=kind.label)
            elif kind.ends_page:
                why = de_en("Element mit eigenen Knöpfen ({type})", "element with buttons of its own ({type})", type=kind.label)
            elif kind.category == "feedback":
                why = de_en("Rückmeldung zu bisherigen Antworten ({type})", "feedback on answers so far ({type})", type=kind.label)
            elif item.type in _TYPES:
                why = de_en(*_TYPES[item.type])
            else:
                why = None
            if why and why not in seen_types:
                seen_types.add(why)
                out.append(why)
        if auto_next_ok(page, {}):
            out.append(de_en("Seite {n} geht ohne Knöpfe von selbst weiter", "page {n} goes on automatically without buttons", n=n))
    for n, page in enumerate(pages):
        if n == 0:
            continue
        for item in page:
            if item.kind.computed:
                continue
            earlier = sorted({items[r].code for r in _refs(item, sources, items)
                              if r in page_of and page_of[r] < n and r in items})
            if earlier:
                out.append(de_en("{code} auf Seite {n} hängt von {refs} auf einer früheren Seite dieses Blocks ab",
                                 "{code} on page {n} depends on {refs} on an earlier page of this block",
                                 code=item.code, n=n + 1, refs=", ".join(earlier)))
    return out


def check(survey):
    """{block id: [reasons]} for every block ([] = loaded at once); the reasons of the survey stand for every block."""
    from app import piping

    common = survey_reasons(survey)
    sources = piping.sources(survey)
    items = {i.id: i for b in survey.blocks for i in b.items}
    return {b.id: common + reasons(survey, b, sources, items) for b in survey.blocks}


def simple(survey, block):
    """Is this block loaded at once for the participants of `survey` (the setting on and the block simple)? Worked
    out once per request and block."""
    from flask import g, has_app_context

    if not on(survey):
        return False
    cache = g.setdefault("blockload_simple", {}) if has_app_context() else {}
    key = (survey.id, block.id)
    if key not in cache:
        cache[key] = not reasons(survey, block)
    return cache[key]


# --- a participation in a block loaded at once ---------------------------------------------------------------------
#
# Response.layout["bl"] holds the state of the block on screen: {"start": its first position, "at": when the server
# sent it or last checked it (its clock for the block), "done": {position: counter of the last page stored by
# store() without errors or notes}, "seen": {position: counter of the newest page store() took, whatever came of it},
# "ms": {position: milliseconds the background sends measured}}; Response.layout["blc"] the milliseconds of
# the browser already counted per page (counted). The participation stays at the block's
# first position (Response.position) until the block is sent at its end; a failed check moves it to the page that is
# not complete.

TOLERANCE_MS = 3000  # the browser's sum may run ahead of the server's clock by this much (the way there and back)
MAX_BODY = 200000    # bytes a page sent in the background may have (a page of the form, as JSON)


def _same(a, b):
    from app.runtime import is_marker

    return not is_marker(a) and a.get("block") == b.get("block") and a.get("iter") == b.get("iter") \
        and a.get("loop") == b.get("loop")


def run(survey, response, position):
    """The layout positions of the block around `position` if it is loaded at once for this participation, else
    None: a real participation (no preview, no dummy, no diary entry, no separated section) in a survey with the
    setting on, a simple block, and more than one page."""
    from app.extensions import db
    from app.models import Block
    from app.runtime import is_marker

    if not hasattr(response, "pid") or getattr(response, "is_preview", False) \
            or getattr(response, "source", "real") != "real" or getattr(response, "enrolment_id", None) or not on(survey):
        return None
    pages = (response.layout or {}).get("pages") or []
    if position is None or not 0 <= position < len(pages) or is_marker(pages[position]):
        return None
    spec = pages[position]
    block = db.session.get(Block, spec.get("block"))
    if block is None or not simple(survey, block):
        return None
    first = last = position
    while first > 0 and _same(pages[first - 1], spec):
        first -= 1
    while last + 1 < len(pages) and _same(pages[last + 1], spec):
        last += 1
    return list(range(first, last + 1)) if last > first else None


def state(response):
    return dict((getattr(response, "layout", None) or {}).get("bl") or {})


def _keep(response, st):
    response.layout = {**(response.layout or {}), "bl": st}


def shown(response, positions, now):
    """The block is sent to the browser: its clock starts, unless it runs already for this block."""
    st = state(response)
    if st.get("start") != positions[0] or not st.get("at"):
        _keep(response, {"start": positions[0], "at": now.isoformat(), "done": {}})


def resume_at(response, positions):
    """The page the block opens on: the one the participation is at, if a check sent it back there; else the page after
    the last one stored in the background (a reload, the link opened again), else the first."""
    if response.position in positions and response.position != positions[0]:
        return response.position
    st = state(response)
    if st.get("start") != positions[0]:
        return positions[0]
    done = [int(p) for p in (st.get("done") or {}) if str(p).isdigit() and int(p) in positions]
    if not done:
        return positions[0]
    later = [p for p in positions if p > max(done)]
    return later[0] if later else max(done)


def _number(value, low=0, high=10 ** 9):
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


def store(survey, response, positions, data, lang, params=None):
    """A page of the block sent in the background (participate.block_page): its valid answers are stored, as "Back"
    keeps them; with "Next" its errors and notes come back, and the page counts as done when it has none. The same
    page with the same or an older counter again (sent twice, from the queue of the tab) changes nothing.
    Returns the answer for the browser, or None for a page that is not one of the block."""
    from app import geoip, offline_answers, quality, tempo
    from app import quotas as quota_mod
    from app.models import utcnow
    from app.runtime import (_AsOptional, _answer_meta, answer_key, evaluate, load_answers, page_at, present,
                             row_view, store_answers)

    page, seq = _number(data.get("page")), _number(data.get("seq"), 1, 10 ** 15)  # the browser counts in ms
    if page is None or seq is None or page not in positions:
        return None
    st = state(response)
    done = dict(st.get("done") or {})
    seen = dict(st.get("seen") or {})
    # the newest send of a page wins, whatever came of it: an older one arriving later (from the queue of the tab, a
    # "Next" overtaken by a "Back") changes nothing (audit data storage 2026-10-21, A4)
    if st.get("start") == positions[0] and seq <= max(int(done.get(str(page)) or 0), int(seen.get(str(page)) or 0)):
        return {"ok": True, "again": True, "errors": {}, "nudges": {}}
    form = offline_answers.fields({"form": data.get("form") or {}})
    layout = response.layout
    spec = layout["pages"][page]
    row = spec.get("row")
    view = {**{f"p:{k}": v for k, v in (params or {}).items()}, **quota_mod.view(survey, response),
            **quality.flow_view(survey, response), **tempo.flow_view(survey, response), **geoip.flow_view(survey, response)}
    answers = {**load_answers(response), **view}
    _block, all_items = page_at(survey, response, page)
    page_items = present(all_items, answers, row)
    loose = bool(data.get("back") or data.get("up"))  # "Back" and a timer that ran out: kept without checks
    items = [_AsOptional(i) for i in page_items] if loose else page_items
    result = evaluate(items, form, lang, nudge_acknowledged=loose or bool(data.get("ack")),
                      meta=_answer_meta(layout, page, response), answers=row_view(answers, row))
    store_answers(response, result.answers, row)
    if result.errors:
        # an answer changed into something invalid is not kept, nor the one from before (as runtime.step does)
        stored = dict(response.answers or {})
        for item_id in set(result.errors) - set(result.answers):
            stored.pop(answer_key(item_id, row), None)
        response.answers = stored
    if st.get("start") != positions[0]:
        st = {"start": positions[0], "at": utcnow().isoformat(), "done": {}}
        done, seen = {}, {}
    if not result.errors and not result.nudges and not data.get("back"):
        done[str(page)] = seq
    seen[str(page)] = seq
    ms = dict(st.get("ms") or {})  # the time on the page so far: kept, the tab may be reloaded before the end
    ms[str(page)] = max(int(ms.get(str(page)) or 0), _number(data.get("ms"), 0, 10 ** 8) or 0)
    _keep(response, {**st, "done": done, "seen": seen, "ms": ms})
    response.last_active_at = utcnow()
    quota_mod.reclaim(survey, response)  # a reservation runs on while pages are sent
    return {"ok": not result.errors and not result.nudges,
            "errors": {str(k): str(v) for k, v in result.errors.items()},
            "nudges": {str(k): str(v) for k, v in result.nudges.items()}}


def read_meta(form):
    """The field _block of the form sent at the end of a block: {"start": position, "pages": {position: {"ms", "up",
    "more", "ack"}}}; None if it is missing or cannot be read."""
    import json

    raw = form.get("_block")
    if not raw or len(raw) > 20000:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):  # nested too deep: a 500 before (audit 2026-10-22, C-02)
        return None
    if not isinstance(data, dict) or _number(data.get("start")) is None or not isinstance(data.get("pages"), dict):
        return None
    pages = {}
    for key, value in list(data["pages"].items())[:500]:
        if _number(key) is None or not isinstance(value, dict):
            continue
        pages[int(key)] = {"ms": _number(value.get("ms"), 0, 10 ** 8) or 0, "up": bool(value.get("up")),
                           "more": _number(value.get("more"), 0, 10) or 0, "ack": bool(value.get("ack"))}
    return {"start": int(data["start"]), "pages": pages}


def page_form(form, token, page_meta):
    """The form one page of the block would have sent by itself: the whole form of the block (its fields are named
    by their questions) with this page's token and what it said of itself (timer, "Mehr Zeit", a note seen)."""
    from werkzeug.datastructures import MultiDict

    out = MultiDict([(k, v) for k, v in form.items(multi=True)
                     if k not in ("pos", "nav", "time_up", "time_more", "nudge_ack", "_block", "set_lang")])
    out["pos"], out["nav"] = token, "next"
    if page_meta.get("up"):
        out["time_up"] = "1"
    if page_meta.get("more"):
        out["time_more"] = str(page_meta["more"])  # how often "Mehr Zeit" was taken on the page
    if page_meta.get("ack"):
        out["nudge_ack"] = "1"
    return out


def browser_times(response, positions, meta, now):
    """({position: milliseconds} the browser measured on each page since they were last counted, cut, browser sum,
    server time, {position: milliseconds measured in all}) for the block sent now (decision 4a): the browser measures each page, the server only the block,
    from when it sent it (or checked it last) to now. A sum longer than that (a clock tampered with, a page kept
    open in another tab) is cut down in proportion, so no page counts more time than the server saw."""
    from datetime import datetime

    st = state(response)
    mine = st.get("start") == positions[0]
    cred, kept = (st.get("cred") or {}) if mine else {}, st.get("ms") if mine else None
    kept = kept or {}
    # the tab may have been reloaded in between: what the pages sent in the background measured counts too
    measured = {p: max((meta["pages"].get(p) or {}).get("ms", 0), int(kept.get(str(p)) or 0)) for p in positions}
    added = {p: max(0, measured[p] - int(cred.get(str(p)) or 0)) for p in positions}
    try:
        since = datetime.fromisoformat(st["at"]) if st.get("start") == positions[0] and st.get("at") else None
    except (TypeError, ValueError):
        since = None
    server = (now - since).total_seconds() * 1000 if since else None
    total = sum(added.values())
    cut = server is not None and total > server + TOLERANCE_MS
    if cut:
        factor = max(0.0, server) / total
        added = {p: ms * factor for p, ms in added.items()}
    return added, cut, total, server, measured


def note_times(response, positions, measured, now, cut, total, server, sent, added=None):
    """After the block was sent: what the browser measured counts as counted (cred) and the block's clock starts anew
    for a page the check sends back; Response.paradata "_bl" keeps how many pages the browser timed and, for each
    block whose sum was cut, [its first position, the browser's sum, the server's time] in seconds (export columns
    COLUMNS, quality control)."""
    st = state(response)
    mine = st.get("start") == positions[0]
    cred = dict(st.get("cred") or {}) if mine else {}
    total_ms = counted(response)
    for p in sent:
        before = int(cred.get(str(p)) or 0)
        cred[str(p)] = max(before, measured.get(p, 0))
        total_ms[str(p)] = int(total_ms.get(str(p)) or 0) + (added.get(p, 0) if added else 0)
    response.layout = {**(response.layout or {}), "blc": total_ms}
    if mine:
        _keep(response, {**st, "cred": cred, "at": now.isoformat()})
    data = dict(response.paradata or {})
    kept = dict(data.get("_bl") or {})
    kept["pages"] = int(kept.get("pages") or 0) + len(sent)
    if cut:
        kept["cut"] = [*(kept.get("cut") or []), [positions[0], round(total / 1000, 1), round(max(server or 0, 0) / 1000, 1)]][-50:]
    data["_bl"] = kept
    response.paradata = data


def leave(response):
    """The block is over (the participation went on past it, or ended): its state goes."""
    if "bl" in (getattr(response, "layout", None) or {}):
        response.layout = {k: v for k, v in response.layout.items() if k != "bl"}


# export and quality control: which page times the browser measured (decision 4a)
COLUMNS = [
    ("pages_browser", "Am Stück geladene Blöcke: Zahl der Seiten, deren Zeit der Browser gemessen hat"),
    ("times_cut", "Am Stück geladene Blöcke: 1 = die Seitenzeiten des Browsers waren länger als die Uhr des Servers "
                  "erlaubt und wurden gekürzt"),
]
COLUMNS_EN = {"pages_browser": "Blocks loaded at once: number of pages whose time the browser measured",
              "times_cut": "Blocks loaded at once: 1 = the browser's page times were longer than the server's clock "
                           "allows and were cut down"}


def pages_timed(response):
    """How many pages of this participation the browser timed (blocks loaded at once)."""
    return int(((getattr(response, "paradata", None) or {}).get("_bl") or {}).get("pages") or 0)


def times_cut(response):
    """1 if the browser's page times of a block of this participation were longer than the server's clock allowed
    (cut down, browser_times), else 0."""
    return int(bool(((getattr(response, "paradata", None) or {}).get("_bl") or {}).get("cut")))


def quality_counts(survey, sources=("real",)):
    """For the speeder check on "Qualitätssicherung": of the completed participations of these sources, how many have
    page times the browser measured, and how many of them had a block whose times were cut to the server's clock.
    The relative speed index reads the page times as they are kept, so cut ones count with the server's time."""
    from app.extensions import db
    from app.models import Response

    timed = cut = 0
    for data in db.session.scalars(db.select(Response.paradata).filter(
            Response.survey_id == survey.id, Response.source.in_(tuple(sources)), Response.status == Response.COMPLETED)):
        kept = (data or {}).get("_bl") or {}
        if kept.get("pages"):
            timed += 1
            cut += bool(kept.get("cut"))
    return {"timed": timed, "cut": cut}


def counted(response):
    """{position: milliseconds} of the browser already counted for each page of blocks loaded at once (note_times),
    in Response.layout["blc"]: kept when the block is left, so a block entered again with "Back" from the next one
    does not count its pages twice nor hold them again."""
    return dict((getattr(response, "layout", None) or {}).get("blc") or {})


def counted_ms(response, positions, page):
    """Milliseconds of the page already counted from an earlier sending of its block (note_times)."""
    return int(counted(response).get(str(page)) or 0)
