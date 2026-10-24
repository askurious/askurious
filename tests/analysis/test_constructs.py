from tests.instrument_permissions import grant_instrument
import re

from app import constructs, ratswd
from app.extensions import db
from app.models import Item, Response, Survey, utcnow
from tests.conftest import PG_URL


def keys(rows):
    return [c.key for c in rows]


def test_search_tags_and_explore(app, client):
    with app.app_context():
        assert "kut" in keys(constructs.search("Beier"))              # also finds "Beierlein" (prefix match)
        found = keys(constructs.search("kontroll"))  # prefix match
        # ranked by bm25 in SQLite's FTS5; PostgreSQL puts matches in the name first, then by name
        assert sorted(found) == ["ie4", "kuk", "kut"]  # names and descriptions; tb only through its real wording
        assert keys(constructs.search("impuls"))[0] == "i8"  # GDMS: "impulsive decisions"
        assert "bfi10" in keys(constructs.search("testaussage"))       # item wording is indexed (stand-in wording)
        assert keys(constructs.search("rammstedt 2007")) == ["bfi10"]  # all words must match
        demographic = keys(constructs.search(tags=["art:demografie"]))  # age, gender and the RatSWD questions
        assert [k for k in demographic if not k.startswith("ratswd_")] == ["age", "gender"]
        assert sorted(k for k in demographic if k.startswith("ratswd_")) == sorted(ratswd.KEYS)
        assert keys(constructs.search(tags=["fach:psychologie", "art:einstellung"])) == ["kusiv3", "kut", "tb"]
        assert keys(constructs.search(tags=["art:einstellung"])) == ["aub", "kusiv3", "kut", "tb"]
        assert "gender" not in keys(constructs.search(tags=["art:einstellung", "art:persönlichkeit"]))
        assert len(constructs.search(tags=["quelle:gesis-zis"])) == 9
        assert constructs.search("xyzzy") == []
        assert set(keys(constructs.search(tags=["status:validiert"]))) == {"bfi10", "kut", "kuk", "kamb", "fitai", "asku", "ie4", "kusiv3", "i8", "nfck",
                                                                     "tam_pu", "tam_peou", "tam_bi", "tb", "r1", "aub", "gdms"}
        assert set(keys(constructs.search("validiert technik"))) == {"kut", "tam_pu", "tam_peou", "tam_bi", "tb"}
        # (aub too with its real wording, where two items name "Technik")
    page = client.get("/constructs/?q=technik").data.decode()
    assert "Kontrollüberzeugung" in page and "Big Five" not in page and "Fachgebiet" in page
    found = [r["key"] for r in client.get("/constructs/api/search?q=gesch").json]
    assert set(found[:2]) == {"gender", "ratswd_ges1"}  # Spiel et al. and the RatSWD question
    detail = client.get("/constructs/bfi10?lang=en").data.decode()
    assert "Test statement 1 of BFI10" in detail and "doi.org/10.1016/j.jrp.2006.02.001" in detail


def test_facet_counts_leave_out_their_own_facet(app, client):
    """A chip counts the hits it would give: the choice of its own facet is left out, the others stay (OR within a
    facet, AND across); an empty result does not set every chip to 0."""
    with app.app_context():
        chosen = ["status:validiert", "quelle:ratswd-konsortswd"]
        assert constructs.search(tags=chosen) == []
        counts = {t.slug: n for pairs in constructs.facet_counts([], "", chosen).values() for t, n in pairs}
        standard = len(constructs.search(tags=["status:standardfrage", "quelle:ratswd-konsortswd"]))
        assert standard and counts["status:standardfrage"] == standard  # Typ: under the chosen source
        assert counts["quelle:gesis-zis"] == len(constructs.search(tags=["status:validiert", "quelle:gesis-zis"])) == 9
        assert "art:demografie" not in counts  # another facet: the (empty) hits themselves
    page = client.get("/constructs/?tag=status:validiert&tag=quelle:ratswd-konsortswd").data.decode()
    assert re.search(r"GESIS ZIS <span class=\"facet-count\">9</span>", page)


def test_insert_from_explore_page(app, client, survey):
    grant_instrument(app, "kut")
    page = client.get("/constructs/kut?survey=1").data.decode()
    assert "In „Test“ einfügen" in page and "/surveys/1/editor/blocks/2/items" in page
    grant_instrument(client.application, "kut")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kut"})
    with app.app_context():
        item = db.session.get(Item, 2)
        meta = item.config["construct"]
        assert (meta["key"], meta["version"], meta["unlocked"]) == ("kut", constructs.get("kut").seed_version, False) and meta["template"]["code"] == "KUT"
    assert client.get("/constructs/kut").data.decode().count("1 Befragung") == 1


def test_terms_gate(app, client):
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    r = other.get("/dashboard/")
    assert r.status_code == 302 and "/terms/" in r.headers["Location"]
    page = other.get(r.headers["Location"]).data.decode()
    assert "Metaanalysen" in page and "Zustimmen und fortfahren" in page
    other.post("/terms/", data={})  # checkbox missing: still blocked
    assert other.get("/dashboard/").status_code == 302
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/dashboard/").status_code == 200
    assert app.test_client().get("/terms/").status_code == 200  # public to read


