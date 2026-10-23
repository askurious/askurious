"""Piping: answers to earlier questions in later texts ({{code}}, app/piping.py)."""
from app import design, piping
from app.extensions import db
from app.items.base import Typed, fill, inline_rich, rich
from app.models import Item, Survey
from tests.conftest import CONSENT_OK, add, fill_consent


def _set(item_id, code=None, config=None, **texts):
    item = db.session.get(Item, item_id)
    if code:
        item.code = code
    if config:
        item.config = {**item.config, **config}
    item.texts = {**(item.texts or {}), "de": {**(item.texts or {}).get("de", {}), **texts}}
    return item


def build(app, client):
    """Page 1: brand (single choice, "Andere" with a text), name (text); page 2: Likert, semantic differential
    and an info text that show them."""
    fill_consent(client)
    for kind in ("single_choice", "text_input", "page_break", "likert", "semantic", "info", "multiple_choice"):
        add(client, kind)
    with app.app_context():
        _set(2, "marke", config={"options": [{"id": "a", "code": "1"}, {"id": "b", "code": "2"},
                                             {"id": "c", "code": "9", "text": True}]},
             question="Welche Marke?", options={"a": "**Fair**phone", "b": "Pixel", "c": "Andere"})
        _set(3, "name", question="Ihr Name?")
        likert = _set(5, "zuv", question="Zu {{marke}}:", labels=["gar nicht {{marke}}", "", "", "", "sehr"])
        sid = likert.config["statements"][0]["id"]
        _set(5, statements={sid: "{{marke}} ist zuverlässig."})
        pid = _set(6).config["pairs"][0]["id"]
        _set(6, "sd", question="Wie ist es?", pairs={pid: {"left": "{{marke|Das Produkt}} ist teuer", "right": "günstig"}})
        _set(7, "hallo", question="Hallo {{name}}, **schön** dass Sie {{ marke }} nutzen.")
        _set(8, "mehr", config={"options": [{"id": "x", "code": "1"}, {"id": "y", "code": "2"}, {"id": "z", "code": "3"}]},
             question="Was noch?", options={"x": "A", "y": "B", "z": "C"})
        db.session.commit()


def start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    return p, uid


def test_answers_appear_in_texts_statements_labels_and_poles(app, client, survey):
    build(app, client)
    p, uid = start(app, client)
    first = p.get(f"/s/{uid}").data.decode()
    assert "{{" not in first
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "9", "i2_t_c": "Shift<b>6</b>", "i3": "**Ada**"})
    html = p.get(f"/s/{uid}").data.decode()
    assert "Shift&lt;b&gt;6&lt;/b&gt; ist zuverlässig." in html  # the typed text of "Andere", escaped
    assert "gar nicht Shift&lt;b&gt;6&lt;/b&gt;" in html and "Zu Shift&lt;b&gt;6&lt;/b&gt;:" in html
    assert "Shift&lt;b&gt;6&lt;/b&gt; ist teuer" in html
    # the info text keeps its own formatting, the typed name stays as typed
    assert "Hallo **Ada**, <strong>schön</strong> dass Sie Shift&lt;b&gt;6&lt;/b&gt; nutzen." in html
    assert "{{" not in html


def test_option_label_without_formatting_and_fallback(app, client, survey):
    build(app, client)
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "1"})
    html = p.get(f"/s/{uid}").data.decode()
    assert "Fairphone ist zuverlässig." in html and "Fairphone ist teuer" in html
    assert "Hallo , " in html  # no name: empty


def test_fallback_for_an_unanswered_question(app, client, survey):
    build(app, client)
    p, uid = start(app, client)
    p.post(f"/s/{uid}", data={"nav": "next"})
    html = p.get(f"/s/{uid}").data.decode()
    assert "Das Produkt ist teuer" in html and " ist zuverlässig." in html


def test_values_and_texts_per_type(app, client, survey):
    build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        v = piping.values(s, {2: "2", 3: "Ada", 8: ["1", "3", "2"]}, "de")
        assert v["marke"] == "Pixel" and v["name"] == "Ada" and isinstance(v["marke"], Typed)
        assert v["mehr"] == "A, B und C"
        assert piping.values(s, {8: ["1", "3"]}, "en")["mehr"] == "A and C"
        assert piping.values(s, {8: ["1", "2", "3"]}, "fr")["mehr"] == "A, B et C"
        assert piping.values(s, {2: "2"}, "de", skip={2})["marke"] == ""  # on the current page: not yet
        assert "zuv" not in v and "sd" not in v  # a matrix is no text
        date = db.session.get(Item, 3).kind  # text field: numbers with a decimal comma
        assert date.pipe_text(db.session.get(Item, 3), 2.5, "de") == "2,5"
        assert piping.earlier(s, db.session.get(Item, 5)) == ["marke", "name", ]
        assert piping.earlier(s, db.session.get(Item, 2)) == []


