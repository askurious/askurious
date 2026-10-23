"""Storage and mathematical integrity regressions from the fresh audit of 14 October 2026."""
import re
from app.extensions import db
from app.models import Response
from tests.participation.test_participate import build, consent_form, uid


def latest():
    return db.session.scalar(db.select(Response).order_by(Response.id.desc()))


def test_partial_required_matrix_is_persisted(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    phone = app.test_client()
    url = f"/s/{uid(app)}"
    phone.get(url)
    phone.post(url, data=consent_form())
    response = phone.post(url, data={"nav": "next", "i2_s1": "4", "i3": "42"})
    assert response.status_code == 200 and b"has-error" in response.data
    with app.app_context():
        assert latest().answers["2"]["s1"] == 4
        assert latest().answers["3"] == 42
    reloaded = phone.get(url).get_data(as_text=True)
    assert re.search(r'name="i2_s1"[^>]*value="4"[^>]*checked', reloaded)
