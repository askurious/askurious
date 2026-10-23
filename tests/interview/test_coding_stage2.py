"""Categorising open answers, stage 2 (app/coding_check.py, app/coding_ai.py, merge and split in app/coding.py):
Krippendorff's alpha, the blind second coder with the comparison and its disagreements, the AI suggestions that count
only once taken over, merging and splitting categories."""
import json

import pytest

from tests.conftest import own_ai
from app import ai
from app.coding_check import alpha_nominal
from app.extensions import db
from app.models import Response, Survey, SurveyMember, User
from tests.interview.test_coding import _add, _assign, _codings, _survey

OTHER = "0000-0002-1825-0097"


def test_alpha_nominal():
    assert alpha_nominal([(1, 1), (2, 2), (1, 1)]) == 1
    # margins 3 and 5 of 8 values, two differing pairable values: 1 - 7 * 2 / (64 - 9 - 25)
    assert alpha_nominal([(1, 1), (1, 2), (2, 2), (2, 2)]) == pytest.approx(1 - 14 / 30)
    assert alpha_nominal([(1, 1), (1, 1)]) is None  # every value the same: not defined
    assert alpha_nominal([(1, 2)]) is None
    assert alpha_nominal([(1, 2), (2, 1)]) < 0  # systematic disagreement


def _second_person(app):
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, name="Zweite", role="editor", position=1))
        db.session.commit()
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Zweite")
    other.post("/terms/", data={"accept": "1"})
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        member = db.session.scalar(db.select(SurveyMember).filter_by(orcid=OTHER))
        member.user_id = user.id
        db.session.commit()
        return other, user.id


def test_second_coder_codes_blind_and_the_page_compares(app, client, survey):
    key = _survey(app, client)
    _add(client, "Preis")
    _add(client, "Lieferung")
    _assign(client, "zu teuer", 1)
    _assign(client, "lieferung dauert", 2)
    _assign(client, "toll", 99)
    other, uid = _second_person(app)
    page = client.get(f"/surveys/1/categorise/{key}").data.decode()
    assert "Zweitkodierung anlegen" in page and "Zweite" in page
    client.post(f"/surveys/1/categorise/{key}/second/start", data={"coder": str(uid)})
    with app.app_context():
        second = db.session.get(Survey, 1).codings[key]["second"]
    assert second["by"] == uid and len(second["ids"]) == 3 and not second["done"]  # fewer than 50 texts: all of them
    # blind: the main page leads to the own page, the first coding is refused, the results show no categories
    r = other.get(f"/surveys/1/categorise/{key}")
    assert r.status_code == 302 and r.headers["Location"].endswith("/second")
    blind = other.get(f"/surveys/1/categorise/{key}/second").data.decode()
    assert "Stichprobe" in blind and "zu teuer" in blind.lower() and 'name="cat" value="1" checked' not in blind
    assert other.post(f"/surveys/1/categorise/{key}/assign", data={"norm": "toll", "cat": "1"}).status_code == 403
    assert "kategorisierte Antworten" not in other.get("/surveys/1/results").data.decode()
    # the first coder may not write the second slot
    assert client.post(f"/surveys/1/categorise/{key}/assign", data={"norm": "toll", "cat": "1", "slot": "second"}).status_code == 403

    def code2(norm, *cats):
        return other.post(f"/surveys/1/categorise/{key}/assign", data={"norm": norm, "slot": "second", "cat": [str(c) for c in cats]},
                          headers={"Accept": "application/json"})

    assert code2("zu teuer", 1).get_json()["status"] == "saved"
    code2("lieferung dauert", 1)  # differs
    other.post(f"/surveys/1/categorise/{key}/second/finish")
    with app.app_context():
        assert not db.session.get(Survey, 1).codings[key]["second"]["done"]  # one answer still without categories
    code2("toll", 99)
    other.post(f"/surveys/1/categorise/{key}/second/finish")
    with app.app_context():
        from app import coding, coding_check

        s = db.session.get(Survey, 1)
        item, col, _ = coding.find(s, key)
        rep = coding_check.report(s, item, col, key)
    assert rep["n"] == 3 and rep["agree"] == pytest.approx(2 / 3) and len(rep["differ"]) == 1
    assert rep["differ"][0]["norm"] == "lieferung dauert"
    page = client.get(f"/surveys/1/categorise/{key}").data.decode()
    assert "Krippendorffs α" in page and "Abweichungen klären (1)" in page
    # clarifying changes the first coding, not the comparison
    _assign(client, "lieferung dauert", 1)
    with app.app_context():
        s = db.session.get(Survey, 1)
        again = coding_check.report(s, item, col, key)
    assert again["agree"] == rep["agree"] and again["differ"][0]["clarified"]
    assert "Zweitkodierung: 3 verschiedene Antworttexte" in client.get("/surveys/1/export/kodierleitfaden.md").data.decode()
    # the second coder's categories never reach the export
    head = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0]
    assert "~" not in head
    # discarding takes the sample and the second slot away
    client.post(f"/surveys/1/categorise/{key}/second/stop")
    assert not any(k.endswith(("~2", "~1")) for c in _codings(app).values() if c for k in c)


