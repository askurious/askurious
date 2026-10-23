"""UI/UX audit 2026-10-09, the five high findings (Sofortpaket): X17 conditions only on code answers, X32 codes
not mirrored on right-to-left pages, X52 hidden column heads stay inside their scrolling table, X53 the overview
of differences ranks Cramér's V corrected for its degrees of freedom, X67 the legal links of the footer never cut."""
import re
from pathlib import Path

from app import conditions, dummy, flow
from app.extensions import db
from app.items import REGISTRY
from app.models import Response, Survey
from tests.conftest import add, experiments_on

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")


# --- X17 ----------------------------------------------------------------------------------------------------------

def _option_types():
    return sorted(k for k, kind in REGISTRY.items() if callable(getattr(kind, "options", None)))


def test_x17_every_offered_answer_can_hold_with_a_real_answer(app, client, survey):
    """Every element type with answer options in one survey, dummy answers for it: each answer a condition (or a
    Teilquote) offers to tick is met by some real answer. A Q-Sort or a card sorting offered its statements as
    answers, but its answer is a placement: such a condition never held."""
    from app.experimental import FEATURES

    experiments_on(client, *[f[0] for f in FEATURES])
    types = _option_types()
    assert {"single_choice", "multiple_choice", "q_sort", "card_sort", "ranking"} <= set(types)
    for key in types:
        add(client, key)
    with app.app_context():
        s = db.session.get(Survey, 1)
        made = {i.type: i for b in s.blocks for i in b.items}
        assert set(types) <= set(made), set(types) - set(made)
        offered = {ref: d for ref, d in conditions.describe(s, everything=True).items()
                   if ref.isdigit() and d["kind"] == "choice"}
        kinds = {i.type for i in made.values() if str(i.id) in offered}
        assert kinds == {k for k in types if getattr(REGISTRY[k], "code_answer", True)}
        for key in ("q_sort", "card_sort", "ranking", "constant_sum", "position2d"):
            if key in made:
                ref = str(made[key].id)
                assert conditions.describe(s, everything=True)[ref]["kind"] == "other"  # only answered or not
                assert ref not in conditions.describe(s)  # not for a Teilquote
                assert flow.answer_codes(made[key]) == []
        dummy.generate(s, 150, seed=7)
        answers = [{int(k): v for k, v in r.answers.items() if k.isdigit()}  # as the flow reads them (runtime)
                   for r in db.session.scalars(db.select(Response).filter_by(survey_id=1))]
        assert answers
        for ref, d in offered.items():
            made_item = next(i for i in made.values() if str(i.id) == ref)
            op = "in" if d["multi"] else "eq"
            for code, _ in d["options"]:
                cond = {"item": int(ref), "op": op, "value": code}
                assert any(flow.holds(cond, a, s) for a in answers), (made_item.type, ref, code)


# --- X32 ----------------------------------------------------------------------------------------------------------

def test_x32_codes_run_left_to_right_on_an_arabic_page(app, client, survey):
    """On a right-to-left page the deletion code was shown as "9R7J-MJMU-9L42-24": copied by hand, it deleted
    nothing. The code, the survey ID and the example of the code are isolated left to right."""
    from tests.privacy.test_erasure import CODE, META, build, take

    uid = build(app, client, start=False)
    client.post("/surveys/1/meta", data={**META, "languages": ["de", "en", "ar"], "erase_code_shown": "1", "erase_code": "1"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        assert "ar" in db.session.get(Survey, 1).languages
    p = app.test_client()
    code = CODE.search(take(p, uid)).group(1)
    privacy = p.get(f"/s/{uid}/privacy?lang=ar").data.decode()
    assert 'dir="rtl"' in privacy
    assert code not in privacy  # SP13 TN-7: only the recent end page shows it.
    end = p.get(f"/s/{uid}?lang=ar").data.decode()
    assert f'class="p-erase-code" dir="ltr">{code}<' in end
    assert f'<code dir="ltr">{uid}</code>' in privacy
    erase = app.test_client().get(f"/s/{uid}/delete?lang=ar").data.decode()
    assert 'dir="rtl"' in erase and f'(<code dir="ltr">{uid}</code>)' in erase
    assert re.search(r'<input type="text" name="code"[^>]*dir="ltr">', erase)
    assert re.search("⁦" + r"\d+-[2-9A-Z]{4}-[2-9A-Z]{4}-[2-9A-Z]{4}" + "⁩", erase)  # the example in its sentence
    css = (ROOT / "app" / "static" / "participant.css").read_text(encoding="utf-8")
    rule = re.search(r"\n\.p-erase-code \{([^}]*)\}", css).group(1)
    assert "direction: ltr" in rule and "unicode-bidi: isolate" in rule
    assert '[dir="rtl"] code { direction: ltr; unicode-bidi: isolate; }' in css
    # copied together with the invisible marks around it, the code still deletes
    # with the tick, which the server asks for now (novalidate; UI/UX audit 2026-10-16, 101)
    done = app.test_client().post(f"/s/{uid}/delete?lang=ar", data={"code": "⁦" + code + "⁩", "sure": "1"}).data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Response).filter_by(survey_id=1, is_preview=False)) == 0, re.findall(r"p-error[^<]*<", done)


# --- X53 ----------------------------------------------------------------------------------------------------------

class _Item:
    def __init__(self, id_, code):
        self.id, self.code = id_, code