def test_consent_mentions_pooling_and_pool_export(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gender"})
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:sorgfalt"})
    with app.app_context():
        from app import design

        s = db.session.get(Survey, 1)
        consent = s.consent_item
        body = consent.kind.template_body(consent, "de")
        # the paragraph on the pool is fixed under the text, not part of it: an own text cannot lose it
        assert "Metaanalysen" not in body and "nicht an Dritte" not in body
        consent.texts = {**consent.texts, "de": {**consent.texts["de"], "body": "Kurz."}}
        db.session.commit()
        notice = consent.kind.pool_notice(consent, "de")
        assert "Metaanalysen" in notice and "Big Five" in notice and "Betreiber der Plattform" in notice and "Brauner" not in notice
        assert "pseudonymisiert" in notice and "Art. 6 Abs. 1 lit. a DSGVO" in notice
        assert "deine Antworten" in consent.kind.pool_notice(consent, "de-du") and "Ihre" not in consent.kind.pool_notice(consent, "de-du")
        assert "meta-analyses" in consent.kind.pool_notice(consent, "en")
        assert not any("studienübergreifend" in f.message for f in design.check(s))
        public_key = s.public_key
        bfi, gender, care = db.session.get(Item, 2), db.session.get(Item, 3), db.session.get(Item, 4)
        s1, s2 = bfi.config["statements"][0]["id"], bfi.config["statements"][1]["id"]
        female = gender.config["options"][0]["code"]
        for source, careful in (("real", "1"), ("dummy", "1"), ("real", "0")):
            db.session.add(Response(survey_id=1, source=source, status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": {s1: 2, s2: 5}, "3": female, "4": careful}))
        db.session.commit()
    page = client.get(f"/s/{public_key}/privacy").data.decode()
    assert "Metaanalysen" in page
    assert client.get("/constructs/bfi10/pool.csv").data.decode("utf-8-sig").count("\n") == 1  # still running: header only
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.CLOSED
        db.session.commit()
    lines = client.get("/constructs/bfi10/pool.csv").data.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("study_id,participant_id,item,language,month,construct_version,adapted,modifications,"
                               "points,BFI_1,BFI_2")
    assert lines[0].endswith(",BFI_10,added_items,age,gender")
    assert len(lines) == 2  # dummy data and the self-report "do not use my answers" excluded
    row = lines[1].split(",")
    assert row[0].startswith("S") and row[1].startswith("P") and row[2] == "BFI"
    assert row[6:9] == ["0", "", "5"] and row[9:11] == ["4", "5"] and row[-1] == "1"  # BFI_1 reverse coded
    gender_rows = client.get("/constructs/gender/pool.csv").data.decode("utf-8-sig").splitlines()
    assert gender_rows[1].split(",")[:2] == row[:2]  # same study and participant ids: files can be joined
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/constructs/bfi10/pool.csv").status_code == 403


def test_former_pool_paragraph_stays_in_started_surveys_and_leaves_drafts(app, client, survey):
    from app.items import consent as consent_kind

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})
    old = ("**Standardisierte Fragebögen**\nEinige Fragen stammen aus wissenschaftlich geprüften Fragebögen (Big Five). "
           "Ihre anonymen Antworten auf diese Fragen werden zusätzlich vom Betreiber … (Metaanalysen).")
    with app.app_context():
        s = db.session.get(Survey, 1)
        consent = s.consent_item
        consent.texts = {**consent.texts, "de": {**consent.texts["de"], "body": "**Worum geht es?**\nTest.\n\n" + old
                                                  + "\n\n**Kontakt**\nWir."},
                          "en": {**consent.texts.get("en", {}), "body": "Short."}}
        s.status = Survey.RUNNING
        db.session.commit()
        assert consent.kind.pool_notice(consent, "de") == ""  # participants agreed to the paragraph in the text
        assert consent.kind.pool_notice(consent, "en")  # the English text has none: the fixed one is shown
        s.status = Survey.DRAFT
        db.session.commit()
        assert consent_kind.upgrade_drafts() == 1
        body = db.session.get(Survey, 1).consent_item.texts["de"]["body"]
        # the paragraph on data processing and rights comes in before "Kontakt" (Consent.upgrade_body, 2026-10-20)
        assert body.startswith("**Worum geht es?**\nTest.\n\n**Datenverarbeitung und Ihre Rechte**\n")
        assert body.endswith("\n\n**Kontakt**\nWir.") and "Standardisierte Fragebögen" not in body
        assert consent_kind.strip_legacy_pool("**Standardised questionnaires:** Some … (meta-analyses).\n\nB") == "B"
        assert consent_kind.upgrade_drafts() == 0


def test_constructs_file_in_export(app, client, survey):
    assert "Validierte Konstrukte und Quellen" not in client.get("/surveys/1/data").data.decode()
    grant_instrument(client.application, "kut")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kut"})
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gender"})
    client.post("/surveys/1/editor/items/2/unlock")
    assert "Validierte Konstrukte und Quellen" in client.get("/surveys/1/data").data.decode()
    md = client.get("/surveys/1/export/constructs.md").data.decode()
    with app.app_context():
        v = constructs.get("kut").seed_version
    assert f"(toolbox version {v}, unchanged)" in md  # unlocking alone changes nothing
    with app.app_context():
        from werkzeug.datastructures import MultiDict

        kut = db.session.get(Item, 2)
        sts, texts = kut.config["statements"], kut.texts["de"]["statements"]
    form = MultiDict([("code", "KUT"), ("answer_mode", "nudge"), ("points", "5")])
    for st in sts[:-1]:  # drop KUT_8, add a new statement
        form.add("stmt_id", st["id"]); form.add("stmt_code", st["code"]); form.add("stmt_text", texts[st["id"]])
        if st["reverse"]:
            form.add("stmt_reverse", st["id"])
    form.add("stmt_id", ""); form.add("stmt_code", "KUT_new"); form.add("stmt_text", "Technik macht mir Spaß.")
    for n, label in enumerate(["stimmt gar nicht", "stimmt wenig", "stimmt mittelmäßig", "stimmt ziemlich", "stimmt sehr"]):
        form.add("label", label)
    client.post("/surveys/1/editor/items/2", data=form)
    md = client.get("/surveys/1/export/constructs.md").data.decode()
    assert "## Kontrollüberzeugung im Umgang mit Technik (KUT)" in md and f"(toolbox version {v}, MODIFIED)" in md
    assert "Modifications: removed KUT_8; added KUT_new" in md
    assert "`KUT_2`: Testaussage 2 von KUT (reverse coded)" in md
    assert "`99` = keine Angabe" in md
    refs = md.split("## References")[1]
    assert refs.index("Beier") < refs.index("Spiel") and "https://doi.org/10.1145/3338283" in refs
    with app.app_context():
        from app import zenodo

        names = [n for n, _ in zenodo.package(db.session.get(Survey, 1))]
        assert "constructs.md" in names


def test_construct_random_order_default_and_switchable(app, client, survey):
    from werkzeug.datastructures import MultiDict

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})
    with app.app_context():
        assert db.session.get(Item, 2).config["randomize"] is True
    with app.app_context():
        question = db.session.get(Item, 2).texts["de"]["question"]
    client.post("/surveys/1/editor/items/2", data=MultiDict([("code", "BFI"), ("answer_mode", "nudge"),
                                                             ("question", question)]))  # randomize unticked
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["randomize"] is False and constructs.locked(item)
        assert constructs.modifications(item, constructs.get("bfi10")) == []  # order is not a modification


