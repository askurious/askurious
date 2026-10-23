"""Paradata (opt-in per survey): coarse technical information about the device, and the time
spent on each page.

Device information is deliberately coarse so it cannot act as a browser fingerprint: categories
only, no version numbers, the raw user-agent string is never stored, recorded once per response.

Page times are measured on the server, from sending a page to the next submit (Next, Back or a
re-shown page after an error), and added up over repeat visits. They are stored in
Response.paradata under "_t" {page key: seconds}; "_shown" remembers the page on screen. Where a person did
not see all of a page (questions, statements or options left out), "_v" {page key: version} says which
version they saw (shown_version).
A page key is the id of its first item (by position), plus ".t<task>" for conjoint tasks, ".x"
for the conjoint explanation, ".a" for the MaxDiff anchoring question, and "#<row>" inside a loop pass.
"""
import re

VIEWPORT_BUCKETS = ((480, "<480"), (768, "480-767"), (1024, "768-1023"), (1440, "1024-1439"))
COLUMNS = [
    ("device", "Gerätetyp: mobile | tablet | desktop"),
    ("os", "Betriebssystem (ohne Version)"),
    ("browser", "Browser (ohne Version)"),
    ("viewport", "Fensterbreite in Klassen (px)"),
    ("orientation", "portrait | landscape"),
    ("touch", "Touchscreen: 1 = ja, 0 = nein"),
]


# Leaving the page and pasting (opt-in, survey.focus_paste): counts only, sent with each page by participant.js.
# Never what was pasted or where the person went. Kept in Response.paradata under "_fp".
FOCUS_COLUMNS = [
    ("focus_lost", "Fenster oder Tab verlassen: wie oft insgesamt"),
    ("focus_pages", "Fenster oder Tab verlassen: auf wie vielen abgeschickten Seiten"),
    ("pasted", "In ein Textfeld eingefügt: wie oft insgesamt"),
]
# With survey.focus_inputs: how long the pasted texts were together, and how many input steps the text fields got
# (a key typed, a word of the phone keyboard or of dictation; not pasting, not deleting). Text that stands in a field
# without either was put there by a program: an agent that fills the fields at once (app/quality.py, flag "typed").
INPUT_COLUMNS = [
    ("pasted_chars", "In ein Textfeld eingefügt: Zeichen insgesamt"),
    ("text_inputs", "Eingaben in Textfelder (Tastendruck, Wort der Bildschirmtastatur, Diktat): wie viele insgesamt"),
]
MAX_CHARS = 100000  # per page: a bound on what a form may claim
DIEDENHOFEN = ("Diedenhofen, B., & Musch, J. (2017). PageFocus: Using paradata to detect and prevent cheating on online "
               "achievement tests. Behavior Research Methods, 49(4), 1444–1459. https://doi.org/10.3758/s13428-016-0800-7")
MAX_COUNT = 999  # per page: more than a person does, and a bound on what a form may claim


def note_focus(response, form, inputs=False):
    """A page was sent with its counts (fields _fp_blur, _fp_paste; absent without JavaScript): add them up.
    inputs: the survey also counts pasted characters and input steps (_fp_pchars, _fp_inputs; survey.focus_inputs)."""
    if "_fp_blur" not in form:
        return

    def count(name):
        try:
            return min(max(int(form.get(name, 0)), 0), MAX_COUNT)
        except (TypeError, ValueError):
            return 0

    data = dict(response.paradata or {})
    kept = {"lost": 0, "pages": 0, "pasted": 0, **(data.get("_fp") or {})}
    lost = count("_fp_blur")
    out = {**kept, "lost": kept["lost"] + lost, "pages": kept["pages"] + (lost > 0), "pasted": kept["pasted"] + count("_fp_paste")}
    if inputs and "_fp_inputs" in form:  # the counts the consent names since focus_inputs

        def amount(name):
            try:
                return min(max(int(form.get(name, 0)), 0), MAX_CHARS)
            except (TypeError, ValueError):
                return 0

        out["pchars"] = kept.get("pchars", 0) + amount("_fp_pchars")
        out["inputs"] = kept.get("inputs", 0) + amount("_fp_inputs")
    response.paradata = {**data, "_fp": out}


