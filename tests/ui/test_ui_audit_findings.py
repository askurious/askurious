"""Ui: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.helpers.audit_audit_2026_10_06_f import WAIT, _likert, _scale_rows, draws  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_c import diary  # noqa: F401
from tests.helpers.audit_audit_small import Survey, db, design, ranking  # noqa: F401
from tests.helpers.audit_audit_small2 import Item, SimpleNamespace, _errors, add, kind_of  # noqa: F401
from tests.helpers.audit_cr8_a import app, client, survey  # noqa: F401
from tests.helpers.audit_cr8_h import _return_flow, date, external, quote  # noqa: F401
from tests.helpers.audit_cr8_j import DEAD_CLASSES, TEMPLATES, _rules, all_app_text, re, read  # noqa: F401
from tests.helpers.audit_review2_reste import HANDLER, ROOT, SCRIPT, glob, os  # noqa: F401


# --- from tests/audits/test_audit_2026_10_06_f.py: Audit 2026-10-06 (security and performance), package F: performance of the evaluation pages. ---


def test_p1_small_samples_wait_for_nothing(app, client, survey, draws):
    _likert(app, client, _scale_rows(n=20))                                  # below 30 cases: no bootstrap at all
    html = client.get("/surveys/1/results").data.decode()
    assert WAIT not in html and "data-omega-slot" not in html and not draws
    assert "Perzentil-Bootstrap mit 200 Ziehungen" not in html


# --- from tests/audits/test_audit_2026_10_12_c.py: Audit data and calculations 2026-10-12, package C (diary study): E-1, E-2, D-1 to D-7, D-10, D-12 to D-15 and ---


def test_icc_negative_is_shown_as_zero(app, client):
    assert diary.icc1({"a": [1, 2], "b": [1, 2], "c": [2, 1]}) < 0
    from flask import render_template

    with app.test_request_context():
        html = render_template("diary/_results_time.html", diary_time=[{
            "code": "x", "label": "X", "module": "M", "n": 6, "persons": 3, "icc": -1.0, "by_day": [(1, 1.5, 3)],
            "by_hour": [(10, 1.5, 3)]}])
        assert "negativ geschätzt" in html


# --- from tests/audits/test_audit_small.py: Smaller points of the data audit: codebook values, orders in loops, screen-out times, long JSON in .sav. ---


def test_order_column_of_a_randomized_item_counts_as_variable_name(app, client, survey):
    ranking(client, last_code="order")                              # prio_order: option column and shown order
    with app.app_context():
        messages = [f.message for f in design.check(db.session.get(Survey, 1))]
        assert any("prio_order" in m and "mehrfach" in m for m in messages)


# --- from tests/audits/test_audit_small2.py: Small fixes from the audit of 2026-10-01 (S4, S6, S7, S8, S10, S11, D-1, D-3, D-4). ---


def test_answer_codes_that_become_variable_names_are_checked(app, client, survey):
    add(client, "multiple_choice"), add(client, "single_choice")
    with app.app_context():
        for item in db.session.scalars(db.select(Item).where(Item.id > 1)):
            item.config = {**item.config, "options": [{"id": "a", "code": "18-29"}, {"id": "b", "code": "über 30"}]}
        db.session.commit()
        codes = {i.type: i.code for i in db.session.scalars(db.select(Item))}
    errors = _errors(app)
    assert any(f"({codes['multiple_choice']}):" in e and "Teil der Variablennamen" in e and "18-29" in e for e in errors)
    assert not any(f"({codes['single_choice']}):" in e and "Variablennamen" in e for e in errors)  # codes are values there


def test_attribute_codes_are_unique_and_no_long_format_columns():
    kind = kind_of("conjoint")

    def item(*codes):
        attrs = [{"id": f"a{n}", "code": c, "levels": [{"id": f"a{n}l0"}, {"id": f"a{n}l1"}]} for n, c in enumerate(codes)]
        labels = {l["id"]: "x" for a in attrs for l in a["levels"]}
        return SimpleNamespace(id=None, code="cbc", config={"attributes": attrs}, text=lambda key: labels)

    assert kind.validate_attributes(item("look", "price")) == []
    assert "Variablennamen der Merkmale sind nicht eindeutig" in kind.validate_attributes(item("look", "Look"))
    assert any("task" in p and "Langformat" in p for p in kind.validate_attributes(item("task", "look")))
    assert any("look_label" in p for p in kind.validate_attributes(item("look", "look_label")))


# --- from tests/audits/test_cr8_a.py: Code review 2026-10-07, package A "Falsche oder verlorene Daten": regression tests for T1, E1, E2, E8, E9, E10, ---


def test_r2_own_labels_of_every_type_have_text_keys(app, client, survey):
    from tests.exports.test_transfer import _everything

    _everything(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        seen = set()
        for item in (i for b in s.blocks for i in b.items):
            for column, _ in item.kind.export_columns(item):
                found = item.kind.result_column(item, column)
                labels = found[2] if found else None
                if labels:
                    seen.add(item.type)
                    assert all(isinstance(k, str) for k in labels), (item.type, column)
        assert "geolocation" in seen


# --- from tests/audits/test_cr8_h.py: Code review 2026-10-07, package H (AI calls and the external signature): I1 no SDK retries and limits below the ---


def test_i8_return_link_with_version_2_and_the_transition(app, client, survey, monkeypatch):
    key, go, sig = _return_flow(app, client)
    v2 = lambda t, text: sig(key, f"sig_v=2&t={quote(t, safe='')}" + text)  # noqa: E731
    ok = go(lambda t: f"&score=17&label=gut+gemacht&other=1&sig_v=2&sig={v2(t, '&score=17&label=gut%20gemacht')}")
    assert ok["v"] == {"score": 17, "label": "gut gemacht"} and ok["sig"] == 1
    # a value that carried "&score=" in version 2 cannot move into another value
    moved = go(lambda t: f"&label=x&score=5&sig_v=2&sig={v2(t, '&score=3&label=x%26score%3D5')}")
    assert moved["v"] == {} and moved["sig"] == 0
    # version 1 is taken until V1_UNTIL, afterwards not
    old = go(lambda t: f"&score=5&sig={sig(key, f't={t}&score=5')}")
    assert old["v"] == {"score": 5} and old["sig"] == 1
    monkeypatch.setattr(external, "V1_UNTIL", date(2020, 1, 1))
    late = go(lambda t: f"&score=5&sig={sig(key, f't={t}&score=5')}")
    assert late["v"] == {} and late["sig"] == 0
    still = go(lambda t: f"&score=6&sig_v=2&sig={v2(t, '&score=6')}")
    assert still["v"] == {"score": 6} and still["sig"] == 1
    unknown = go(lambda t: f"&score=6&sig_v=3&sig={v2(t, '&score=6')}")
    assert unknown["v"] == {} and unknown["sig"] == 0


# --- from tests/audits/test_cr8_j.py: Code review 2026-10-07, package J (tidying up): dead code, dead templates and CSS, one counter for the limits. ---


def test_g9_the_unused_options_template_is_gone():
    assert not (TEMPLATES / "surveys" / "_fields_options.html").exists()
    assert "_fields_options" not in all_app_text((".py", ".html"))


def test_g10_classes_without_a_user_are_not_in_app_css():
    css = read("app.css")
    for name in DEAD_CLASSES:
        assert not re.search(r"\." + re.escape(name) + r"(?![\w-])", css), name


def test_g12_a_selector_written_twice_does_not_set_a_value_that_never_applies():
    # only rules with a single selector: in a group, the first rule still applies to the other selectors
    for name in ("app.css", "participant.css"):
        seen = {}
        for ctx, sel, decls in _rules(read(name)):
            if "," in sel:
                continue
            for earlier in seen.get((ctx, sel), []):
                lost = [k for k in earlier if k in decls and earlier[k] != decls[k] and "!important" not in earlier[k]]
                assert not lost, (name, sel, lost)
            seen.setdefault((ctx, sel), []).append(decls)


# --- from tests/audits/test_review2_reste.py: Second code review 2026-10-03, package 7 (the rest): no inline scripts and handlers, so the CSP allows scripts ---


def test_no_template_has_an_inline_handler_or_script():
    handlers, scripts = [], []
    for f in glob.glob(os.path.join(ROOT, "app/templates/**/*.html"), recursive=True):
        text = open(f, encoding="utf-8").read()
        name = os.path.relpath(f, ROOT)
        handlers += [name for _ in HANDLER.finditer(text)]
        # a script is a file (src=) or data for one (type="application/json"); never code in the page
        scripts += [name for m in SCRIPT.finditer(text) if "src=" not in m.group(1) and "application/json" not in m.group(1)]
    assert not handlers, "Inline-Handler (on…=): " + ", ".join(sorted(set(handlers)))
    assert not scripts, "Inline-Skript: " + ", ".join(sorted(set(scripts)))
    # nor written by the scripts themselves
    for f in glob.glob(os.path.join(ROOT, "app/static/*.js")):
        assert not re.search(r"""\son(click|change|submit|input|load|error)=['"\\]""", open(f, encoding="utf-8").read()), f
