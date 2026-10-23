"""Values of the levels of CBC and ACBC (config levels[].value): editor, long format, codebook."""
import csv
import io

from app.extensions import db
from app.items.attributes import level_value
from app.models import Item, Survey
from tests.conftest import CONSENT_OK
from tests.analysis import test_acbc
from tests.analysis import test_conjoint

E = "/surveys/1/editor"


def with_values(form, values):
    """The editor form with a value field per level ({level id: text}); levels not named get an empty field."""
    for key in [k for k in form.keys() if k.endswith("_id") and k.startswith("lvl_")]:
        aid = key[4:-3]
        for lid in form.getlist(key):
            form.add(f"lvl_{aid}_value", values.get(lid, ""))
    return form


def test_level_value_reads_numbers():
    assert level_value("5") == 5 and level_value("2,5") == 2.5 and level_value(" −10 ") == -10
    assert level_value("") is None and level_value("fünf") is None and level_value("nan") is None


def test_cbc_values_in_editor_and_long_format(app, client, survey):
    test_conjoint.setup(client)
    client.post(E + "/items/2", data=with_values(test_conjoint.conjoint_form(), {"a2l0": "50", "a2l1": "100", "a2l2": "149,5"}))
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        attrs = db.session.get(Item, 2).config["attributes"]
        assert [l.get("value") for l in attrs[1]["levels"]] == [50, 100, 149.5]
        assert all("value" not in l for l in attrs[0]["levels"])
    page = client.get(E + "/items/2").get_data(as_text=True)
    assert 'name="lvl_a2_value"' in page and 'value="149,5"' in page
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    for t in range(4):
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{t}": "1"})
    long = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/conjoint/2.csv").data.decode("utf-8-sig"))))
    assert "price_value" in long[0] and "look_value" not in long[0]
    assert all(r["price_value"] == {"1": "50", "2": "100", "3": "149.5"}[r["price"]] for r in long)
    # with answers the structure is locked, the values may still change
    client.post(E + "/items/2", data=with_values(test_conjoint.conjoint_form(), {"a2l0": "40", "a2l1": "100"}))
    with app.app_context():
        assert [l.get("value") for l in db.session.get(Item, 2).config["attributes"][1]["levels"]] == [40, 100, None]


def test_acbc_values_in_long_format_and_codebook(app, client, survey):
    test_acbc.setup(client)
    form = with_values(test_acbc.acbc_form(), {"a1l0": "1", "a1l1": "2", "a1l2": "3"})
    client.post(E + "/items/2", data=form)
    uid = test_acbc.start(app, client)
    test_acbc.run_participant(app, uid, possible={0, 2, 3, 5, 7})
    long = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/conjoint/2.csv").data.decode("utf-8-sig"))))
    assert "look_value" in long[0] and "speed_value" not in long[0]
    assert all(r["look_value"] == r["look"] for r in long if r["phase"] in ("byo", "screen", "tour"))
    codebook = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "1 = humanoid (Wert 1)" in codebook
