"""The minimum age as a screen-out behind the age question (app/min_age.py; pre-test 2026-10-16: the minimum age of
the consent did not end younger people)."""
from app import flow
from app.extensions import db
from app.models import Survey
from tests.conftest import fill_consent


def _with_age(app):
    from app.wizard import add_demographics

    with app.app_context():
        survey = db.session.get(Survey, 1)
        add_demographics(survey)
        flow.normalize(survey)
        db.session.commit()


def _branch(app):
    from app import min_age

    with app.app_context():
        survey = db.session.get(Survey, 1)
        node = min_age.node(survey)
        if node is None:
            return None, None
        age = min_age.age_item(survey)
        top = [n for n in flow.tree(survey)["children"]]
        at = top.index(node)
        return node, (top[at - 1].get("block") == age.block_id, age.id)


def test_settings_say_the_minimum_age_is_only_confirmed(app, client, survey):
    fill_consent(client, min_age="18")
    page = client.get("/surveys/1").data.decode()
    assert "nur mit einem Häkchen" in page and "braucht der Fragebogen eine Altersfrage" in page
    _with_age(app)
    page = client.get("/surveys/1").data.decode()
    assert "Jüngere als 18 nach der Altersfrage beenden" in page


def test_one_click_builds_the_screen_out_and_it_follows_the_minimum_age(app, client, survey):
    fill_consent(client, min_age="18")
    _with_age(app)
    r = client.post("/surveys/1/min-age-screenout", data={"action": "apply"})
    assert r.headers["Location"].endswith("/surveys/1#consent-facts")
    node, (behind_age, age_id) = _branch(app)
    assert behind_age and node["cond"] == {"item": age_id, "op": "lt", "value": "18"}
    assert node["children"][0]["t"] == "end" and node["children"][0]["kind"] == "screenout"
    assert "Jünger als 18" in client.get("/surveys/1").data.decode()
    client.post("/surveys/1/min-age-screenout", data={"action": "apply"})   # a second click: still one
    with app.app_context():
        branches = [n for n, _, _ in flow.walk(flow.tree(db.session.get(Survey, 1))) if n.get("by") == "min_age"]
        assert len(branches) == 2                                              # the condition and its end
    fill_consent(client, min_age="16")
    assert _branch(app)[0]["cond"]["value"] == "16"
    fill_consent(client, min_age="")
    assert _branch(app)[0] is None


def test_the_end_can_be_taken_out_and_needs_an_age_question(app, client, survey):
    fill_consent(client, min_age="18")
    client.post("/surveys/1/min-age-screenout", data={"action": "apply"})
    assert _branch(app)[0] is None                                             # no age question: nothing built
    _with_age(app)
    client.post("/surveys/1/min-age-screenout", data={"action": "apply"})
    assert _branch(app)[0] is not None
    client.post("/surveys/1/min-age-screenout", data={"action": "remove"})
    assert _branch(app)[0] is None
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert not [n for n, _, _ in flow.walk(flow.tree(survey)) if n["t"] == "end"]


def test_younger_people_are_screened_out(app, client, survey):
    fill_consent(client, min_age="18")
    _with_age(app)
    client.post("/surveys/1/min-age-screenout", data={"action": "apply"})
    node, (_, age_id) = _branch(app)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert flow.holds(node["cond"], {age_id: "16"}, survey)
        assert not flow.holds(node["cond"], {age_id: "30"}, survey)
        assert not flow.holds(node["cond"], {age_id: "18"}, survey)