def test_x53_cramers_v_is_corrected_before_it_is_ranked():
    """Federal state (17 answers) by two groups, n = 155: V = 0.33 at p = .431 is what chance alone gives
    (√(16/155) ≈ 0.32); it stood second, before a real small difference. Corrected (Bergsma, 2013) it is about
    nothing and goes last; every row has its word."""
    from app import compare
    from app import group_tests as gt

    chi2 = 0.33 ** 2 * 155
    state = {"chi2": chi2, "df": 16, "n": 155, "v": 0.33, "p": 0.431, "shape": [17, 2], "cochran": False}
    v, w = compare.corrected(state)
    phi2 = max(0, chi2 / 155 - 16 / 154)
    assert abs(w - phi2 ** 0.5) < 1e-12 and v < 0.1
    assert abs(v - (phi2 / min(17 - 256 / 154 - 1, 2 - 1 / 154 - 1)) ** 0.5) < 1e-12
    cmp = {"items": {
        1: [{"kind": "nominal", "groups": [("1", "a"), ("2", "b")], "code": "land", "label": "", "test": state}],
        2: [{"kind": "ordinal", "groups": [("1", "a"), ("2", "b")], "code": "bequem", "label": "",
             "test": {"r": 0.19, "p": 0.038}}],
        3: [{"kind": "metric", "groups": [("1", "a"), ("2", "b")], "code": "preis", "label": "", "test": {"d": 1.4, "p": 0.001}}],
        4: [{"kind": "multi", "groups": [("1", "a"), ("2", "b")], "code": "medien", "label": "", "pass": None,
             "rows": [{"label": "x", "test": {"chi2": 0.5, "df": 1, "n": 155, "v": 0.06, "p": 0.48, "shape": [2, 2]}},
                      {"label": "y", "test": {"chi2": 3.0, "df": 1, "n": 155, "v": 0.14, "p": 0.08, "shape": [2, 2]}}]}]}}
    items = [{"item": _Item(i, c), "question": c} for i, c in ((1, "land"), (2, "bequem"), (3, "preis"), (4, "medien"))]
    rows = compare.overview(cmp, items)["rows"]
    assert [r["code"] for r in rows] == ["preis", "bequem", "medien", "land"]
    assert [r["verdict"] for r in rows] == ["large", "small", "none", "none"]  # δ* 1.4: r = 1.4 / √5.96 = 0.57
    assert rows[3]["value"] == 0.33 and rows[3]["corrected"] == v      # the reported V, its corrected value beside it
    assert rows[2]["option"] == {"label": "y", "of": 2} and rows[1]["corrected"] is None
    assert compare.verdict(0.6, 0.01) == "large" and compare.verdict(0.35, 0.01) == "medium"
    assert compare.verdict(0.35, 0.05) == "none" and compare.verdict(0.05, 0.001) == "none"
    # the χ² test gives the shape of the table it counted (rows and columns with answers)
    assert gt.chi2_test([[10, 5], [3, 12], [0, 0], [4, 4]])["shape"] == [3, 2]
    assert compare.BERGSMA.startswith("Bergsma, W. (2013).") and "https://doi.org/10.1016/j.jkss.2012.10.002" in compare.BERGSMA


# --- X52 ----------------------------------------------------------------------------------------------------------

def test_x52_a_scrolling_table_holds_its_hidden_column_heads():
    """A visually hidden span in a column head is position: absolute. Without a positioned .res-scroll it left the
    scrolling table and made the page "Datenerhebung" 943 px wide at 390 px (the browser shrank it to 41 %)."""
    rule = re.search(r"\n\.res-scroll \{([^}]*)\}", CSS).group(1)
    assert "overflow-x: auto" in rule and "position: relative" in rule
    links = (ROOT / "app" / "templates" / "surveys" / "_links.html").read_text(encoding="utf-8")
    assert 'class="res-scroll"' in links and "visually-hidden" in links


# --- X67 ----------------------------------------------------------------------------------------------------------

def _footer(html):
    return html[html.index('<footer class="site-footer">'):html.index("</footer>")]


def test_x67_the_legal_links_stand_apart_and_never_shrink(app, client, survey):
    """The sentence "Sie haben … offene Umfragen …" pushed "Impressum & Datenschutz" out of the fixed footer (cut at
    1280 and 1400 px, out of sight at 390 px). The links now stand in their own part that does not shrink; the sentence
    gives way first; below 600 px the footer is not fixed, may take two lines and leaves the sentence out."""
    for page in (client.get("/dashboard/").data.decode(), app.test_client().get("/").data.decode()):
        footer = _footer(page)
        legal = re.search(r'<span class="footer-legal">(.*?)</span>', footer, re.S).group(1)
        assert 'href="/terms/"' in legal and 'href="/imprint"' in legal and "Impressum &amp; Datenschutz" in legal
        stats = footer[footer.index('class="footer-stats"'):footer.index('class="footer-legal"')]
        assert "/imprint" not in stats and "/terms/" not in stats
        assert footer.index('class="footer-owner"') < footer.index('class="footer-stats"') < footer.index('class="footer-legal"')
    assert "laufende Befragung" not in _footer(client.get("/dashboard/").data.decode())  # counts gone (Ralf, 2026-10-17)
    rules = dict(re.findall(r"\n(\.footer-[a-z]+) \{([^}]*)\}", CSS))
    assert "flex: none" in rules[".footer-legal"]
    assert "flex: 1 1 0" in rules[".footer-stats"] and "min-width: 0" in CSS[CSS.index(".footer-owner, .footer-stats"):][:120]
    phone = CSS[CSS.index("/* on the phone: not fixed"):]
    phone = phone[:phone.index("\n}\n")]
    assert "@media (max-width: 600px)" in phone and "position: static" in phone and "height: auto" in phone
    assert ".footer-stats { display: none; }" in phone and "flex-wrap: wrap" in phone and "body { padding-bottom: 0; }" in phone
