"""Item-driven pages (ItemType.expand_pages / page_applies): pages marked "dyn" are shown only
if the item wants them, given the answers so far (the basis for adaptive conjoint)."""
from app.extensions import db
from app.items.conjoint import Conjoint
from app.models import Survey
from tests.conftest import CONSENT_OK
from tests.analysis.test_conjoint import setup


def test_dynamic_pages_are_skipped_by_the_item(app, client, survey, monkeypatch):
    plain = Conjoint.expand_pages

    def expand(self, survey, item, rng):
        state, pages = plain(self, survey, item, rng)
        return state, [{**p, "dyn": True} for p in pages]

    def applies(self, item, spec, answers):
        # tasks 3 and 4 only after option 1 in task 1
        return spec["task"] < 2 or (answers.get(item.id) or {}).get("t0") == 1

    monkeypatch.setattr(Conjoint, "expand_pages", expand)
    monkeypatch.setattr(Conjoint, "page_applies", applies)
    setup(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code

    def run(first):
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        seen = []
        for _ in range(6):
            html = p.get(f"/s/{uid}").data.decode()
            if "Vielen Dank" in html:
                return seen
            task = next(t for t in range(4) if f'name="i2_t{t}"' in html)
            seen.append(task)
            p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{task}": first if task == 0 else "1"})
        return seen

    assert run("2") == [0, 1]
    assert run("1") == [0, 1, 2, 3]
