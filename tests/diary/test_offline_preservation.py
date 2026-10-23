"""Storage and mathematical integrity regressions from the fresh audit of 14 October 2026."""
from datetime import timedelta
from app import diary, export
from app.extensions import db
from app.models import Item, Response, Survey, utcnow
from tests.diary.test_diary import API, _active_from_today, _bearer, _enrol, _new, _prompt, entry_person
from tests.diary.test_diary_integrity import _kept_page, _offline, _rating_field


def setup_offline(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app, minutes_ago=20)
    return phone, secret, due


def latest():
    return db.session.scalar(db.select(Response).order_by(Response.id.desc()))


def test_valid_long_offline_text_is_stored_in_full(app, client):
    phone, secret, due = setup_offline(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        block = next(b for b in survey.blocks if diary.part_of(b) == "m1")
        item = Item(block_id=block.id, type="text_input", code="longtext", position=2,
                    config={"validation": "none", "max_length": 20000, "multiline": True},
                    texts={"de": {"question": "Text"}})
        db.session.add(item)
        db.session.commit()
        field = f"i{item.id}"
        key = str(item.id)
    version = _kept_page(app, phone, secret)
    response = _offline(phone, secret, version, _rating_field(app), due, due + 60000,
                        form={field: ["x" * 6000], _rating_field(app): ["4"]})
    assert response.status_code == 200 and response.json["status"] == "stored"
    with app.app_context():
        assert len(latest().answers[key]) == 6000


def test_invalid_offline_rating_is_retained_for_review(app, client):
    phone, secret, due = setup_offline(app, client)
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    response = _offline(phone, secret, version, field, due, due + 60000, form={field: ["999"]})
    assert response.status_code == 200 and response.json["status"] == "review"
    with app.app_context():
        assert latest().status == "review"
        assert field[1:] not in latest().answers

        assert latest().occasion["review"]["form"][field] == ["999"]
        assert not export.rows(db.session.get(Survey, 1)) or all(r.get("teil") != "m1" for r in export.rows(db.session.get(Survey, 1)))

def test_fetching_page_again_keeps_an_earlier_page_valid(app, client):
    phone, secret, due = setup_offline(app, client)
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    refreshed = phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret))
    assert refreshed.json["version"] == version  # identical pages reuse their original immutable receipt
    response = _offline(phone, secret, version, field, due, due + 60000)
    assert response.status_code == 200 and response.json["status"] == "stored"


def test_offline_random_order_is_preserved(app, client, monkeypatch):
    import random
    phone, secret, due = setup_offline(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        block = next(b for b in survey.blocks if diary.part_of(b) == "m1")
        item = Item(block_id=block.id, type="slider", code="battery", position=2,
                    config={"min": 0, "max": 100, "step": 1, "randomize": True,
                            "rows": [{"id": f"s{i}", "code": f"row{i}"} for i in range(8)]},
                    texts={"de": {"question": "Matrix", "rows": {f"s{i}": f"Statement {i}" for i in range(8)}}})
        db.session.add(item)
        db.session.commit()
        key, iid = str(item.id), item.id
    seeds = iter([random.Random(1), random.Random(2)])
    monkeypatch.setattr(random, "SystemRandom", lambda: next(seeds))
    kept = phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret)).json
    html = kept["html"]
    shown = sorted([f"s{i}" for i in range(8)], key=lambda sid: html.index(f'name="i{iid}_{sid}"'))
    with app.app_context():
        person = entry_person()
        person.settings = {**person.settings, "offline_at": (utcnow() - timedelta(hours=1)).isoformat()}
        from app.models import BrowserReceipt
        for receipt in db.session.scalars(db.select(BrowserReceipt).filter_by(survey_id=person.survey_id,
                                                                              scope=f"offline:{person.id}")):
            receipt.data = {**receipt.data, "at": person.settings["offline_at"]}
        db.session.commit()
    form = {_rating_field(app): ["4"], **{f"i{iid}_s{i}": ["3"] for i in range(8)}}
    response = _offline(phone, secret, kept["version"], _rating_field(app), due, due + 60000, form=form)
    assert response.status_code == 200, response.json
    with app.app_context():
        stored = latest().layout["stmt"][key]
        assert shown == stored, (shown, stored)