def test_gesis_scales_score_as_documented(app):
    with app.app_context():
        def reverse(key):
            return [s["reverse"] for s in constructs.get(key).spec["config"]["statements"]]

        assert reverse("bfi10") == [True, False, True, True, True, False, True, False, False, False]  # items 1, 3, 4, 5, 7
        assert reverse("kusiv3") == [False, True, False]
        assert reverse("nfck") == [True, False, False, True]
        assert not any(reverse("asku") + reverse("ie4") + reverse("i8"))
        assert constructs.get("nfck").spec["config"]["points"] == 7 and constructs.get("nfck").languages == ["de"]
        for key in ("asku", "ie4", "kusiv3", "i8", "nfck", "bfi10", "tb", "r1", "aub"):
            c = constructs.get(key)
            assert c.status == "verified" and "nichtkommerzielle" in c.notes and "zis" in (c.doi + c.citation)


def test_technikbereitschaft_as_documented(app, client, survey):
    with app.app_context():
        c = constructs.get("tb")
        sts = c.spec["config"]["statements"]
        assert c.languages == ["de"] and c.spec["config"]["points"] == 5 and c.doi == "10.6102/zis244"
        assert [s["code"] for s in sts] == [f"TB_{n}" for n in range(1, 13)]
        assert [s["reverse"] for s in sts] == [False] * 4 + [True] * 4 + [False] * 4  # items 5 to 8
        assert [s["subscale"] for s in sts] == (["Technikakzeptanz"] * 4 + ["Technikkompetenzüberzeugungen"] * 4
                                                + ["Technikkontrollüberzeugungen"] * 4)
        assert c.spec["texts"]["de"]["labels"] == ["stimmt gar nicht", "stimmt wenig", "stimmt teilweise", "stimmt ziemlich",
                                                   "stimmt völlig"]
        assert keys(constructs.search("technikbereitschaft")) == ["tb"] and keys(constructs.search("neyer")) == ["tb"]
    detail = client.get("/constructs/tb").data.decode()
    assert "Subskala" in detail and "Technikkontrollüberzeugungen" in detail and "doi.org/10.6102/zis244" in detail
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:tb"})
    with app.app_context():
        from app import methods

        item = db.session.get(Item, 2)
        ids = [s["id"] for s in item.config["statements"]]
        # acceptance varies, competence is answered "stimmt gar nicht" (recoded to 5), control "stimmt ziemlich"
        for a in (1, 2, 4, 5):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": dict(zip(ids, [a] * 4 + [1] * 4 + [4] * 4))}))
        db.session.commit()
        fact = methods.facts(db.session.get(Survey, 1))["constructs"][0]  # reliability and scores per facet
        scales = {s["name"]: (s["k"], s["mean"]) for s in fact["subscales"]}
        assert scales == {"Technikakzeptanz": (4, 3.0), "Technikkompetenzüberzeugungen": (4, 5.0),
                          "Technikkontrollüberzeugungen": (4, 4.0)}


