"""An offline answer that waited as a review case is marked in the export (app/export.py, column geprueft):
"bestaetigt" when the researchers took the values the phone sent, "geaendert" when they set others, empty for every
other entry. Before, a changed entry stood in the data like an answer of the person (business logic audit 2026-11-11,
GWP)."""
from app import export
from tests.diary.test_diary_integrity import _kept_page, _offline, _rating_field
from tests.diary.test_offline_preservation import latest, setup_offline


def _review_case(app, client, sent, accepted):
    phone, secret, due = setup_offline(app, client)
    version, field = _kept_page(app, phone, secret), _rating_field(app)
    assert _offline(phone, secret, version, field, due, due + 60000, form={field: [sent]}).json["status"] == "review"
    with app.app_context():
        rid = latest().id
    client.post("/diary/1/offline-reviews", data={"response": rid, field: accepted, "clarification": "Geklärt.",
                                                  "action": "accept"})
    with app.app_context():
        return export._diary_values(latest(), {})["geprueft"], latest().occasion["review"]


def test_a_changed_value_is_marked(app, client):
    mark, review = _review_case(app, client, "999", "4")
    assert review["form"] and mark == "geaendert"


def test_a_column_and_its_notes_in_both_languages():
    assert "geprueft" in [c for c, _ in export.DIARY_COLUMNS]
    assert "geprueft" in export.DIARY_NOTES and "geprueft" in export.DIARY_NOTES_EN


def test_the_original_taken_is_marked_confirmed():
    form = {"i7": ["3"]}
    occ = {"review": {"form": form, "accepted_form": {"i7": ["3"]}, "resolved_at": "2026-11-12T10:00:00"}}
    assert export._reviewed(occ) == "bestaetigt"
    assert export._reviewed({"review": {**occ["review"], "accepted_form": {"i7": ["4"]}}}) == "geaendert"
    assert export._reviewed({"review": {"form": form}}) == "" and export._reviewed({}) == ""