def focus_values(response):
    """{column: value} of FOCUS_COLUMNS; {} where no page with counts was sent."""
    kept = (response.paradata or {}).get("_fp")
    if not kept:
        return {}
    out = {"focus_lost": kept.get("lost", 0), "focus_pages": kept.get("pages", 0), "pasted": kept.get("pasted", 0)}
    if "inputs" in kept:  # counted from focus_inputs on; empty for pages sent before
        out.update({"pasted_chars": kept.get("pchars", 0), "text_inputs": kept["inputs"]})
    return out


def _os(ua):
    if re.search(r"iPhone|iPad|iPod", ua):
        return "iOS"
    if "Android" in ua:
        return "Android"
    if "CrOS" in ua:
        return "ChromeOS"
    if "Windows" in ua:
        return "Windows"
    if "Macintosh" in ua or "Mac OS X" in ua:
        return "macOS"
    if "Linux" in ua:
        return "Linux"
    return "other"


def _browser(ua):
    for token, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("FxiOS/", "Firefox"),
                        ("CriOS/", "Chrome"), ("Chrome/", "Chrome"), ("Safari/", "Safari")):
        if token in ua:
            return name
    return "other"


def _viewport(width):
    try:
        w = int(width)
    except (TypeError, ValueError):
        return ""
    for limit, label in VIEWPORT_BUCKETS:
        if w < limit:
            return label
    return ">=1440"


def collect(user_agent, form):
    """Categories from the User-Agent header and a few values the page sends (see participant.js)."""
    ua = user_agent or ""
    touch = form.get("_pd_touch") == "1"
    os_ = _os(ua)
    width = form.get("_pd_vw")
    if "iPad" in ua or ("Android" in ua and "Mobile" not in ua) or (os_ == "macOS" and touch):
        device = "tablet"  # iPadOS reports itself as macOS; touch tells it apart
        if os_ == "macOS":
            os_ = "iOS"
    elif re.search(r"Mobi|iPhone|Android", ua):
        device = "mobile"
    else:
        device = "desktop"
    return {
        "device": device,
        "os": os_,
        "browser": _browser(ua),
        "viewport": _viewport(width),
        "orientation": form.get("_pd_or") if form.get("_pd_or") in ("portrait", "landscape") else "",
        "touch": 1 if touch else 0,
    }


MAX_PAGE_SECONDS = 3600  # longer gaps (a forgotten tab) count as one hour


def page_key(page_items, spec):
    first = min(page_items, key=lambda i: i.position) if page_items else None
    if first is None:
        return None
    key = str(first.id)
    if "task" in spec:
        key += f".t{spec['task']}"
    if "holdout" in spec:  # a holdout task (app/holdouts.py)
        key += f".h{spec['holdout']}"
    if spec.get("explain"):
        key += ".x"
    if spec.get("anchor"):
        key += ".a"
    if spec.get("row"):
        key += f"#{spec['row']}"
    return key


def page_left(response, position, key, now, forward=True):
    """The participant submits the page at `position`: add the time since it was shown. Returns the seconds
    of this visit if it is the first "Next" on the page (for the tempo hint, app/tempo.py), else None.
    forward=False: the page is left with "Back" or sent for a change of language; its time counts, but its
    first "Next" is still to come (kept in "_tn": the pages whose first "Next" was sent). Participations from
    before "_tn" was kept go by the old rule: the first visit only."""
    data = dict(response.paradata or {})
    shown = data.get("_shown") or {}
    first = None
    if key and shown.get("pos") == position and shown.get("at"):
        from datetime import datetime

        seconds = max(0.0, (now - datetime.fromisoformat(shown["at"])).total_seconds())
        times = dict(data.get("_t") or {})
        if "_tn" not in data and times:  # started before the pages of the first "Next" were kept
            if key not in times:
                first = seconds
        elif forward and key not in (data.get("_tn") or []):
            first = times.get(key, 0) + seconds  # with the visits before (left with "Back"): read there already
        if "_tn" in data or not times:  # the new rule from the first page on
            sent = list(data.get("_tn") or [])
            if forward and key not in sent:
                sent.append(key)
            data["_tn"] = sent
        times[key] = round(times.get(key, 0) + min(seconds, MAX_PAGE_SECONDS), 1)
        data["_t"] = times
    data.pop("_shown", None)
    response.paradata = data
    return first


