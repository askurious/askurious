"""English addresses (app/url_names.py, Philipp 2026-10-08): no German path segment in any route; the German ones of
before lead with 308 to the English ones (Ralf 2026-10-08, code audit W6)."""
from app.url_names import RENAMED

OLD = "/" + "ablauf"  # kept apart, so that a rewrite of the tests' addresses never touches it


def test_no_route_keeps_a_german_segment(app):
    german = sorted({seg for r in app.url_map.iter_rules() if not r.rule.startswith("/rechenknecht/")
                     for seg in r.rule.split("/") if seg in RENAMED})
    assert german == []
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/surveys/<sid:survey_id>/flow/" in rules and "/surveys/<sid:survey_id>/researchdesign" in rules
    assert "/slack/aktion" in rules and "/rechenknecht/stand" in rules        # called by services at a fixed address


def test_old_german_addresses_lead_to_the_english_ones(app, client, survey):
    r = client.get("/surveys/1" + OLD + "/?x=1")
    assert r.status_code == 308 and r.headers["Location"].endswith("/surveys/1/flow/?x=1")
    r = client.get("/surveys/1/setup/" + "studienplan")
    assert r.status_code == 308 and r.headers["Location"].endswith("/surveys/1/setup/researchdesign")
    assert client.get("/surveys/1/setup/researchdesign").status_code in (200, 302)
    assert client.get("/surveys/1/setup/team").status_code in (200, 302)
    r = client.get("/" + "impressum")
    assert r.status_code == 308 and r.headers["Location"].endswith("/imprint")
    r = client.get("/surveys/1/export/" + "methodenteil.docx")
    assert r.status_code == 308 and r.headers["Location"].endswith("/surveys/1/export/methods-section.docx")
    r = client.post("/surveys/1/" + "veroeffentlichungen", data={"a": "1"})  # method and form stay (308)
    assert r.status_code == 308 and r.headers["Location"].endswith("/surveys/1/publications")
    r = client.get("/s/ABCDEF/" + "zurueck?t=abc")                            # return link handed to a web app
    assert r.status_code == 308 and r.headers["Location"].endswith("/s/ABCDEF/back?t=abc")
    assert client.get("/" + "gibtsnicht").status_code == 404                  # not a German address of before
    assert client.get("/surveys/1/" + "ablaufx").status_code == 404