def test_risikobereitschaft_as_documented(app, client, survey):
    with app.app_context():
        c = constructs.get("r1")
        cfg = c.spec["config"]
        assert c.languages == ["de", "en"] and cfg["points"] == 7 and cfg["labeling"] == "endpoints" and c.doi == "10.6102/zis236"
        assert [(s["code"], s["reverse"]) for s in cfg["statements"]] == [("R1_1", False)]  # one item, the question itself
        de, en = c.spec["texts"]["de"], c.spec["texts"]["en"]
        assert de["question"] == "Teststandardfrage zu R1"
        assert en["question"] == "Test instruction for R1"
        assert (de["labels"][0], de["labels"][-1]) == ("gar nicht risikobereit", "sehr risikobereit")
        assert (en["labels"][0], en["labels"][-1]) == ("not at all willing to take risks", "very willing to take risks")
        assert "zis286" in c.citation and "en" not in constructs.WORKING_LANGS.get("r1", ())
        assert set(keys(constructs.search("risikobereit"))) == {"i8", "r1"} and keys(constructs.search("kemper 2015")) == ["r1"]
    detail = client.get("/constructs/r1").data.decode()
    assert "1 Item, 7 Stufen" in detail and "doi.org/10.6102/zis236" in detail and "7 = sehr risikobereit" in detail
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:r1"})
    with app.app_context():
        from app import methods

        item = db.session.get(Item, 2)
        assert item.type == "likert" and not item.kind.validate(item)  # a single row needs no text of its own
        sid = item.config["statements"][0]["id"]
        for a in (2, 4, 6, 4):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": {sid: a}}))
        db.session.commit()
        fact = methods.facts(db.session.get(Survey, 1))["constructs"][0]  # the score is the answer; no alpha with one item
        assert (fact["items"], fact["points"], fact["alpha"], fact["mean"], fact["subscales"]) == (1, 7, None, 4.0, [])


def test_umweltbewusstsein_as_documented(app, client, survey):
    with app.app_context():
        c = constructs.get("aub")
        sts = c.spec["config"]["statements"]
        assert c.languages == ["de"] and c.spec["config"]["points"] == 5 and c.doi == "10.6102/zis208"
        assert [s["code"] for s in sts] == [f"AUB_{n}" for n in range(1, 12)] and not any(s["reverse"] for s in sts)
        instr, intr, exist = "Instrumentelles Naturverständnis", "Intrinsisches Naturverständnis", "Natur als Existenzgrundlage"
        # ZIS table 3; items 3 and 5 load on two factors and stay outside the subscales
        assert [s.get("subscale") for s in sts] == [instr, instr, None, instr, None, instr, intr, intr, exist, exist, exist]
        assert c.spec["texts"]["de"]["labels"] == ["stimme überhaupt nicht zu", "stimme eher nicht zu", "teils/teils",
                                                   "stimme weitgehend zu", "stimme voll und ganz zu"]
        assert c.spec["texts"]["de"]["statements"]["s11"] == "Testaussage 11 von AUB"
        assert keys(constructs.search("umweltbewusstsein")) == ["aub"] and keys(constructs.search("wingerter")) == ["aub"]
    detail = client.get("/constructs/aub").data.decode()
    assert "Subskala" in detail and "Natur als Existenzgrundlage" in detail and "doi.org/10.6102/zis208" in detail
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:aub"})
    with app.app_context():
        from app import methods

        item = db.session.get(Item, 2)
        ids = [s["id"] for s in item.config["statements"]]
        # instrumental items answered 2, intrinsic 4, existence 5; items 3 and 5 vary and count for no subscale
        for a in (1, 2, 4, 5):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": dict(zip(ids, [2, 2, a, 2, a, 2, 4, 4, 5, 5, 5]))}))
        db.session.commit()
        fact = methods.facts(db.session.get(Survey, 1))["constructs"][0]
        scales = {s["name"]: (s["k"], s["mean"]) for s in fact["subscales"]}
        assert scales == {instr: (4, 2.0), intr: (2, 4.0), exist: (3, 5.0)}


