"""Overlapping saves of the element editor (audit 2026-10-23, S-02): two saves on the same base must not both pass the
fingerprint check. The item's row is held from before the check until the commit (edit_guard.lock), so the later
save sees the earlier one's change as a conflict instead of overwriting it, and a beacon sent when the page is left
cannot be overwritten by its own tab's older save still under way.

The overlap is forced, not left to chance: a hook after loading the item holds both requests at a barrier (both
have read the old state, the window of the finding), then lets the first one finish before the second goes on."""
import threading

from flask import request
from werkzeug.datastructures import MultiDict

from app.editor import elements
from app.extensions import db
from app.models import Survey, SurveyMember
from tests.conftest import add
from tests.editor.test_autosave import E, OTHER, form_of, state


def _overlap(monkeypatch):
    """Both POSTs load the item, then "first" finishes before "second" goes on."""
    real = elements._get_item
    start = threading.Barrier(2)
    first_done = threading.Event()

    def held(survey, item_id):
        item = real(survey, item_id)
        who = request.headers.get("X-Overlap")
        if request.method == "POST" and who:
            start.wait(timeout=10)
            if who == "second":
                assert first_done.wait(timeout=10)
        return item

    monkeypatch.setattr(elements, "_get_item", held)
    return first_done


def _send(client, form, who, **extra):
    data = MultiDict(form)
    data.update({"autosave": "1", **extra})
    r = client.post(f"{E}/items/2", data=data, headers={"X-Requested-With": "fetch", "X-Overlap": who})
    return r.status_code, r.get_json()


def _run(first, second):
    """Run both sends at once; `first` sets the event when it has its answer."""
    out = {}

    def go(name, send):
        out[name] = send()

    threads = [threading.Thread(target=go, args=("first", first)), threading.Thread(target=go, args=("second", second))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    return out["first"], out["second"]


def _second_person(app):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role="admin", position=1))
        db.session.commit()
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    return other


def test_two_overlapping_saves_on_the_same_base_give_the_later_a_conflict(app, client, survey, monkeypatch):
    add(client, "text_input")
    other = _second_person(app)
    mine, theirs = form_of(client, 2), form_of(other, 2)
    assert mine["base"] == theirs["base"]
    old_question = mine["question"]
    theirs["question"] = "Changed by A"           # A changes the question
    mine["suffix"] = "m"                           # B changes another field and sends the old question along
    first_done = _overlap(monkeypatch)

    def a():
        try:
            return _send(other, theirs, "first")
        finally:
            first_done.set()

    (code_a, a_json), (code_b, b_json) = _run(a, lambda: _send(client, mine, "second"))
    assert code_a == 200 and a_json["status"] == "saved"
    assert code_b == 200 and b_json["status"] == "conflict" and b_json["who"] == "Kollegin"
    assert b_json["base"] == a_json["base"]
    question = state(app)[3]["de"]["question"]
    assert question == "Changed by A" != old_question  # A's change is not overwritten by B's old text


def test_page_leave_beacon_is_not_overwritten_by_its_own_older_save(app, client, survey, monkeypatch):
    """The beacon of a page being left carries everything typed so far and `after_own` (app.js); a save of the same
    tab still under way, with only part of it, may not land after it and put the older text back."""
    add(client, "text_input")
    beacon_tab = app.test_client()  # the same person: the page's save and its beacon are two requests at once
    beacon_tab.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    older = form_of(client, 2)
    newer = MultiDict(older)
    older["question"] = "Halb getippt"
    newer["question"] = "Fertig getippt"
    first_done = _overlap(monkeypatch)

    def beacon():
        try:
            return _send(beacon_tab, newer, "first", after_own="1")
        finally:
            first_done.set()

    (code_beacon, beacon_json), (code_save, save_json) = _run(beacon, lambda: _send(client, older, "second"))
    assert code_beacon == 200 and beacon_json["status"] == "saved"
    assert code_save == 200 and save_json["status"] == "conflict"
    assert state(app)[3]["de"]["question"] == "Fertig getippt"
