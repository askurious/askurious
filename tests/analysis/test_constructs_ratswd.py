"""The demographic standard questions of the RatSWD (2023) in the construct toolbox (app/ratswd.py; concept
Demografie-Standards 2026-10-15, decisions 5b and 7a)."""
from types import SimpleNamespace

from app import bibliography, constructs, ratswd
from app.extensions import db
from app.models import Item, Response, Survey, utcnow


def test_every_item_of_the_catalogue_is_a_construct_with_its_codes(app):
    with app.app_context():
        codes = {}
        for key in ratswd.KEYS:
            c = constructs.get(key)
            tags = {t.slug for t in c.tags}
            assert c.status == "review" and c.authors == "RatSWD" and c.year == 2023 and c.doi == "10.17620/02671.76"
            assert {"status:standardfrage", "quelle:ratswd-konsortswd", "art:demografie"} <= tags
            assert "CC BY 4.0" in c.notes and c.spec["texts"]["de"]["question"] and c.spec["texts"]["en"]["question"]
            codes[c.spec["code"]] = c.spec
        assert sorted(codes) == sorted(["ALT_1", "GES_1", "BIL_1", "BIL_4", "ERW_1", "ERW_2", "BER_1", "STE_1", "HNE_3",
                                        "HNE_4", "HNE_5", "HNE_6", "STA_1", "GEB_1", "GEB_3", "GEB_5", "HHG_1", "FAM_1"])
        alt = codes["ALT_1"]
        assert alt["type"] == "date_time" and alt["config"]["parts"] == "month"   # MM / JJJJ, chosen from lists
        assert codes["BER_1"]["type"] == "text_input"

        def options(code):
            spec = codes[code]
            return [(o["code"], spec["texts"]["de"]["options"][o["id"]], bool(o.get("missing")), bool(o.get("text")))
                    for o in spec["config"]["options"]]

        assert options("GES_1") == [("1", "männlich", False, False), ("2", "weiblich", False, False),
                                    ("3", "divers", False, False)]
        assert codes["GES_1"]["texts"]["de"]["question"] == "Welches Geschlecht haben Sie?"
        # "Weiß nicht" -1 and "Keine Angabe" -2 as in the catalogue, both missing
        assert options("STA_1")[2:] == [("-1", "Weiß nicht", True, False), ("-2", "Keine Angabe", True, False)]
        assert [o[0] for o in options("BIL_1")] == ["1", "2", "3", "4", "-1"]
        assert [o[0] for o in options("BIL_4")] == ["1", "2", "3", "4", "5", "6", "7", "8", "-1"]
        assert [o[0] for o in options("STE_1")] == [str(n) for n in range(1, 11)] + ["-1"]
        assert options("ERW_1")[8] == ("9", "Aus anderen Gründen aktuell nicht (hauptberuflich) erwerbstätig, und zwar:",
                                       False, True)
        assert options("HHG_1")[1] == ("2", "Mehrere Personen, und zwar:", False, True)  # the number in the text field
        assert [o[1] for o in options("FAM_1")] == ["Ledig, noch nie verheiratet gewesen", "Verheiratet", "Geschieden",
                                                    "Verwitwet", "Weiß nicht", "Keine Angabe"]
        assert [o[1] for o in options("HNE_4")][:3] == ["weniger als 500 Euro", "500 bis unter 1.000 Euro",
                                                        "1.000 Euro bis unter 1.500 Euro"]
        # the catalogue's note to respondents, below the question
        assert codes["FAM_1"]["texts"]["de"]["question"].endswith(
            "<small>Hinweis: Bitte behandeln Sie eine eingetragene Lebenspartnerschaft in dieser Frage wie eine Ehe.</small>")


