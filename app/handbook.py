"""The handbook under /docs/ (Philipp, 2026-10-10): what Askurious is for, its pages and dialogs with the trade-offs
of their settings, and where AI is used; for researchers and students, in German and English, in the look of the
site and without an account. Reached by the book in the header (base.html).

The texts are Markdown files, one per page and language: app/data/handbook/<de|en>/<page>.md, rendered by
app/handbook_md.py; the screenshots are in static/handbook/<de|en>/ (tools/handbook_shots takes them again). A page missing in the language of the reader is shown
in the other one."""
import os
import re

from flask import Blueprint, abort, current_app, g, render_template, request, url_for

from app import brand
from app.handbook_md import PAGES, render, slug
from app.lang import current, de_en

bp = Blueprint("handbook", __name__, url_prefix="/docs")

TITLES = {"index": ("Das Konzept", "The concept"), "guides": ("Anleitungen", "Guides"),
          "build": ("Eine Befragung aufbauen", "Building a survey"), "field": ("Feldphase", "Fieldwork"),
          "analysis": ("Auswerten", "Analysis"), "kinds": ("Weitere Befragungsarten", "Other kinds of survey"),
          "more": ("Weitere Seiten", "Other pages"), "ai": ("KI in Askurious", "AI in Askurious"),
          "dialogs": ("Seiten und Dialoge", "Pages and dialogs")}
# the pages that were "Seiten und Dialoge" until 2026-10-22, in their order (the overview /docs/dialogs lists them)
DIALOG_PAGES = ("build", "field", "analysis", "kinds", "more")
_cache = {}
_anchor_cache = {}

# "Hilfe zu dieser Seite" in the menu "Hilfe und Werkzeuge" (UI/UX audit 2026-10-16, 17 and 156): the section of
# the handbook for the tab that is open (help_url finds the page that has the heading, page_of), as (German anchor, English anchor). TAB_ANCHORS per kind of survey
# and key of active_tab (surveys/_tabs.html), PAGE_ANCHORS per blueprint for the pages outside a survey. A test
# (tests/platform/test_handbook.py) asks for every tab of surveys/_tabs.html an anchor in both languages: a PR that
# changes the tabs or the build of a page changes the handbook with it (CLAUDE.md).
TAB_ANCHORS = {
    "survey": {"settings": ("4-einstellungen", "4-settings"), "plan": ("5-studienplan", "5-research-plan"),
               "editor": ("6-fragebogen-editor", "6-questionnaire-editor"), "flow": ("7-ablauf", "7-flow"),
               "quotas": ("8-quoten", "8-quotas"), "translation": ("9-übersetzung", "9-translation"),
               "schedule": ("zeitplan-einer-tagebuchstudie", "schedule-of-a-diary-survey"),
               "field": ("11-datenerhebung", "11-data-collection"),
               "quality": ("12-qualitätssicherung", "12-quality-assurance"), "results": ("13-ergebnisse", "13-results"),
               "boards": ("14-dashboards", "14-dashboards"), "data": ("15-export-und-offene-daten", "15-export-and-open-data")},
    # one section per tab for the live poll and the interview guide too (concept "Befragungstypen im Vergleich"
    # 2026-10-17, T15)
    "live": {"settings": ("einstellungen-einer-live-abstimmung", "settings-of-a-live-poll"),
             "editor": ("folien", "slides"), "present": ("präsentieren", "present"),
             "results": ("ergebnisse-einer-live-abstimmung", "results-of-a-live-poll"),
             "data": ("export-einer-live-abstimmung", "export-of-a-live-poll")},
    "interview": {"settings": ("einstellungen-eines-interviewleitfadens", "settings-of-an-interview-guide"),
                  "editor": ("leitfaden", "guide"), "interviews": ("interviews", "interviews"),
                  "analysis": ("ergebnisse-eines-interviewleitfadens", "results-of-an-interview-guide"),
                  "data": ("export-eines-interviewleitfadens", "export-of-an-interview-guide")},
}
# a diary survey has the tabs of a questionnaire; its "Datenerhebung" (joining, participants) has a section of its own
TAB_ANCHORS["diary"] = {**TAB_ANCHORS["survey"],
                        "field": ("beitreten-und-teilnehmende-einer-tagebuchstudie", "joining-and-participants-of-a-diary-survey")}
PAGE_ANCHORS = {"dashboard": ("1-meine-befragungen", "1-my-surveys"), "wizard": ("2-eine-befragung-anlegen", "2-creating-a-survey"),
                "constructs": ("konstrukte", "constructs"), "groups": ("gruppen", "groups"), "designs": ("designs", "designs"),
                "samplesize": ("stichprobengröße", "sample-size"), "accounts": ("konto", "account"), "ai_access": ("konto", "account"),
                "element_types": ("elementtypen", "element-types"), "learning": ("21-lernhilfen", "21-learning-aids")}


def survey_kind(survey):
    return ("live" if survey.live is not None else "interview" if survey.interview is not None
            else "diary" if survey.sampling is not None else "survey")