def shown_version(all_items, page_items, answers):
    """Which version of a page this person saw, as a short code: the questions left out (ItemType.applies) and
    the statements, pairs and options not shown (app/rows.py); None if they saw all of it, as on every page
    without such content. Kept per page key under "_v" (note_version): the speed index compares a page time
    with those of the people who saw the same (export.page_medians)."""
    import hashlib

    from app import rows

    shown = {i.id for i in page_items}
    gone = []
    for item in sorted(all_items, key=lambda i: i.position):
        if item.kind.computed:
            continue
        if item.id not in shown:
            gone.append(str(item.id))
            continue
        for field in ("statements", "pairs", "options"):
            found = (item.config or {}).get(field)
            if isinstance(found, list) and rows.conditional(found):
                gone += sorted(f"{item.id}.{r}" for r in map(str, rows.hidden(found, answers, item.survey)))
    return hashlib.sha1(",".join(gone).encode()).hexdigest()[:8] if gone else None


def note_version(response, key, version):
    """Keeps the version of the page `key` this person saw (shown_version) in Response.paradata "_v"."""
    data = response.paradata or {}
    versions = dict(data.get("_v") or {})
    if versions.get(key) == version or (version is None and key not in versions):
        return
    if version is None:
        versions.pop(key, None)
    else:
        versions[key] = version
    data = {k: v for k, v in data.items() if k != "_v"}
    response.paradata = {**data, "_v": versions} if versions else data


MAX_LANGS = 20  # language switches kept per participation: the first language and the last ones


def note_language(response, lang):
    """A stored participation goes on in another language (the language link or picker): kept in Response.paradata
    "_langs" as [[language, position from which on], ...], the first entry the language it began in. The export
    says whether the language was changed (export.LANG_SWITCH_COLUMN; audit 2026-10-12, T-5)."""
    data = response.paradata or {}
    langs = [list(x) for x in data.get("_langs") or [] if isinstance(x, (list, tuple)) and len(x) == 2]
    if not langs:
        langs = [[response.language, 0]]
    if langs[-1][0] == lang:
        return
    langs.append([lang, response.position or 0])
    if len(langs) > MAX_LANGS:
        langs = langs[:1] + langs[-(MAX_LANGS - 1):]
    response.paradata = {**data, "_langs": langs}


def language_switched(response):
    """1 if the participation changed its language on the way (note_language), else 0."""
    langs = (response.paradata or {}).get("_langs") or []
    return int(len({x[0] for x in langs if isinstance(x, (list, tuple)) and x}) > 1)


def page_shown(response, position, now):
    """The page at `position` is sent to the browser: its clock starts, unless it runs already (as pagetime.shown).
    A reload, the way back from the privacy page or a page dropped as sent twice shows the same page again; the
    time before counts (audit 2026-10-10, T-1). page_left stops the clock, so a page shown again after it was sent
    (an error, the tempo hint) starts anew, its time so far already added up."""
    data = response.paradata or {}
    if (data.get("_shown") or {}).get("pos") != position or not (data.get("_shown") or {}).get("at"):
        response.paradata = {**data, "_shown": {"pos": position, "at": now.isoformat()}}


def timing_columns(survey):
    """[(column, label, key)] for all pages of the main part, in questionnaire order."""
    return [(column, label, key) for column, label, key, _, _ in timing_pages(survey)]


def timing_pages(survey):
    """[(column, label, key, block, items of the page)] for all pages of the main part, in questionnaire order;
    a page of its own of a task (conjoint, MaxDiff) has the task's item, an explanation page the suffix ".x"."""
    from app import flow, loops, runtime

    separated = set(flow.blocks_after_split(survey))
    out = []
    for block in survey.blocks:
        if block.id in separated:
            continue
        loop_rows = [(n, r["id"]) for n, r in enumerate(loops.config(block)["rows"], start=1)] if loops.active(block) else []
        pages = block.pages() if loop_rows else runtime.split_alone(block.pages())
        for items in pages:
            if not items:
                continue
            first = min(items, key=lambda i: i.position)
            base = f"t_{first.code}"
            label = f"Zeit auf der Seite ab „{first.code}“ in Sekunden (Summe aller Besuche)"
            if loop_rows:
                out += [(f"{base}_{n}", f"{label}, Durchgang Zeile {n}", f"{first.id}#{rid}", block, items)
                        for n, rid in loop_rows]
            elif len(items) == 1 and first.kind.timing_pages(first) is not None:
                out += [(base + col, f"{label}, {text}", f"{first.id}{key}", block, items)
                        for col, text, key in first.kind.timing_pages(first)]
            else:
                out.append((base, label, str(first.id), block, items))
    return out