def test_tam_placeholder_is_not_a_modification(app, client, survey):
    from werkzeug.datastructures import MultiDict

    grant_instrument(client.application, "tam_pu")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:tam_pu"})
    with app.app_context():
        from app import design

        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        assert item.config["points"] == 7 and len(item.config["statements"]) == 6
        assert any("[System]" in f.message for f in design.check(s))
        texts, sts = item.texts["de"], item.config["statements"]
    form = MultiDict([("code", "PU"), ("answer_mode", "nudge"), ("randomize", "1"),
                      ("question", texts["question"].replace("[System]", "dem Pflegeroboter"))])
    for st in sts:
        form.add("stmt_id", st["id"])
        form.add("stmt_text", texts["statements"][st["id"]].replace("[System]", "den Pflegeroboter"))
    for label in texts["labels"]:
        form.add("label", label)
    client.post("/surveys/1/editor/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert "Pflegeroboter" in item.texts["de"]["statements"][sts[0]["id"]]
        assert constructs.modifications(item, constructs.get("tam_pu")) == []  # placeholder filled: unchanged
        item.texts = {**item.texts, "de": {**item.texts["de"], "question": "Etwas ganz anderes."}}
        assert constructs.modifications(item, constructs.get("tam_pu")) == ["wording (de): instruction"]
    with app.app_context():
        assert {"tam_pu", "tam_peou", "tam_bi"} <= {c.key for c in constructs.search("technikakzeptanz")}
        assert [c.key for c in constructs.search("Benutzerfreundlichkeit")][0] == "tam_peou"


def test_construct_badge_in_questionnaire(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:asku"})
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gender"})
    page = client.get("/surveys/1/editor/").data.decode()
    assert page.count("badge-construct") == 2 and "validiert" in page and "Standardfrage" in page
    with app.app_context():
        item = db.session.get(Item, 2)
        item.texts = {**item.texts, "de": {**item.texts["de"], "question": "Anders."}}
        db.session.commit()
    assert "verändert" in client.get("/surveys/1/editor/").data.decode()


def test_age_standard_question(app, client, survey):
    import pytest
    from werkzeug.datastructures import MultiDict

    from app.items.base import AnswerError

    with app.app_context():
        assert "age" in [c.key for c in constructs.search("Alter")]
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:age"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.type == "text_input" and item.code == "age" and item.config["construct"]["key"] == "age"
        assert (item.config["validation"], item.config["min"], item.config["max"]) == ("integer", 10, 120)
        assert item.text("question", "de") == "Wie alt sind Sie?" and item.text("suffix", "de") == "Jahre"
        assert item.text("suffix", "en") == "years"
        assert item.kind.parse_answer(item, MultiDict([("i2", "34")])) in (34, "34")
        for bad in ("5", "130", "zwanzig"):
            with pytest.raises(AnswerError):
                item.kind.parse_answer(item, MultiDict([("i2", bad)]))
    html = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    assert "Wie alt sind Sie?" in html and '<span class="p-affix" id="suf2">Jahre</span>' in html


def test_favourites_decide_the_list_in_the_editor(app, client, survey):
    """Stars come first; with fewer than five (or none) the list is filled up to six with the constructs most
    used in the own surveys and those of the own groups, then by name. From five stars on: the stars only."""
    def listed():
        with app.app_context():
            from app.models import User

            return keys(constructs.sidebar(db.session.scalar(db.select(User))))

    by_name = listed()
    with app.app_context():
        # the RatSWD standard questions fill the list only once used (one construct per item, app/ratswd.py)
        assert by_name == [k for k in keys(constructs.search()) if k not in ratswd.NOT_POOLED][:6]
        assert "tb" not in by_name and "tam_bi" not in by_name
    page = client.get("/surveys/1/editor/").data.decode()
    assert page.count('aria-pressed="false" title="Als Favorit merken') == 6

    r = client.post("/constructs/tb/favourite", data={"json": "1"}).json  # the palette: the new list comes back
    assert r["starred"] is True and [c["key"] for c in r["sidebar"]] == ["tb"] + by_name[:5]
    assert r["sidebar"][0]["starred"] and not r["sidebar"][1]["starred"]
    for key in ("kut", "tam_bi", "tam_bi", "tb"):             # used in the own survey: the most used first
        grant_instrument(app, key)
        client.post("/surveys/1/editor/blocks/2/items", data={"type": f"construct:{key}"})
    assert listed() == ["tb", "tam_bi", "kut"] + [k for k in by_name if k != "kut"][:3]
    page = client.get("/surveys/1/editor/").data.decode()
    assert page.count('aria-pressed="true" title="Favorit:') == 1
    assert client.get("/constructs/api/search?q=technikbereitschaft").json[0]["starred"] is True

    for key in ("bfi10", "gender", "age", "nfck"):  # five stars: exactly these, by name
        r = client.post(f"/constructs/{key}/favourite", data={"list": "1", "q": "x", "survey": "1"})
        assert r.status_code == 302 and r.headers["Location"].endswith(f"/constructs/?q=x&survey=1#k-{key}")
    assert listed() == ["age", "bfi10", "gender", "tb", "nfck"]
    client.post("/constructs/kut/favourite")                    # a sixth one: no limit
    assert len(listed()) == 6 and "kut" in listed()
    cards = client.get("/constructs/").data.decode()
    assert cards.count('aria-pressed="true">★') == 6 and 'id="k-kut"' in cards
    assert "Favorit entfernen" in client.get("/constructs/kut").data.decode()
    r = client.post("/constructs/kut/favourite", data={"lang": "en"})  # the same click takes the star away
    assert r.headers["Location"].endswith("/constructs/kut?lang=en") and "kut" not in listed()
    assert client.post("/constructs/nope/favourite").status_code == 404

    other = app.test_client()                                 # favourites are personal
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    assert all(not c["starred"] for c in other.get("/constructs/api/search?limit=20").json)

    def of_other():
        with app.app_context():
            from app.models import User

            return keys(constructs.sidebar(db.session.scalar(db.select(User).filter_by(orcid="0000-0001-5555-5555"))))

    assert of_other() == by_name                              # a survey of somebody else does not count ...
    client.post("/groups/new", data={"name": "Team"})
    client.post("/groups/1/members", data={"orcid": "0000-0001-5555-5555", "name": "Kollegin"})
    client.post("/surveys/1/access-groups", data={"group": "1", "role": "editor"})
    assert of_other()[:3] == ["tam_bi", "kut", "tb"]          # ... until it is shared with a group of theirs
    client.post("/surveys/1/editor/items/3/delete")           # in the trash: not counted (item 3 = tam_bi)
    assert of_other()[:3] == ["kut", "tb", "tam_bi"]


def test_media_evaluation_scale_is_a_six_point_differential_with_two_subscales(app, client, survey):
    from app import constructs

    spec = constructs._kamb_spec()
    assert spec["type"] == "semantic" and spec["config"]["points"] == 6 and spec["config"]["randomize"]
    pairs = spec["config"]["pairs"]
    assert [p["reverse"] for p in pairs] == [True, False, False, True, True, False, True, False]
    assert {p["subscale"] for p in pairs} == {"affektiv", "kognitiv"}
    assert spec["texts"]["de"]["pairs"]["p7"] == {"left": "neutral", "right": "parteiisch"}
    assert spec["texts"]["en"]["pairs"]["p1"] == {"left": "professional", "right": "amateur"}


def test_media_evaluation_scale_in_a_survey(app, client, survey):
    from tests.conftest import CONSENT_OK, fill_consent

    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kamb"})
    client.post("/surveys/1/start", data={})
    with app.app_context():
        s = db.session.get(Survey, 1)
        url, item = f"/s/{s.code}", [i for b in s.blocks for i in b.items if i.type == "semantic"][0]
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    page = p.get(url).data.decode()
    assert "parteiisch" in page and "Bewerten Sie den Artikel" in page
    p.post(url, data={"nav": "next", **{f"i{item.id}_p{n}": "6" for n in range(1, 9)}})
    results = client.get("/surveys/1/results").data.decode()
    assert "Subskala „affektiv“" in results and "Subskala „kognitiv“" in results


def test_every_construct_says_what_it_measures(app, client):
    """Facet "Bezug" for meta-analyses: a lasting trait, something about the person that can change, or the object."""
    with app.app_context():
        assert all(constructs.REFERENCE.get(s["key"]) for s in constructs.SEEDS)
        assert set(keys(constructs.search(tags=["bezug:gegenstand"]))) == {"kamb", "tam_bi", "tam_peou", "tam_pu"}
        assert "age" in keys(constructs.search(tags=["bezug:person-stabil"]))
        assert "fitai" in keys(constructs.search(tags=["bezug:person-veränderlich"]))
        # a database seeded before the facet gets it at the next start, without a new seed version
        c = constructs.get("tb")
        c.tags = [t for t in c.tags if t.facet != "bezug"]
        db.session.commit()
        constructs.ensure_seed()
        assert "bezug:person-stabil" in {t.slug for t in constructs.get("tb").tags}
    assert "Bezug" in client.get("/constructs/").data.decode()


def test_fitai_targets_and_foils(app, client):
    with app.app_context():
        spec = constructs.get("fitai").spec
    stmts = spec["config"]["statements"]
    assert len(stmts) == 12 and [s["reverse"] for s in stmts] == [False] * 6 + [True] * 6
    assert [s["subscale"] for s in stmts] == ["Target"] * 6 + ["Foil"] * 6
    assert spec["config"]["points"] == 5 and spec["config"]["labeling"] == "endpoints"
    de = spec["texts"]["de"]["question"]
    assert " Sie " not in de and " du " not in de and set(spec["texts"]) == {"de", "en"}


def test_gdms_five_styles_english_only(app, client, survey):
    with app.app_context():
        c = constructs.get("gdms")
        sts = c.spec["config"]["statements"]
        assert c.languages == ["en"] and c.doi == "10.1177/0013164495055005017"  # no validated German version
        assert [s["code"] for s in sts] == [f"GDMS_{n}" for n in range(1, 26)] and not any(s["reverse"] for s in sts)
        assert [s["subscale"] for s in sts] == ["Rational", "Intuitive", "Dependent", "Avoidant", "Spontaneous"] * 5
        assert c.spec["config"]["points"] == 5 and c.spec["config"]["labeling"] == "endpoints"
        assert keys(constructs.search("entscheidungsstil")) == ["gdms"]
    grant_instrument(app, "gdms")
    detail = client.get("/constructs/gdms?survey=1").data.decode()
    assert "doi.org/10.1177/0013164495055005017" in detail and "10.1037/t08399-000" in detail and "Spontaneous" in detail
    grant_instrument(client.application, "gdms")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gdms"})
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["construct"]["key"] == "gdms" and len(item.config["statements"]) == 25

    # PsycTests: the wording goes only to participants, so a public deposit leaves it out (rights review 2026-10-08)
    with app.app_context():
        from app import export, zenodo

        s = db.session.get(Survey, 1)
        wording = [t for texts in item.texts.values() for t in (texts.get("statements") or {}).values() if t]
        assert wording and wording[0] in export.constructs_md(s) and wording[0] in export.questionnaire_md(s)
        files = dict(zenodo.package(s))
        for name in ("questionnaire.md", "codebook.csv", "constructs.md"):
            text = files[name].decode()
            assert not any(w in text for w in wording) and "wording not included" in text.lower(), name
        assert "GDMS_1" in files["codebook.csv"].decode() and "`GDMS_25`" in files["constructs.md"].decode()
        assert "not covered by the licence of this deposit" in files["README.md"].decode()


def test_the_page_of_a_semantic_differential_lists_its_pairs(app, client):
    page = client.get("/constructs/kamb").data.decode()
    assert "data-sort-col>links<" in page and "data-sort-col>rechts<" in page and "KAMB_1" in page and "Subskala" in page
    assert "langweilig" in page and "interessant" in page
    page = client.get("/constructs/fitai").data.decode()  # endpoints only: no "2 = " for unlabelled points
    assert "1 = stimme gar nicht zu" in page and " 2 = " not in page


def test_saving_an_unlocked_likert_construct_keeps_its_subscales(app, client, survey):
    """S-2: the first save after unlocking keeps each statement's subscale (as the differential does); a statement
    taken out takes its subscale with it."""
    from werkzeug.datastructures import MultiDict

    from app import scales

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})
    client.post("/surveys/1/editor/items/2/unlock")
    with app.app_context():
        item = db.session.get(Item, 2)
        sts, texts = item.config["statements"], item.texts["de"]["statements"]
        subscales = [s.get("subscale") for s in sts]
    assert len(set(subscales)) == 5 and None not in subscales
    data = [("code", "BFI"), ("answer_mode", "nudge"), ("points", str(5)), ("labeling", "full")]
    for s in sts[1:]:  # the first statement is taken out
        data += [("stmt_id", s["id"]), ("stmt_code", s["code"]), ("stmt_text", texts[s["id"]])]
    data += [("stmt_reverse", s["id"]) for s in sts[1:] if s.get("reverse")]
    client.post("/surveys/1/editor/items/2", data=MultiDict(data))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert [s.get("subscale") for s in item.config["statements"]] == subscales[1:]
        assert sorted(len(codes) for _, codes in scales.groups(item)) == [1, 2, 2, 2, 2]