def test_valid_queued_offline_answer_keeps_its_questionnaire_version(app, client):
    phone, secret, due = setup_offline(app, client)
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        block = next(b for b in s.blocks if diary.part_of(b) == "m1")
        db.session.add(Item(block_id=block.id, type="info", code="notice", position=2,
                            config={}, texts={"de": {"question": "Additional notice"}}))
        s.qversion = 2
        db.session.commit()
    sent = _offline(phone, secret, version, field, due, due + 60000)
    assert sent.status_code == 200 and sent.json["status"] == "stored"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(part="m1")) == 1
        assert latest().qversion == 1


def test_review_requires_clarification_and_preserves_original(app, client):
    phone, secret, due = setup_offline(app, client)
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    assert _offline(phone, secret, version, field, due, due + 60000,
                    form={field: ["999"]}).json["status"] == "review"
    with app.app_context():
        rid = latest().id
    url = "/diary/1/offline-reviews"
    page = client.get(url)
    assert page.status_code == 200 and "999" in page.get_data(as_text=True)
    assert app.test_client().get(url).status_code == 302
    client.post(url, data={"response": rid, field: "4"})
    with app.app_context():
        assert db.session.get(Response, rid).status == "review"
    client.post(url, data={"response": rid, field: "4", "clarification": "Value confirmed by participant."})
    with app.app_context():
        row = db.session.get(Response, rid)
        assert row.status == Response.COMPLETED and row.answers[field[1:]] == 4
        assert row.occasion["review"]["form"][field] == ["999"]
        assert row.occasion["review"]["clarification"] == "Value confirmed by participant."
        assert row.occasion["review"]["resolved_by"] is not None


def test_review_preview_and_failed_correction_keep_original_and_entered_values(app, client):
    phone, secret, due = setup_offline(app, client)
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    _offline(phone, secret, version, field, due, due + 60000, form={field: ["999"]})
    with app.app_context():
        rid = latest().id
    url = "/diary/1/offline-reviews"
    failed = client.post(url, data={"response": rid, field: "888", "action": "check"})
    assert failed.status_code == 200
    text = failed.get_data(as_text=True)
    assert "Unverändertes Original" in text and "Vorgeschlagene Korrektur" in text
    assert ">888</textarea>" in text and "999" in text
    preview = client.post(url, data={"response": rid, field: "4", "action": "check"})
    assert "vorgeschlagenen Werte sind gültig" in preview.get_data(as_text=True)
    missing_note = client.post(url, data={"response": rid, field: "4", "action": "accept"})
    assert ">4</textarea>" in missing_note.get_data(as_text=True)
    with app.app_context():
        row = db.session.get(Response, rid)
        assert row.status == "review" and row.answers == {}
        assert row.occasion["review"]["form"][field] == ["999"]

    client.post(url, data={"response": rid, field: "4", "clarification": "Confirmed", "action": "accept"})
    with app.app_context():
        row = db.session.get(Response, rid)
        assert row.status == Response.COMPLETED
        assert row.occasion["review"]["accepted_form"][field] == ["4"]
        assert row.occasion["review"]["form"][field] == ["999"]


