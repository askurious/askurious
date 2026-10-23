"""The handbook under /docs/ (app/handbook.py, app/handbook_md.py; Philipp, 2026-10-10): its pages in German and
English (since 2026-10-22 "Seiten und Dialoge" split into five pages with guides in front, Ralf), without an account, reached by the book in the header; its Markdown rendered with everything escaped."""
import os

from app import handbook_md


def test_the_pages_answer_in_both_languages_without_an_account(app):
    c = app.test_client()
    for path in ("/docs/", "/docs/dialogs", "/docs/ai") + tuple("/docs/" + p for p in handbook_md.PAGES if p != "index"):
        de = c.get(path, headers={"Accept-Language": "de"})
        en = app.test_client().get(path, headers={"Accept-Language": "en"})
        assert de.status_code == en.status_code == 200
    page = c.get("/docs/", headers={"Accept-Language": "de"}).data.decode()
    assert "Das Konzept" in page and 'class="handbook-callout"' in page and "Grundsätze" in page
    page = app.test_client().get("/docs/build", headers={"Accept-Language": "en"}).data.decode()
    assert 'id="4-settings"' in page and "/settings.jpg" in page
    assert app.test_client().get("/docs/nothing").status_code == 404


def test_the_book_is_in_the_header_signed_in_and_not(client, app):
    assert 'href="/docs/"' in client.get("/dashboard/").data.decode()
    assert 'href="/docs/"' in app.test_client().get("/").data.decode()


def test_every_image_and_page_link_exists(app):
    root = app.root_path
    for lang in ("de", "en"):
        for name in handbook_md.PAGES:
            with open(os.path.join(root, "data", "handbook", lang, name + ".md"), encoding="utf-8") as f:
                text = f.read()
            for src in __import__("re").findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
                base = src.rsplit("/", 1)[-1]  # in the language of the page, or without a language
                assert (os.path.exists(os.path.join(root, "static", "handbook", lang, base))
                        or os.path.exists(os.path.join(root, "static", "handbook", base))), (lang, name, src)


def test_markdown_is_escaped_and_rendered(app):
    with app.test_request_context():
        html, toc = handbook_md.render("## Eins & zwei\n\nText <script>x</script> mit **fett** und `code`.\n\n"
                                       "| a | b |\n|---|---|\n| 1 | 2 |\n\n- eins\n- zwei\n\n> Kasten")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert toc == [("eins--zwei", "Eins & zwei")] and "<strong>fett</strong>" in html and "<code>code</code>" in html
    assert "<table" in html and "<ul><li>eins</li>" in html and 'class="handbook-callout"' in html


def _anchors(app, lang):
    """The ids of the headings of all pages in one language, the {kind:…} marks taken off as on the page."""
    from app.handbook import _KIND_MARK

    out = set()
    for name in handbook_md.PAGES:
        with open(os.path.join(app.root_path, "data", "handbook", lang, name + ".md"), encoding="utf-8") as f:
            out |= {handbook_md.slug(_KIND_MARK.sub("", line).lstrip("#").strip())
                    for line in f if __import__("re").match(r"^#{1,4}\s", line)}
    return out