def test_date_texts(app, client, survey):
    add(client, "date_time")
    with app.app_context():
        item = db.session.get(Item, 2)
        t = item.kind.pipe_text
        assert t(item, "2000-05-23", "de") == "23.05.2000" and t(item, "2000-05-23", "en") == "2000-05-23"
        assert t(item, "2000-05-23T14:30", "de") == "23.05.2000, 14:30" and t(item, "14:30", "de") == "14:30"
        assert t(item, 2000, "de") == "2000" and t(item, "2000-05", "de") == "05.2000"


def test_fill_with_fallback_and_typed_values_after_formatting():
    assert fill("{{a|Ihr Produkt}} ist gut", {"a": ""}) == "Ihr Produkt ist gut"
    assert fill("{{a|Ihr Produkt}} ist gut", {"a": "X"}) == "X ist gut"
    assert fill("{{ a | dies }}", {}) == "dies" and fill("{{b}}", {}) == "{{b}}"  # unknown: the text, or as written
    assert "placeholder" in fill("{{a|dies}}", {}, mark_missing=True)  # the editor's preview marks it
    assert inline_rich("**{{a}}**", {"a": Typed("*x*")}) == "<strong>*x*</strong>"
    assert inline_rich("{{a}}", {"a": "**loop**"}) == "<strong>loop</strong>"  # a loop column may format
    assert rich("- {{a}}\n- b", {"a": Typed("<i>")}) == "<ul><li>&lt;i&gt;</li><li>b</li></ul>"
    assert inline_rich("\x01" + "0\x01{{a}}", {"a": Typed("x")}) == "0x"  # no smuggled markers


def test_design_check(app, client, survey):
    build(app, client)
    with app.app_context():
        _set(2, question="Sie sagten {{name}} und {{zuv}}, {{marke}}")  # same page, a matrix, itself
        _set(3, question="{{mehr}} {{hallo}}")  # a later question, an info text
        db.session.commit()
        msgs = [f.message for f in design.check(db.session.get(Survey, 1))]
    # the same page: goes in while the person answers (test_same_page_answers); only the missing fallback is named
    assert any("{{name}} steht auf derselben Seite und hat keinen Ersatztext" in m for m in msgs)
    assert any("{{marke}} steht in derselben Frage" in m for m in msgs)
    assert any("{{zuv}}: Antworten auf Likert" in m for m in msgs)
    assert any("{{mehr}} steht erst danach" in m for m in msgs)
    assert any("{{hallo}}: Text hat keine Antwort" in m for m in msgs)
    assert not any("{{marke}} ist zuverlässig" in m or "nicht ersetzt" in m for m in msgs)


def test_design_check_warns_without_a_text_for_an_empty_answer(app, client, survey):
    """{{marke}} without a fallback, and the question may stay empty (not required, or behind a condition): a
    warning; none where every use has its text, none for a required question that everybody gets."""
    build(app, client)
    client.post("/surveys/1/editor/blocks")
    add(client, "info", block=3)                                              # 9, in a block of its own
    with app.app_context():
        s = db.session.get(Survey, 1)
        gaps = [f.message for f in design.check(s) if "hat keinen Ersatztext" in f.message]
        assert any(m.startswith("zuv: {{marke}}") and "ist keine Pflichtfrage" in m for m in gaps)
        assert any(m.startswith("hallo: {{name}}") for m in gaps) and any(m.startswith("hallo: {{marke}}") for m in gaps)
        assert not any(m.startswith("sd:") for m in gaps)                    # {{marke|Das Produkt}}
        for item_id in (2, 3):
            db.session.get(Item, item_id).answer_mode = "required"
        db.session.commit()
        assert not any("hat keinen Ersatztext" in f.message for f in design.check(s))
        import copy

        from app import flow

        root = copy.deepcopy(flow.tree(s))  # block 2 (the sources) behind a condition
        node = next(n for n in root["children"] if n.get("block") == 2)
        root["children"][root["children"].index(node)] = {"t": "branch", "id": "b1", "cond": {"item": None, "op": "eq", "value": ""},
                                                          "children": [node]}
        s.flow = root
        _set(9, "spaeter", question="Noch einmal {{name}}")
        db.session.commit()
        late = [f.message for f in design.check(db.session.get(Survey, 1)) if "hat keinen Ersatztext" in f.message]
        assert any(m.startswith("spaeter: {{name}}") and "steht hinter einer Bedingung" in m for m in late)