def test_method_section_takes_the_subscales_of_the_results_page(app, client, survey):
    """Mathe-Audit S-5/X-2: the semantic differential KAMB has two subscales; the method section reports them with the
    numbers of the results page (it had none for differentials and its own rule for means and checks)."""
    from app import methods, results
    from app.models import Response, utcnow

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kamb"})
    with app.app_context():
        item = next(i for b in db.session.get(Survey, 1).blocks for i in b.items if i.type == "semantic")
        ids = [p["id"] for p in item.config["pairs"]]
        for n in range(8):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={str(item.id): {pid: (n + k) % 6 + 1 if k % 3 else (n % 6) + 1
                                                            for k, pid in enumerate(ids)}}))
        db.session.commit()
        s = db.session.get(Survey, 1)
        page = next(e for e in results.compute(s, ("real",), False, True)["items"] if e["item"].id == item.id)["subscales"]
        fact = methods.facts(s)["constructs"][0]
        assert [x["name"] for x in fact["subscales"]] == [x["name"] for x in page] and len(page) == 2
        for mine, theirs in zip(fact["subscales"], page):
            assert (mine["k"], mine["alpha"], mine["omega"], mine["mean"], mine["sd"]) == \
                (theirs["k"], theirs["alpha"], theirs["omega"], theirs["mean"], theirs["sd"])
        assert "Subskala kognitiv (4 Items" in methods.draft(methods.facts(s))


