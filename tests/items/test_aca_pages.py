"""The questionnaire editor and the flow page open with an ACA element (pre-test 2026-10-20: an ACA
element made the questionnaire editor and the flow page fail with a 500, because AttributeLevels said it had an
image set and app/design.stats asked it for required_keys, which ACA does not have)."""


def test_editor_and_flow_open_with_an_aca_element(app, client, survey):
    from app.extensions import db
    from app.models import User

    with app.app_context():
        for u in db.session.scalars(db.select(User)):
            u.experimental = True
        db.session.commit()
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "aca"})
    with app.app_context():
        from app.models import Item
        assert db.session.scalar(db.select(Item).filter_by(type="aca")) is not None
    assert client.get("/surveys/1/editor/").status_code == 200
    assert client.get("/surveys/1/flow/").status_code == 200