def test_typed_answers_go_in_as_one_short_line(app, client, survey):
    from app.items.base import typed_text

    assert typed_text("  eins\n zwei\x00\x07 drei ") == "eins zwei drei"
    assert typed_text("x" * 300) == "x" * 199 + "…" and len(typed_text("wort " * 80)) <= 200
    build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        got = piping.values(s, {3: "a\nb " + "lang " * 60, 2: {"v": "9", "t": {"9": "Shift\t6 " + "z" * 300}}}, "de")
        assert got["name"].startswith("a b lang") and len(got["name"]) <= 200 and got["name"].endswith("…")
        assert got["marke"].startswith("Shift 6 zzz") and len(got["marke"]) == 200


def test_new_variable_name_takes_its_placeholders_along(app, client, survey):
    """A question is renamed: the texts that show its answer follow, with their fallback texts; one undo takes
    both back. A name that is also a URL parameter is left alone."""
    build(app, client)
    form = {"code": "brand", "question": "Welche Marke?", "option_label": ["**Fair**phone", "Pixel", "Andere"],
            "option_code": ["1", "2", "9"]}
    r = client.post("/surveys/1/editor/items/2", data=form, follow_redirects=True)
    assert "zeigen die Antwort weiter" in r.data.decode()
    with app.app_context():
        texts = {i: db.session.get(Item, i).texts["de"] for i in (5, 6, 7)}
        assert texts[5]["question"] == "Zu {{brand}}:" and texts[5]["labels"][0] == "gar nicht {{brand}}"
        assert list(texts[5]["statements"].values()) == ["{{brand}} ist zuverlässig."]
        assert list(texts[6]["pairs"].values())[0]["left"] == "{{brand|Das Produkt}} ist teuer"
        assert texts[7]["question"] == "Hallo {{name}}, **schön** dass Sie {{ brand }} nutzen."
        assert not [f.message for f in design.check(db.session.get(Survey, 1)) if "unbekannter Platzhalter" in f.message]
    client.post("/surveys/1/editor/undo")
    with app.app_context():
        assert db.session.get(Item, 2).code == "marke" and db.session.get(Item, 7).texts["de"]["question"].endswith("{{ marke }} nutzen.")
        from app import panel

        s = db.session.get(Survey, 1)
        s.panel = {**(s.panel or {}), **{**panel.config(s), "params": [{"name": "marke", "label": "", "required": False}]}}
        db.session.commit()
        assert piping.renamed(s, "marke", "brand") == []


