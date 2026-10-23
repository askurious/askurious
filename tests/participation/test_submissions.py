"""Linked elements submitted for the construct toolbox (app/submissions.py): submit, ask back, answer, reject,
accept as a new toolbox construct, mark as checked; the toolbox filter "Meine und geteilte"."""
from app import constructs, linked
from app.extensions import db
from app.models import Construct, ConstructSubmission, Item
from tests.editor.test_linked import E, _linked_question

FORM = {"action": "submit", "description": "Vorwissen zu einem Thema, eine Frage.", "citation":
        "Muster, A., & Beispiel, B. (2024). Vorwissen messen. Zeitschrift für Tests, 3(2), 1–10.",
        "doi": "https://doi.org/10.1234/abcd.5", "scoring": "Einzelfrage, keine Rekodierung.", "licence": "CC BY 4.0", "rights": "1"}


def _submission(app):
    with app.app_context():
        return db.session.scalars(db.select(ConstructSubmission).order_by(ConstructSubmission.id.desc())).first()


def test_submit_ask_answer_accept(app, client, survey):
    key = _linked_question(app, client)
    page = client.get(f"/constructs/linked/{key}").get_data(as_text=True)
    assert "Für „Konstrukte“ einreichen" in page
    r = client.post(f"/constructs/linked/{key}", data={**FORM, "rights": ""}, follow_redirects=True)
    assert "Bestätigung der Nutzungsrechte" in r.get_data(as_text=True) and _submission(app) is None
    assert "Vorwissen messen" in r.get_data(as_text=True)  # what was typed comes back
    client.post(f"/constructs/linked/{key}", data=FORM)
    s = _submission(app)
    assert s.state == "submitted" and s.fields["doi"] == "10.1234/abcd.5"
    again = client.post(f"/constructs/linked/{key}", data=FORM, follow_redirects=True)
    assert "schon eingereicht" in again.get_data(as_text=True)

    listing = client.get("/admin/constructs").get_data(as_text=True)
    assert "Für „Konstrukte“ eingereicht" in listing and f"/admin/constructs/submitted/{s.id}" in listing
    detail = client.get(f"/admin/constructs/submitted/{s.id}").get_data(as_text=True)
    assert "Vorwissen messen" in detail and 'value="vorwissen"' in detail and 'value="Muster &amp; Beispiel"' in detail
    # ask back: the submitter sees it on the start page and answers on the element's page
    client.post(f"/admin/constructs/submitted/{s.id}", data={"action": "question", "note": "Gibt es eine englische Fassung?"})
    assert "Rückfrage zu „Vorwissen“" in client.get("/dashboard/").get_data(as_text=True)
    page = client.get(f"/constructs/linked/{key}").get_data(as_text=True)
    assert "Gibt es eine englische Fassung?" in page and "Antwort senden" in page
    assert "Rückfrage zu" not in client.get("/dashboard/").get_data(as_text=True)  # seen
    client.post(f"/constructs/linked/{key}", data={"action": "answer", "answer": "Nein, nur deutsch."})
    assert _submission(app).state == "submitted" and _submission(app).fields["answer"] == "Nein, nur deutsch."
    # accept: a new toolbox construct as a copy, unchecked; the element stays a linked element
    taken = client.post(f"/admin/constructs/submitted/{s.id}", data={"action": "accept", "key": "bfi10", "name": "Vorwissen"},
                        follow_redirects=True)
    assert "gibt es schon" in taken.get_data(as_text=True)
    client.post(f"/admin/constructs/submitted/{s.id}", data={"action": "accept", "key": "vorwissen", "name": "Vorwissen",
                                                               "authors": "Muster & Beispiel", "year": "2024"})
    with app.app_context():
        c = db.session.scalar(db.select(Construct).filter_by(key="vorwissen"))
        element = linked.get(key)
        assert (c.status, c.visibility, c.year, c.doi) == ("review", "public", 2024, "10.1234/abcd.5")
        assert c.spec == element.spec and element.status == "linked"
        assert "vorwissen" in [x.key for x in constructs.search("Vorwissen")]
    assert _submission(app).state == "accepted"
    assert "in „Konstrukte“ aufgenommen" in client.get("/dashboard/").get_data(as_text=True)
    assert "unter „Konstrukte“" in client.get(f"/constructs/linked/{key}").get_data(as_text=True)
    assert "aufgenommen" not in client.get("/dashboard/").get_data(as_text=True)
    client.post(f"/admin/constructs/submitted/{s.id}", data={"action": "verify"})
    with app.app_context():
        assert db.session.scalar(db.select(Construct).filter_by(key="vorwissen")).status == "verified"
    # it can be inserted from the toolbox like any construct
    client.post(f"{E}/blocks/2/items", data={"type": "construct:vorwissen"})
    with app.app_context():
        assert any((i.config.get("construct") or {}).get("key") == "vorwissen" for i in db.session.scalars(db.select(Item)))


def test_reject_and_submit_again_and_who_may(app, client, survey):
    key = _linked_question(app, client)
    client.post(f"/constructs/linked/{key}", data=FORM)
    s = _submission(app)
    client.post(f"/admin/constructs/submitted/{s.id}", data={"action": "reject", "note": ""})
    assert _submission(app).state == "submitted"  # a reason is needed
    client.post(f"/admin/constructs/submitted/{s.id}", data={"action": "reject", "note": "Keine Validierung veröffentlicht."})
    assert _submission(app).state == "rejected"
    page = client.get(f"/constructs/linked/{key}").get_data(as_text=True)
    assert "Nicht aufgenommen: Keine Validierung veröffentlicht." in page and "Für „Konstrukte“ einreichen" in page
    client.post(f"/constructs/linked/{key}", data=FORM)
    assert _submission(app).id != s.id and _submission(app).state == "submitted"

    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    other.post("/terms/", data={"accept": "1"})
    assert other.post(f"/constructs/linked/{key}", data=FORM).status_code == 404  # not usable for them
    assert other.get(f"/admin/constructs/submitted/{s.id}").status_code == 403


def test_toolbox_filter_own_and_shared(app, client, survey):
    key = _linked_question(app, client)
    page = client.get("/constructs/", query_string={"eigene": "1"}).get_data(as_text=True)
    assert "Meine und geteilte" in page and f"/constructs/linked/{key}" in page and "Treffer" not in page
    assert f"/constructs/linked/{key}" in client.get("/constructs/", query_string={"q": "Vorwissen"}).get_data(as_text=True)
    assert f"/constructs/linked/{key}" not in client.get("/constructs/", query_string={"q": "Persönlichkeit"}).get_data(as_text=True)


def test_dissolving_the_element_removes_its_submission(app, client, survey):
    key = _linked_question(app, client)
    client.post(f"/constructs/linked/{key}", data=FORM)
    client.post(f"/constructs/linked/{key}", data={"action": "dissolve"})
    assert _submission(app) is None
    assert client.get("/admin/constructs").status_code == 200