def test_review_uses_frozen_choice_and_matrix_wording(app, client):
    import copy
    from app import items, offline_answers

    phone, secret, due = setup_offline(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        block = next(b for b in survey.blocks if diary.part_of(b) == "m1")
        definitions = []
        for kind_key, group, question, label in [("multiple_choice", "options", "Original choice", "Former option"),
                                                 ("likert", "statements", "Original matrix", "Former statement")]:
            config = {**items.get(kind_key).default_config(), group: [{"id": "a", "code": "A"}]}
            item = Item(block_id=block.id, type=kind_key, code=kind_key, position=2, config=config,
                        texts={"de": {"question": question, group: {"a": label}}})
            db.session.add(item)
            db.session.flush()
            definitions.append({"id": item.id, "type": item.type, "code": item.code,
                                "answer_mode": item.answer_mode, "config": copy.deepcopy(config),
                                "texts": copy.deepcopy(item.texts)})
            item.texts = {"de": {"question": "Current " + kind_key, group: {"a": "Current wording"}}}
        response = Response(survey_id=1, source="real", part="m1", language="de", pid="frozen-wording",
                            qversion=1, started_at=utcnow(), last_active_at=utcnow())
        form = {f"i{definitions[0]['id']}": ["a"], f"i{definitions[1]['id']}_a": ["999"]}
        offline_answers.review(response, {"form": form}, {"language": "de", "items": definitions}, "invalid")
        db.session.add(response)
        db.session.commit()
        rid = response.id
    text = client.get("/diary/1/offline-reviews").get_data(as_text=True)
    for wording in ("Original choice", "Original matrix", "Former option", "Former statement", "Current multiple_choice", "Current likert"):
        assert wording in text
    with app.app_context():
        assert db.session.get(Response, rid).occasion["review"]["form"] == form
    corrected = {key: ["3"] if key.endswith("_a") else ["a"] for key in form}
    client.post("/diary/1/offline-reviews", data={**corrected, "response": rid, "clarification": "Confirmed from original wording", "action": "accept"})
    with app.app_context():
        row = db.session.get(Response, rid)
        assert row.status == Response.COMPLETED
        assert row.occasion["review"]["form"] == form
        assert row.occasion["review"]["accepted_form"] == corrected


def test_receipt_is_scoped_to_its_person(app, client):
    phone, secret, due = setup_offline(app, client)
    version = _kept_page(app, phone, secret)
    with app.app_context():
        code = db.session.get(Survey, 1).code
    other, other_secret = _enrol(app, code)
    response = _offline(other, other_secret, version, _rating_field(app), due, due + 60000)
    assert response.status_code == 409


def test_changed_answer_meaning_is_preserved_and_excluded(app, client):
    phone, secret, due = setup_offline(app, client)
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    with app.app_context():
        item = db.session.get(Item, int(field[1:]))
        item.config = {**item.config, "reverse": True}
        db.session.commit()
    reply = _offline(phone, secret, version, field, due, due + 60000)
    assert reply.status_code == 200 and reply.json["status"] == "review"
    with app.app_context():
        assert latest().occasion["review"]["form"][field] == ["4"]
        assert latest().completed_at is None


def test_erasing_person_removes_offline_page_receipts(app, client):
    from app.models import BrowserReceipt
    phone, secret, due = setup_offline(app, client)
    _kept_page(app, phone, secret)
    with app.app_context():
        person = entry_person()
        scope = f"offline:{person.id}"
        assert db.session.scalar(db.select(db.func.count()).select_from(BrowserReceipt).filter_by(scope=scope))
        db.session.delete(person)
        db.session.commit()
        assert not db.session.scalar(db.select(db.func.count()).select_from(BrowserReceipt).filter_by(scope=scope))


def test_replays_and_second_entries_cannot_overwrite_review_original(app, client):
    phone, secret, due = setup_offline(app, client)
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    first_uuid = "11111111-1111-1111-1111-111111111111"
    assert _offline(phone, secret, version, field, due, due + 60000, uuid=first_uuid,
                    form={field: ["999"]}).json["status"] == "review"
    assert _offline(phone, secret, version, field, due, due + 60000, uuid=first_uuid).json["status"] == "review"
    second = _offline(phone, secret, version, field, due, due + 60000,
                      uuid="22222222-2222-2222-2222-222222222222")
    assert second.status_code == 409
    with app.app_context():
        assert latest().occasion["review"]["form"][field] == ["999"]
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(part="m1")) == 1


def test_fetching_a_randomised_page_again_reuses_its_receipt(app, client):
    """Abgleich 2026-10-15 NB-2: every fetch drew a new order and kept a whole questionnaire for 30 days; the layout
    is now drawn once a day per person and module, so the app's fetch every 15 minutes reuses the receipt."""
    from app.models import BrowserReceipt

    phone, secret, due = setup_offline(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        block = next(b for b in survey.blocks if diary.part_of(b) == "m1")
        db.session.add(Item(block_id=block.id, type="slider", code="battery", position=2,
                            config={"min": 0, "max": 100, "step": 1, "randomize": True,
                                    "rows": [{"id": f"s{i}", "code": f"row{i}"} for i in range(8)]},
                            texts={"de": {"question": "Matrix", "rows": {f"s{i}": f"Statement {i}" for i in range(8)}}}))
        db.session.commit()
    versions = {phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret)).json["version"] for _ in range(4)}
    assert len(versions) == 1
    with app.app_context():
        person = entry_person()
        assert db.session.scalar(db.select(db.func.count()).select_from(BrowserReceipt).where(
            BrowserReceipt.scope == f"offline:{person.id}")) == 1