def test_single_values_of_answers_with_several(app, client, survey):
    """A matrix, a semantic differential, several sliders, a constant sum, a ranking, a cloze text and the four
    prices: each value goes in by a name of its own, the column in the data set where it has one."""
    fill_consent(client)
    for kind in ("likert", "semantic", "slider", "constant_sum", "ranking", "cloze", "psm", "page_break", "info"):
        add(client, kind)
    with app.app_context():
        likert = _set(2, "zuv", question="Wie zuverlässig?", labels=["gar nicht", "", "", "", "sehr"])
        likert.config = {**likert.config, "statements": [{"id": "s1", "code": "", "reverse": False}, {"id": "s2", "code": "akku", "reverse": True}]}
        _set(2, statements={"s1": "Das Gerät", "s2": "Der Akku"})
        pair = _set(3, "sd").config["pairs"][0]["id"]
        _set(3, pairs={pair: {"left": "teuer", "right": "günstig"}})
        _set(4, "regler", config={"rows": [{"id": "r1", "code": ""}, {"id": "r2", "code": ""}], "format": "percent", "min": 0, "max": 100},
             rows={"r1": "Arbeit", "r2": "Freizeit"})
        _set(5, "summe", config={"options": [{"id": "a", "code": "miete"}, {"id": "b", "code": "essen"}], "total": 100, "format": "percent"},
             options={"a": "Miete", "b": "Essen"})
        _set(6, "rang", config={"options": [{"id": "a", "code": "1"}, {"id": "b", "code": "2"}]}, options={"a": "Preis", "b": "**Marke**"})
        _set(7, "luecke", text="Ich fahre mit [ ] nach [   ].")
        _set(8, "wtp", question="Was darf es kosten?")
        _set(10, "fazit", question="{{zuv_1}}, {{akku}}, {{sd_1}}, {{regler_2}}, {{summe_miete}}, {{rang_rank1}}, {{luecke_gap2|irgendwo}}, "
                                   "{{wtp_c}}, {{wtp_te|unbekannt}}")
        db.session.commit()
        s = db.session.get(Survey, 1)
        found = piping.sources(s)
        assert {"zuv_1", "akku", "sd_1", "regler_1", "regler_2", "summe_miete", "summe_essen", "rang_rank1", "rang_rank2",
                "luecke_gap1", "luecke_gap2", "wtp_tc", "wtp_c", "wtp_e", "wtp_te"} <= set(found)
        assert not {"zuv", "sd", "regler", "summe", "rang", "luecke", "wtp"} & set(found)   # not as a whole
        answers = {2: {"s1": 5, "s2": 3}, 3: {pair: 6}, 4: {"r1": 20, "r2": 80}, 5: {"miete": 30, "essen": 70}, 6: ["2", "1"],
                   7: ["dem Rad", "<b>Köln</b>"], 8: {"tc": 0.3, "c": 0.8, "e": 1.5, "te": None}}
        got = piping.values(s, answers, "de")
        assert (got["zuv_1"], got["akku"], got["sd_1"]) == ("sehr", "3", "6")      # a label, else the number as chosen
        assert (got["regler_1"], got["regler_2"], got["summe_miete"]) == ("20 %", "80 %", "30 %")
        assert (got["rang_rank1"], got["rang_rank2"]) == ("Marke", "Preis")
        assert (got["luecke_gap1"], got["luecke_gap2"]) == ("dem Rad", "<b>Köln</b>")
        assert (got["wtp_c"], got["wtp_e"], got["wtp_te"]) == ("0,80 €", "1,50 €", "")
        assert piping.values(s, answers, "en")["wtp_c"] == "€0.80"
        assert all(v == "" for v in piping.values(s, {}, "de").values())
        assert inline_rich(db.session.get(Item, 10).texts["de"]["question"], got) == \
            "sehr, 3, 6, 80 %, 30 %, Marke, &lt;b&gt;Köln&lt;/b&gt;, 0,80 €, unbekannt"
        # the editor offers them in the order of the questionnaire, each with what it is
        offered = dict(__import__("app.editor.elements", fromlist=["_pipe_sources"])._pipe_sources(s, db.session.get(Item, 10), "de"))
        assert list(offered)[:4] == ["zuv_1", "akku", "sd_1", "regler_1"]
        assert offered["akku"] == "Der Akku – Wie zuverlässig?" and offered["wtp_c"] == "Preis „günstig“ – Was darf es kosten?"
        assert offered["rang_rank1"].startswith("Platz 1") and offered["sd_1"].startswith("teuer – günstig")
    page = client.get("/surveys/1/editor/items/10").data.decode()                 # rarely needed: folded at first
    assert '<details class="pipe-fold">' in page and "↪ Antworten früherer Fragen" in page
    with app.app_context():
        # the pre-launch check: these names are known; the matrix as a whole is not a text
        _set(10, question="{{zuv}} {{akku|–}} {{nichts}}")
        db.session.commit()
        msgs = [f.message for f in design.check(db.session.get(Survey, 1))]
        assert any("{{zuv}}: Antworten auf Likert" in m and "nur ihre einzelnen Werte" in m for m in msgs)
        assert any("{{nichts}} wird nicht ersetzt" in m for m in msgs) and not any("{{akku}}" in m for m in msgs)


def test_new_variable_name_takes_the_single_values_along(app, client, survey):
    fill_consent(client)
    for kind in ("psm", "page_break", "info"):
        add(client, kind)
    with app.app_context():
        _set(2, "wtp", question="Was darf es kosten?", product="Ein Apfel")
        _set(4, "fazit", question="Günstig: {{wtp_c}}, teuer: {{wtp_e|offen}}")
        db.session.commit()
        item = db.session.get(Item, 2)
        before = [n for n, _ in item.kind.pipe_parts(item, "de")]
        item.code = "preis"
        assert piping.renamed(db.session.get(Survey, 1), before, [n for n, _ in item.kind.pipe_parts(item, "de")]) == ["fazit"]
        assert db.session.get(Item, 4).texts["de"]["question"] == "Günstig: {{preis_c}}, teuer: {{preis_e|offen}}"