def test_gender_self_description_can_be_left_out_per_element(app, client, survey):
    """Spiel et al. (2019): "Ich beschreibe mich selbst:" with a text field; the editor's tick takes it out of one
    element and back, also on the locked construct, and neither counts as a change of the construct."""
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gender"})

    def state():
        with app.app_context():
            item = db.session.get(Item, 2)
            return ([(o["id"], o["code"], bool(o.get("text"))) for o in item.config["options"]],
                    constructs.modifications(item, constructs.get("gender")), [c for c, _ in item.kind.export_columns(item)],
                    item.texts["de"]["options"].get("o5"))

    def save(shown, **extra):
        with app.app_context():
            question = db.session.get(Item, 2).texts["de"]["question"]
        data = {"code": "gender", "answer_mode": "optional", "question": question, "optional_options_shown": "1", **extra}
        if shown:
            data["optional_option"] = "o5"
        client.post("/surveys/1/editor/items/2", data=data)

    options, mods, columns, label = state()
    assert options == [("o1", "1", False), ("o2", "2", False), ("o3", "3", False), ("o5", "4", True), ("o4", "99", False)]
    assert mods == [] and columns == ["gender", "gender_4_text"] and label == "Ich beschreibe mich selbst:"
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="optional_option" value="o5" data-reload-saved checked' in page and "Option „Ich beschreibe mich selbst:“ anbieten" in page
    save(False)
    options, mods, columns, _ = state()
    assert [o[0] for o in options] == ["o1", "o2", "o3", "o4"] and mods == [] and columns == ["gender"]
    assert re.search(r'name="optional_option" value="o5" data-reload-saved\s+>', client.get("/surveys/1/editor/items/2").data.decode())
    save(False, option_id="o5", option_label="x")   # a stray row of the option does not bring it back
    assert [o[0] for o in state()[0]] == ["o1", "o2", "o3", "o4"]
    save(True)   # back at its place, with its wording
    options, mods, columns, label = state()
    assert [o[0] for o in options] == ["o1", "o2", "o3", "o5", "o4"] and mods == [] and label == "Ich beschreibe mich selbst:"
    with app.app_context():
        assert not constructs.badge(db.session.get(Item, 2))["modified"]
    # forms without the tick (scripts, older pages) leave the options alone
    with app.app_context():
        question = db.session.get(Item, 2).texts["de"]["question"]
    client.post("/surveys/1/editor/items/2", data={"code": "gender", "answer_mode": "optional", "question": question})
    assert len(state()[0]) == 5
    # the source in full, as for every construct
    with app.app_context():
        c = constructs.get("gender")
        assert c.citation.startswith("Spiel, K., Haimson, O. L., & Lottridge, D. (2019). How to do better with gender") and c.doi


def test_pool_paragraph_is_short_and_names_no_person():
    """Only what consent needs: what, by whom (the role), pseudonymised, legal basis, deletion (Philipp, 2026-10-06)."""
    from app.items.consent import pool_text

    for lang, basis in (("de", "Art. 6 Abs. 1 lit. a DSGVO"), ("de-du", "Art. 6 Abs. 1 lit. a DSGVO"), ("en", "Art. 6(1)(a) GDPR")):
        text = pool_text(lang, ["Alter", "Geschlecht"])
        assert "(Alter, Geschlecht)" in text and basis in text and "Brauner" not in text
        assert ("pseudonymisiert" in text or "pseudonymised" in text) and len(text) < 420
    assert "löschst du deine Antworten" in pool_text("de-du", ["Alter"])