def note_tab(survey, tab):
    """Called by surveys/_tabs.html: remembers the open tab for the header, which base.html draws after the page."""
    g.help_tab = (survey_kind(survey), tab)
    return ""


def help_url():
    """The address of the handbook section on the page being shown, or None when there is none."""
    kind, tab = g.get("help_tab") or (None, None)
    pair = TAB_ANCHORS.get(kind, {}).get(tab) if kind else PAGE_ANCHORS.get(request.blueprint)
    if not pair:
        return None
    anchor = pair[0] if current() == "de" else pair[1]
    return url_for("handbook.page", name=page_of(anchor) or "dialogs") + "#" + anchor


def anchors(lang):
    """{heading id: page} over all pages in one language, the {kind:…} marks taken off as on the page; worked out
    again only when a page changed (base.html asks on every page, help_url)."""
    texts = tuple(_source(name, lang) for name in PAGES)
    key = (lang, tuple(id(text) for text in texts))  # _source hands out the same string while its file is unchanged
    hit = _anchor_cache.get(lang)
    if hit is not None and hit[0] == key:
        return hit[1]
    out = {}
    for name in PAGES:
        for line in _source(name, lang).splitlines():
            if re.match(r"^#{1,4}\s", line):
                out.setdefault(slug(_KIND_MARK.sub("", line).lstrip("#").strip()), name)
    _anchor_cache[lang] = (key, out)
    return out


def page_of(anchor, lang=None):
    """The page that has the heading `anchor` in the reader's language (or `lang`), or None."""
    return anchors(lang or current()).get(anchor)


def _source(name, lang):
    path = os.path.join(current_app.root_path, "data", "handbook", lang, name + ".md")
    if not os.path.exists(path):
        other = "en" if lang == "de" else "de"
        path = os.path.join(current_app.root_path, "data", "handbook", other, name + ".md")
    stamp = os.stat(path).st_mtime_ns
    hit = _cache.get(path)
    if hit is None or hit[0] != stamp:  # rendered again only when the file changed
        with open(path, encoding="utf-8") as f:
            _cache[path] = hit = (stamp, f.read())
    return hit[1]


def _branded(source):
    """The page's heading with the brand of the domain ("POLLtergeist: das Konzept"), as on every other page; the
    text keeps the name of the software, Askurious (UI audit 2026-10-12, U50; decision of Ralf)."""
    head, sep, rest = source.partition("\n")
    return brand.apply(head) + sep + rest if head.startswith("# ") else source


_KIND_MARK = re.compile(r"\s*\{kind:(\w+)\}\s*$")


def _for_kinds(source):
    """The text without the parts about survey types that are "off" for the signed-in reader (app/kind_access.py): a
    line ending in {kind:live} (a table row) goes alone, a heading with it goes with its section. The marks never
    show; readers without an account see everything."""
    from flask_login import current_user

    from app import kind_access

    seen = (set(kind_access.visible_kinds(current_user)) if getattr(current_user, "is_authenticated", False)
            else set(kind_access.KINDS))
    out, skip_level = [], None
    for line in source.splitlines():
        level = len(line) - len(line.lstrip("#")) if line.startswith("#") else None
        if skip_level is not None:
            if level is None or level > skip_level:
                continue
            skip_level = None
        mark = _KIND_MARK.search(line)
        if mark:
            if mark.group(1) not in seen:
                if level is not None:
                    skip_level = level
                continue
            line = line[:mark.start()]
        out.append(line)
    return "\n".join(out)


def _show(name):
    if name not in PAGES:
        abort(404)
    body, toc = render(_branded(_for_kinds(_source(name, current()))))
    pages = [(p, brand.apply(de_en(*TITLES[p]))) for p in PAGES]
    return render_template("handbook/page.html", name=name, body=body, toc=toc, pages=pages,
                           title=brand.apply(de_en(*TITLES[name])))


@bp.route("/")
def index():
    return _show("index")


def _overview():
    """/docs/dialogs, the former single page "Seiten und Dialoge": its pages with their chapters; an old link with a
    section (#4-einstellungen) is sent on to the page that has it (static/handbook.js, data-handbook-anchors)."""
    import json

    lang = current()
    parts = []
    for p in DIALOG_PAGES:
        _body, toc = render(_for_kinds(_source(p, lang)))
        parts.append((p, brand.apply(de_en(*TITLES[p])), toc))
    targets = {a: url_for("handbook.page", name=p) for a, p in anchors(lang).items() if p in DIALOG_PAGES}
    pages = [(p, brand.apply(de_en(*TITLES[p]))) for p in PAGES]
    return render_template("handbook/overview.html", name="dialogs", parts=parts, pages=pages, toc=[],
                           targets=json.dumps(targets, ensure_ascii=False), title=brand.apply(de_en(*TITLES["dialogs"])))


@bp.route("/<name>")
def page(name):
    if name == "index":
        abort(404)
    if name == "dialogs":
        return _overview()
    return _show(name)