def test_kept_pages_per_person_are_capped(app, client, monkeypatch):
    """Abgleich 2026-10-15 NB-2: at most MAX_PAGES kept pages per person, the ones fetched longest ago go first.
    Pages fetched within OFFLINE_DAYS + 1 day are never trimmed (audit 2026-10-21, F6): here they were fetched
    10 days ago."""
    from types import SimpleNamespace

    from app import offline_answers
    from app.models import BrowserReceipt

    setup_offline(app, client)
    monkeypatch.setattr(offline_answers, "MAX_PAGES", 2)
    with app.app_context():
        survey, person = db.session.get(Survey, 1), entry_person()
        for row in db.session.scalars(db.select(BrowserReceipt).where(BrowserReceipt.scope == f"offline:{person.id}")):
            row.expires_at = utcnow() + timedelta(days=20)
        versions = []
        for n in range(4):
            versions.append(offline_answers.capture(survey, person, SimpleNamespace(
                part="m1", layout={"n": n}, qversion=1, language="de"), []))
            if n < 3:  # fetched 10 days ago, the later ones a little after each other
                for row in db.session.scalars(db.select(BrowserReceipt).where(
                        BrowserReceipt.scope == f"offline:{person.id}", BrowserReceipt.expires_at > utcnow() + timedelta(days=25))):
                    row.expires_at = utcnow() + timedelta(days=20, minutes=n)
                db.session.flush()
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count()).select_from(BrowserReceipt).where(
            BrowserReceipt.scope == f"offline:{person.id}")) == 2
        assert offline_answers.page(survey, person, versions[-1], "m1") is not None
        assert offline_answers.page(survey, person, versions[0], "m1") is None


def test_review_cases_are_limited_in_size_and_number(app, client, monkeypatch):
    """Abgleich 2026-10-15 NB-3: an invalid entry kept up to 2 MB of raw form as a review case, without a limit on
    their number. Now at most REVIEW_BYTES per case and MAX_OPEN_REVIEWS open cases per person; nothing is stored
    beyond, and the app keeps the entry in its queue."""
    from app import offline_answers

    phone, secret, due = setup_offline(app, client)
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    monkeypatch.setattr(offline_answers, "REVIEW_BYTES", 200)
    big = _offline(phone, secret, version, field, due, due + 60000, form={field: ["999", "x" * 500]})
    assert big.status_code == 422 and big.json["error"] == "invalid"
    monkeypatch.setattr(offline_answers, "REVIEW_BYTES", 64 * 1024)
    monkeypatch.setattr(offline_answers, "MAX_OPEN_REVIEWS", 0)
    many = _offline(phone, secret, version, field, due, due + 60000, form={field: ["999"]})
    assert many.status_code == 429 and many.json["error"] == "limit"
    with app.app_context():
        assert not db.session.scalar(db.select(db.func.count(Response.id)).filter_by(status="review"))
    monkeypatch.setattr(offline_answers, "MAX_OPEN_REVIEWS", 50)
    assert _offline(phone, secret, version, field, due, due + 60000, form={field: ["999"]}).json["status"] == "review"


def test_review_page_shows_a_page_of_cases_at_a_time(app, client):
    """Abgleich 2026-10-15 NB-3: the page "Offline-Antworten prüfen" rendered every case of a study at once."""
    from app.diary import REVIEW_PAGE

    setup_offline(app, client)
    with app.app_context():
        person = entry_person()
        for n in range(REVIEW_PAGE + 3):
            db.session.add(Response(survey_id=1, language="de", answers={}, qversion=1, pid=f"rev{n:03d}", status="review",
                                    position=0, source="real", is_preview=False, enrolment_id=person.id,
                                    received_at=utcnow(), occasion={"part": "m1", "offline": True, "review": {
                                        "reason": "invalid", "errors": {}, "form": {}, "page": {"items": []}}}))
        db.session.commit()
    first = client.get("/diary/1/offline-reviews").get_data(as_text=True)
    second = client.get("/diary/1/offline-reviews?page=2").get_data(as_text=True)
    assert first.count("rev0") == REVIEW_PAGE and second.count("rev0") == 3
    assert "page=2" in first and 'rel="next"' in first