def test_ai_suggestions_count_only_once_taken_over(app, client, survey, monkeypatch):
    key = _survey(app, client)
    app.config["OPENAI_API_KEY"] = "test"
    own_ai(app)  # the AI coding help runs only over an own access (2026-10-21)
    calls = []

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None):
        calls.append((purpose, json.loads(prompt)))
        if purpose == "coding_scheme":
            return json.dumps({"categories": [{"name": "Preis", "definition": "zu teuer", "example": "Zu teuer"},
                                              {"name": "Lieferung", "definition": "Versand", "example": "Lieferung dauert"},
                                              {"name": "Sonstiges", "definition": "", "example": ""}]})
        return json.dumps({"answers": [{"n": i, "ids": [1] if "teuer" in a["text"].lower() else [99]}
                                       for i, a in enumerate(json.loads(prompt)["answers"])]})

    monkeypatch.setattr(ai, "generate_text", fake)
    client.post(f"/surveys/1/categorise/{key}/ai/scheme")
    assert not calls  # only once a member confirmed that the participants were told (audit 2026-10-10, B-1)
    client.post(f"/surveys/1/categorise/{key}/ai/informed", data={"informed": "1"})
    client.post(f"/surveys/1/categorise/{key}/ai/scheme")
    assert calls[0][0] == "coding_scheme" and "Zu teuer" in calls[0][1]["answers"]
    with app.app_context():
        found = db.session.get(Survey, 1).codings[key]
    assert [p["name"] for p in found["proposals"]] == ["Preis", "Lieferung"]  # "Sonstiges" is there already
    assert [c["id"] for c in found["cats"]] == [98, 99]  # nothing taken over yet
    client.post(f"/surveys/1/categorise/{key}/ai/take", data={"n": "all"})
    with app.app_context():
        found = db.session.get(Survey, 1).codings[key]
    assert [c["name"] for c in found["cats"]][:2] == ["Preis", "Lieferung"] and "proposals" not in found
    assert found["cats"][0]["definition"] == "zu teuer"
    client.post(f"/surveys/1/categorise/{key}/ai/assign")
    got = _codings(app)
    assert got["p0"] == {key + "~ai": [1]} and got["p3"] == {key + "~ai": [99]}  # suggestions only
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert all(r[c] == "" for r in rows for c in r if c.startswith("why_k"))  # nothing counted yet
    _assign(client, "toll", 2)  # a person's own choice stays
    client.post(f"/surveys/1/categorise/{key}/ai/accept")
    got = _codings(app)
    assert got["p0"][key] == [1] and got["p0"][key + "~ki"] == 1
    assert got["p3"][key] == [2] and key + "~ki" not in got["p3"]
    assert "aus KI-Vorschlag" in client.get(f"/surveys/1/categorise/{key}").data.decode()
    assert "gesammelt aus KI-Vorschlägen" in client.get("/surveys/1/export/kodierleitfaden.md").data.decode()
    # a person's tick clears the mark
    _assign(client, "zu teuer", 1)
    assert key + "~ki" not in _codings(app)["p0"]


def test_merge_and_split(app, client, survey):
    key = _survey(app, client)
    for name in ("Preis", "Kosten", "Lieferung"):
        _add(client, name)
    _assign(client, "zu teuer", 2)
    _assign(client, "lieferung dauert", 1, 2)
    client.post(f"/surveys/1/categorise/{key}/merge", data={"source": "2", "target": "1"})
    with app.app_context():
        assert [c["id"] for c in db.session.get(Survey, 1).codings[key]["cats"]] == [1, 3, 98, 99]
    got = _codings(app)
    assert got["p0"][key] == [1] and got["p2"][key] == [1]
    assert client.post(f"/surveys/1/categorise/{key}/merge", data={"source": "98", "target": "1"}).status_code == 302
    with app.app_context():
        assert 98 in [c["id"] for c in db.session.get(Survey, 1).codings[key]["cats"]]  # the fixed ones stay
    client.post(f"/surveys/1/categorise/{key}/split/1", data={"name": "Rabatt"})
    with app.app_context():
        assert [c["name"] for c in db.session.get(Survey, 1).codings[key]["cats"]][:3] == ["Preis", "Lieferung", "Rabatt"]
    got = _codings(app)
    assert got["p0"][key + "~r"] == 1 and got["p2"][key + "~r"] == 1
    assert "neu prüfen" in client.get(f"/surveys/1/categorise/{key}").data.decode()
    _assign(client, "zu teuer", 4)
    assert key + "~r" not in _codings(app)["p0"]


def test_ai_proposals_are_dropped_by_editors_only(app, client, survey):
    """coding_ai.ai_drop_proposals had no direct test (code review 2026-10-15, L8): a viewer is turned away, an editor
    drops the AI's proposed categories and keeps the scheme; no AI is asked."""
    from app import coding

    key = _survey(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        found = coding.scheme(s, key) or coding.new_scheme()
        coding._store(s, key, {**found, "proposals": [{"name": "Preis", "definition": "", "example": ""}]})
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, name="Zweite", role="viewer", position=1))
        db.session.commit()
    viewer = app.test_client()
    viewer.get(f"/auth/dev-login?orcid={OTHER}&name=Zweite")
    viewer.post("/terms/", data={"accept": "1"})
    assert viewer.post(f"/surveys/1/categorise/{key}/ai/drop").status_code == 302
    with app.app_context():
        assert db.session.get(Survey, 1).codings[key]["proposals"]
    r = client.post(f"/surveys/1/categorise/{key}/ai/drop")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/surveys/1/categorise/{key}#kategorien")
    with app.app_context():
        found = db.session.get(Survey, 1).codings[key]
    assert "proposals" not in found and [c["id"] for c in found["cats"]] == [98, 99]