def test_design_check_name_also_url_parameter(app, client, survey):
    build(app, client)
    with app.app_context():
        from app import panel

        s = db.session.get(Survey, 1)
        cfg = panel.config(s)
        cfg["params"] = [{"name": "marke", "label": "", "required": False}]
        s.panel = {**(s.panel or {}), **cfg}
        db.session.commit()
        msgs = [f.message for f in design.check(s)]
    assert any("zugleich URL-Parameter" in m for m in msgs)


def test_editor_offers_earlier_answers(app, client, survey):
    build(app, client)
    html = client.get("/surveys/1/editor/items/5").data.decode()
    assert "Antworten früherer Fragen" in html and 'data-insert="{{marke}}"' in html and 'title="Welche Marke?"' in html
    assert 'data-insert="{{zuv}}"' not in html
    assert ">Welche Marke? <small>marke</small></button>" in html              # the chip reads as the question
    # the formatting field gets the same list: words on the placeholders, the list behind "Antwort einfügen"
    import json
    import re

    def offered(page):
        return json.loads(re.search(r'<script type="application/json" data-rich-vars>(.*?)</script>', page, re.S).group(1))

    groups = offered(html)
    assert [g["kind"] for g in groups] == ["answer", "param", "param"]
    assert groups[2]["title"] == "Befragung und Ablauf" and [i["name"] for i in groups[2]["items"]] == ["lang", "survey"]
    assert groups[0]["items"] == [{"name": "marke", "text": "Welche Marke?"}, {"name": "name", "text": "Ihr Name?"},
                                  {"name": "mehr", "text": "Was noch? (gleiche Seite)"}]
    assert groups[1]["items"] == [{"name": "pid", "text": "Teilnahme-ID"}]
    first = client.get("/surveys/1/editor/items/2").data.decode()
    # the first question has nothing before it, only the text field of its own page
    assert offered(first)[0]["items"] == [{"name": "name", "text": "Ihr Name? (gleiche Seite)"}]


def test_word_export_keeps_the_placeholder_in_the_codebook_and_prints_the_fallback(app, client, survey):
    import io

    from docx import Document

    build(app, client)

    def text(version):
        doc = Document(io.BytesIO(client.get(f"/surveys/1/export/questionnaire-{version}.docx").data))
        return "\n".join([p.text for p in doc.paragraphs] +
                         [c.text for t in doc.tables for r in t.rows for c in r.cells])

    assert "{{marke|Das Produkt}} ist teuer" in text("codebook")
    paper = text("print")
    assert "Das Produkt ist teuer" in paper and "________ ist zuverlässig." in paper and "{{" not in paper


def test_same_page_answers(app, client, survey):
    """An answer of the same page stands in the question text of that page: a span the page's script fills."""
    fill_consent(client)
    for kind in ("single_choice", "text_input", "info", "likert", "slider", "page_break", "info"):
        add(client, kind)                                              # 2 .. 8
    with app.app_context():
        _set(2, "marke", config={"options": [{"id": "a", "code": "1"}, {"id": "b", "code": "2"}]},
             question="Welche Marke?", options={"a": "Fairphone", "b": "Pixel"})
        _set(3, "name", question="Ihr Name?")
        _set(4, "hallo", question="Hallo {{name|du}}, **{{marke|deine Marke}}** ist gewählt, Regler: {{regler|–}}.")
        likert = _set(5, "zuv", question="Zu {{marke|der Marke}}:")
        _set(5, statements={likert.config["statements"][0]["id"]: "{{marke|Sie}} ist gut."})
        _set(6, "regler", config={"format": "percent"}, question="Wie viel?")
        _set(8, "danke", question="Danke, {{name}}.")
        db.session.commit()
        s = db.session.get(Survey, 1)
        msgs = [f.message for f in design.check(s)]
        # a statement is a plain text: there the same page does not work
        assert any("zuv: {{marke}} steht auf derselben Seite. Dort lassen sich nur" in m for m in msgs)
        assert not any(m.startswith("hallo:") for m in msgs)
        assert piping.same_page(s, db.session.get(Item, 4)) == ["marke", "name", "regler"]
        _set(5, statements={likert.config["statements"][0]["id"]: "Die Marke ist gut."})
        db.session.commit()
    p, uid = start(app, client)
    html = p.get(f"/s/{uid}").data.decode()
    assert '<span data-pipe="name" data-pipe-item="3" data-pipe-kind="text" data-fallback="du">du</span>' in html
    assert '<strong><span data-pipe="marke" data-pipe-item="2" data-pipe-kind="choice" data-fallback="deine Marke">deine Marke</span></strong>' in html
    assert 'data-pipe="regler"' in html and 'data-pipe-suf=" %"' in html and 'data-pipe-kind="value"' in html
    assert "Zu <span data-pipe=" in html and "{{" not in html
    # sent with an error (the name is too long for nothing; the Likert is left out and required is off): the page
    # comes again and the server has put in what it knows
    with app.app_context():
        db.session.get(Item, 5).answer_mode = "required"
        db.session.commit()
    again = p.post(f"/s/{uid}", data={"nav": "next", "i2": "2", "i3": "<Ada>"}).data.decode()
    assert 'data-fallback="du">&lt;Ada&gt;</span>' in again and 'data-fallback="deine Marke">Pixel</span>' in again
    sid = None
    with app.app_context():
        sid = db.session.get(Item, 5).config["statements"][0]["id"]
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "2", "i3": "Ada", f"i5_{sid}": "3"})
    last = p.get(f"/s/{uid}").data.decode()
    assert "Danke, Ada." in last and "data-pipe" not in last          # a later page: put in by the server as before