def test_every_tab_has_a_section_in_both_languages(app):
    """UI/UX audit 2026-10-16, 156: the handbook bound to the tabs. Every tab of surveys/_tabs.html (its active_tab key)
    has an anchor in app/handbook.py, and the anchor is a heading of the German and of the English handbook; so a PR
    that changes the tabs or the build of a page changes the handbook with it (CLAUDE.md)."""
    import re

    from app.handbook import PAGE_ANCHORS, TAB_ANCHORS

    with open(os.path.join(app.root_path, "templates", "surveys", "_tabs.html"), encoding="utf-8") as f:
        tabs = f.read()
    # one list of groups per kind, in the order live, interview, questionnaire (and diary) (concept "Befragungstypen im
    # Vergleich" 2026-10-17, T1)
    live = tabs.split('{% set nav_label = de_en("Bereiche der Live-Abstimmung"', 1)[1]
    live, interview = live.split('{% set nav_label = de_en("Bereiche des Interviewleitfadens"', 1)
    interview, survey_part = interview.split('{% set nav_label = de_en("Bereiche der Befragung"', 1)
    found = {kind: set(re.findall(r'\("(\w+)", url_for\(', part))
             for kind, part in (("live", live), ("interview", interview), ("survey", survey_part))}
    found["survey"] |= {"quotas"}
    others = set(re.findall(r'active_tab == "(\w+)"', tabs))
    assert {"settings", "editor", "field", "results", "data", "schedule"} <= found["survey"]
    assert {"settings", "editor", "present", "results", "data"} <= found["live"]
    assert {"settings", "editor", "interviews", "analysis", "data"} <= found["interview"]
    for kind, keys in found.items():
        assert keys <= set(TAB_ANCHORS[kind]), (kind, keys - set(TAB_ANCHORS[kind]))
    assert others <= set(TAB_ANCHORS["survey"]) | set(TAB_ANCHORS["live"]) | set(TAB_ANCHORS["interview"])
    de, en = _anchors(app, "de"), _anchors(app, "en")
    for kind in TAB_ANCHORS.values():
        for tab, (a_de, a_en) in kind.items():
            assert a_de in de and a_en in en, (tab, a_de, a_en)
    for blueprint, (a_de, a_en) in PAGE_ANCHORS.items():
        assert a_de in de and a_en in en, (blueprint, a_de, a_en)


def _first_help_entry(page):
    menu = page.split('class="menu nav-menu help-menu"')[1].split("</details>")[0]
    return menu.split('<div class="nav-menu-panel">')[1].split("</a>")[0]


def test_help_on_this_page_opens_the_section_of_the_tab(client, survey):
    """UI/UX audit 2026-10-16, 17: "Hilfe zu dieser Seite" is the first entry of the menu "Hilfe und Werkzeuge", for
    the open tab of a survey and for the pages outside one; a page without a section has none."""
    first = _first_help_entry(client.get("/dashboard/").data.decode())
    assert "Hilfe zu dieser Seite" in first and "/docs/build#1-meine-befragungen" in first
    first = _first_help_entry(client.get(f"/surveys/{survey}/quality").data.decode())
    assert "/docs/field#12-qualit" in first
    first = _first_help_entry(client.get(f"/surveys/{survey}/editor/").data.decode())
    assert "/docs/build#6-fragebogen-editor" in first
    assert "Hilfe zu dieser Seite" not in _first_help_entry(client.get("/docs/").data.decode())


def test_old_links_to_pages_and_dialogs_still_lead_somewhere(app):
    """Ralf, 2026-10-22: "Seiten und Dialoge" split into five pages. /docs/dialogs lists them and carries the sections
    for static/handbook.js, which sends an old link with a section on; a page link "dialogs.md#…" in the Markdown and
    "Hilfe zu dieser Seite" go straight to the page that has the heading."""
    import json

    from app import handbook

    page = app.test_client().get("/docs/dialogs", headers={"Accept-Language": "de"}).data.decode()
    assert "handbook.js" in page and 'href="/docs/build#4-einstellungen"' in page
    targets = json.loads(__import__("html").unescape(page.split('data-handbook-anchors="')[1].split('"')[0]))
    assert targets["4-einstellungen"] == "/docs/build" and targets["11-datenerhebung"] == "/docs/field"
    with app.test_request_context(headers={"Accept-Language": "de"}):
        assert handbook.page_of("13-ergebnisse", "de") == "analysis" and handbook.page_of("8-quotas", "en") == "build"
        html, _toc = handbook_md.render("[x](dialogs.md#12-qualitätssicherung)")
        assert 'href="/docs/field#12-qualitätssicherung"' in html


def test_screenshots_in_the_language_of_the_reader(app):
    """A screenshot comes from static/handbook/<lang>/ when it was taken in that language (tools/handbook_shots),
    else from static/handbook/."""
    with app.test_request_context(headers={"Accept-Language": "en"}):
        from app.lang import current

        name = "settings.jpg"
        own = os.path.join(app.static_folder, "handbook", current(), name)
        expected = f"handbook/{current()}/{name}" if os.path.exists(own) else "handbook/" + name
        assert handbook_md.shot("img/" + name) == expected
        assert handbook_md.shot("img/no-such-picture.jpg") == "handbook/no-such-picture.jpg"