def test_cards_warn_of_reidentification_and_special_categories(app, client):
    with app.app_context():
        assert "kleinen Stichproben erkennbar" in constructs.get("ratswd_alt1").notes
        for key in ("ratswd_geb3", "ratswd_geb5"):
            assert "Art. 9 DSGVO" in constructs.get(key).notes and "Einwilligungserklärung" in constructs.get(key).notes
        assert "Art. 9" not in constructs.get("ratswd_geb1").notes
    page = client.get("/constructs/ratswd_geb3").data.decode()
    assert "ethnische Herkunft" in page and "nicht in die gemeinsame Auswertung" in page
    english = client.get("/constructs/ratswd_geb3?lang=en").data.decode()
    assert "Art. 9 GDPR" in english or "Art. 9 DSGVO" in english


def test_the_citation_reads_as_a_report_of_an_organisation():
    seed = next(s for s in constructs.SEEDS if s["key"] == "ratswd_ges1")
    entry, = bibliography.entries(SimpleNamespace(citation=seed["citation"], doi=seed["doi"]))
    assert entry["kind"] == "report" and entry["authors"] == [("Rat für Sozial- und Wirtschaftsdaten", "")]
    assert entry["title"].startswith("Standardfragenkatalog zur Erhebung") and entry["title"].endswith("Nr. 4b)")
    assert entry["publisher"] == "RatSWD" and entry["doi"] == "10.17620/02671.76" and entry["year"] == 2023
    bib = bibliography.bibtex([entry])
    assert "@techreport{RatfurSozialundWirtschaftsdaten2023," in bib and "author = {{Rat für Sozial- und " in bib
    assert "institution = {RatSWD}" in bib
    assert "AU  - Rat für Sozial- und Wirtschaftsdaten\r\n" in bibliography.ris([entry])


def test_inserted_like_any_construct(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:ratswd_ges1"})
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:ratswd_alt1"})
    with app.app_context():
        ges, alt = db.session.get(Item, 2), db.session.get(Item, 3)
        assert (ges.code, ges.type, ges.config["construct"]["key"]) == ("GES_1", "single_choice", "ratswd_ges1")
        assert (alt.code, alt.type, alt.config["parts"]) == ("ALT_1", "date_time", "month")
        assert ges.kind.missing_codes(ges, "GES_1") == ()
        assert constructs.modifications(ges, constructs.get("ratswd_ges1")) == []


def test_standard_questions_stay_out_of_the_pool(app, client, survey):
    """Decision 7a: the RatSWD questions go neither into a pooled file of their own nor into those of the scales,
    and the consent does not name them; age and gender stay as before."""
    for key in ("bfi10", "ratswd_ges1", "ratswd_hhg1", "age"):
        client.post("/surveys/1/editor/blocks/2/items", data={"type": f"construct:{key}"})
    with app.app_context():
        from app import construct_pool
        from app.items import consent as consent_kind

        assert not constructs.pooled("ratswd_ges1") and constructs.pooled("gender") and constructs.pooled("age")
        s = db.session.get(Survey, 1)
        assert consent_kind._construct_names(s) == ["Big Five Inventory (BFI-10)"]
        assert [k for k, _n, _s in consent_kind._pooled(s)] == ["bfi10", "age"]
        bfi = db.session.get(Item, 2)
        s1 = bfi.config["statements"][0]["id"]
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                answers={"2": {s1: 2}, "3": "2", "4": "1", "5": "30"}))
        s.status = Survey.CLOSED
        db.session.commit()
        assert {row["construct"].key for row in construct_pool.overview()} == {"bfi10", "age"}
        assert constructs.pool("ratswd_ges1") == (constructs.pool("ratswd_ges1")[0], [])
    header = client.get("/constructs/bfi10/pool.csv").data.decode("utf-8-sig").splitlines()[0]
    assert header.endswith(",added_items,age,gender") and "GES_1" not in header and "HHG_1" not in header
    assert client.get("/constructs/ratswd_ges1/pool.csv").status_code == 404
    assert client.get("/constructs/bfi10").data.decode().count("pool.csv") == 1
    assert "pool.csv" not in client.get("/constructs/ratswd_ges1").data.decode()


def test_survey_with_only_standard_questions_has_no_pool_paragraph(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:ratswd_fam1"})
    with app.app_context():
        from app.items import consent as consent_kind

        s = db.session.get(Survey, 1)
        assert consent_kind._pooled(s) == [] and consent_kind._construct_names(s) == []