def test_pool_copy_for_own_privacy_information(app, client, survey):
    """Tester feedback 2026-10-06: researchers learn where the pool applies and get a paragraph for their own data
    protection information (settings, wizard, consent editor, palette); without toolbox constructs nothing shows."""
    assert "Gemeinsame Auswertung standardisierter" not in client.get("/surveys/1").data.decode()
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})
    with app.test_request_context():  # the copy text links the legal notice with its full address
        consent = db.session.get(Survey, 1).consent_item
        copy = consent.kind.pool_copy(consent)
        assert "Big Five" in copy["de"] and "pseudonymisiert" in copy["de"] and "(Kontakt: http" in copy["de"]
        assert "meta-analyses" in copy["en"] and "Metaanalysen" not in copy["en"]
        consent_id = consent.id
    settings = client.get("/surveys/1").data.decode()
    assert "Gemeinsame Auswertung standardisierter" in settings and "data-copy-text=\"Standardisierte" in settings
    assert "durch den Betreiber" in settings and "gemeinsame Auswertung standardisierter Fragebögen (" in settings  # what the consent names
    assert "#einwilligung" in client.get(f"/surveys/1/editor/items/{consent_id}").data.decode()
    assert "Für die eigene Datenschutzinformation" not in client.get("/surveys/1/editor/").data.decode()  # Ralf, 2026-10-16: not in the palette
    assert "Text für die eigene Datenschutzinformation" in client.get("/surveys/1/field").data.decode()  # before the start


def test_new_terms_of_use_reach_stored_constructs_without_a_new_revision(app):
    """Rights review 2026-10-08: the terms in `notes` follow the code; the revision (pooled analyses) stays."""
    with app.app_context():
        c = constructs.get("tam_pu")
        version = c.seed_version
        c.notes = "alt"
        db.session.commit()
        constructs._seed()
        c = constructs.get("tam_pu")
        assert c.notes.startswith("Für den Wortlaut und seine Übersetzungen ist keine allgemeine") and c.seed_version == version
        assert constructs.get("kut").notes.startswith("Für den Wortlaut und seine Übersetzungen")


def test_an_unlocked_zis_scale_names_the_authors_permission(app, client, survey):
    """Rights review 2026-10-08: ZIS allows only the documented form without the authors' permission."""
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})
    grant_instrument(client.application, "kut")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:kut"})
    assert "data-zis-terms" not in client.get("/surveys/1/editor/items/2").data.decode()
    for item_id in (2, 3):
        client.post(f"/surveys/1/editor/items/{item_id}/unlock")
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert "data-zis-terms" in page and "Einverständnis der Autorinnen und Autoren" in page
    page = client.get("/surveys/1/editor/items/3").data.decode()
    assert "(KUT)" in page and "Die Struktur ist entsperrt" in page and "data-zis-terms" not in page  # KUT: not ZIS


def test_inserting_a_non_commercial_construct_says_so(app, client, survey):
    """GESIS/ZIS, APA PsycTests and journal scales allow only non-commercial research and teaching; the editor
    says it when one is inserted, an own wording says nothing (copyright audit 2026-10-16, U13)."""
    r = client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"}, follow_redirects=True)
    assert "nur nichtkommerzielle Forschung und Lehre" in r.data.decode()
    r = client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:age"}, follow_redirects=True)
    assert "nur nichtkommerzielle Forschung und Lehre" not in r.data.decode()


def test_scales_of_others_need_the_installations_wording(app):
    """The public code carries the structure of the scales of others, not their wording (rights review 2026-10-21):
    without instruments.json they are not seeded, with it they get its texts and the code's structure."""
    with app.app_context():
        assert constructs.get("asku").spec["texts"]["de"]["statements"]["s1"] == "Testaussage 1 von ASKU"
        assert [s["reverse"] for s in constructs.get("kusiv3").spec["config"]["statements"]] == [False, True, False]
        before = app.config["INSTRUMENT_WORDINGS"]
        app.config["INSTRUMENT_WORDINGS"] = ""
        try:
            assert constructs._instrument_spec("asku") is None and not constructs.instrument_available("bfi10")
            assert constructs.instrument_available("kamb")  # an own wording, in the code
            assert not constructs.instrument_available("kuk")  # wording only in the installation (Ralf, 2026-10-24)
            Construct = type(constructs.get("asku"))
            db.session.execute(db.delete(constructs.construct_tag))
            db.session.execute(db.delete(Construct))
            db.session.commit()
            constructs.ensure_seed()
            seeded = {c.key for c in db.session.scalars(db.select(Construct))}
            assert "kamb" in seeded and "gender" in seeded and not seeded & set(constructs.INSTRUMENTS)
        finally:
            app.config["INSTRUMENT_WORDINGS"] = before


def test_deploy_ships_the_wording_file():
    """instruments.json goes up with the installation's overlay (deploy-docker.sh) and lands in the image's
    instance_overlay/, where INSTRUMENT_WORDINGS looks by default."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert "parts+=(instruments.json)" in (root / "deploy/docker/deploy-docker.sh").read_text(encoding="utf-8")
    assert "COPY instance_overlay ./instance_overlay" in (root / "deploy/docker/Dockerfile").read_text(encoding="utf-8")
