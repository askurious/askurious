"""Code review 2026-10-15, F7: runtime.applicable read the flow's conditions (a walk through the whole flow) for
every entry it was asked about, several times per page. Now once per question over several entries, and not at
all when no entry of the layout has a condition."""
import sys

from app import flow, runtime
from tests.participation.test_participate import build, consent_form, uid


def test_a_flow_without_conditions_is_not_walked_for_them(app, client, survey, monkeypatch):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{uid(app)}"
    real, calls = flow.conditions_by_id, []

    def counted(survey):
        calls.append(sys._getframe(1).f_code.co_filename)
        return real(survey)

    monkeypatch.setattr(flow, "conditions_by_id", counted)
    p = app.test_client()
    p.get(url)
    p.post(url, data=consent_form())
    p.post(url, data={"nav": "next", "i2_s1": "2", "i2_s2": "2", "i3": "41"})
    assert "Fast fertig" in p.get(url).data.decode()
    assert not [c for c in calls if c == runtime.__file__]


def test_the_conditions_are_read_once_for_several_entries(app, monkeypatch):
    layout = {"pages": [{"items": []}, {"items": [], "cond": ["b1"]}, {"items": [], "cond": ["b1"]}, {"end": "e"}]}
    calls = []
    monkeypatch.setattr(flow, "conditions_by_id", lambda survey: calls.append(1) or {"b1": {}})
    monkeypatch.setattr(flow, "cond_holds", lambda conds, c, answers, survey: False)
    with app.app_context():
        assert runtime.next_index(None, layout, 0, {}) == 3
    assert calls == [1]