def test_same_page_span_only_in_formatted_texts():
    from app.items.base import Live

    live = {"x": Live("", {"kind": "text", "item": 7})}
    assert fill("a {{x|leer}} b", live) == "a leer b"                  # a plain text may stand in an attribute
    assert inline_rich('**{{x|"le<er"}}**', live) == (
        '<strong><span data-pipe="x" data-pipe-item="7" data-pipe-kind="text" data-fallback="&#34;le&lt;er&#34;">'
        "&#34;le&lt;er&#34;</span></strong>")
    assert "data-pipe" in rich("Absatz {{x}}", live)


def test_builtin_placeholders_language_survey_and_randomizer(app, client, survey):
    """{{lang}}, {{survey}} and the variable name of a randomizer ({{cond1}}: what was drawn) in texts."""
    from app import flow, panel, runtime
    from app.models import Block, Response

    fill_consent(client)
    add(client, "info")                                                # 2
    client.post("/surveys/1/editor/flow/add", data={"kind": "rand"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        for title in ("Kontrolle", "**Treatment**"):
            s.blocks.append(Block(position=len(s.blocks), titles={"de": title}))
        db.session.flush()
        tree = flow.normalize(s)
        rand = next(n for n in tree["children"] if n["t"] == "rand")
        arms = [n for n in tree["children"] if n["t"] == "block" and n["block"] in (s.blocks[-1].id, s.blocks[-2].id)]
        tree["children"] = [n for n in tree["children"] if n is not rand and n not in arms]
        rand["children"] = arms
        tree["children"].insert(1, rand)                               # before the block with the text
        s.flow = tree
        _set(2, "hallo", question="Sprache {{lang}}, Befragung {{survey}}, Gruppe {{cond1}}.")
        db.session.commit()
        assert flow.branch_names(s) == {"cond1": "Randomisierung 1"}
        assert not [f.message for f in design.check(s) if "wird nicht ersetzt" in f.message]
        code = s.code
    p, uid = start(app, client)
    html = p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        response = db.session.scalars(db.select(Response).filter_by(is_preview=False)).one()
        drawn = flow.branch_values(s, response)["cond1"]
        assert drawn in ("Kontrolle", "**Treatment**")
        assert runtime.builtin_variables(response, s)["survey"] == code and runtime.builtin_variables(response)["lang"] == "de"
    # the name of the drawn block goes in as typed: its "**" is no formatting
    assert f"Sprache de, Befragung {code}, Gruppe {drawn}." in html
    # a URL parameter of the same name wins over the built-in value, as before with questions
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {**(s.panel or {}), **{**panel.config(s), "params": [{"name": "lang", "label": "", "required": False}]}}
        db.session.commit()
    q = app.test_client()
    q.get(f"/s/{uid}?lang=xx")
    q.post(f"/s/{uid}", data=CONSENT_OK)
    assert "Sprache xx, Befragung" in q.get(f"/s/{uid}").data.decode()

