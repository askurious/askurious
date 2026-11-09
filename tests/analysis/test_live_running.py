from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import add


def test_live_feedback_counts_running_participations_with_the_tick(app, client, survey):
    """u7pauw, 2026-11-09: a class waited behind a code word at about 75 %, nobody had completed, and every live
    feedback said "too few answers". With "auch laufende Teilnahmen zählen" (on unless switched off, Ralf 2026-11-09) the
    running participations that gave the answer count, the person's own left out; without it only completed ones."""
    from flask import g

    from app import balance

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:asku"})     # 2
    add(client, "page_break")                                                              # 3
    add(client, "scale_profile")                                                           # 4
    client.post("/surveys/1/editor/items/4", data={"question": "Profil", "sources": "2", "show_sample": "1"})
    with app.app_context():
        sids = [st["id"] for st in db.session.get(Item, 2).config["statements"]]
        for v in (1, 2, 3, 4, 5, 2):
            db.session.add(Response(survey_id=1, source="real", status=Response.ACTIVE,
                                    answers={"2": {sid: v for sid in sids}}))
        db.session.add(Response(survey_id=1, source="real", status=Response.ACTIVE, answers={}))  # not there yet
        s = db.session.get(Survey, 1)
        s.status = Survey.RUNNING
        db.session.commit()
        assert balance.answers(s, "2") == []                                   # only completed ones, as before
        assert len(balance.answers(s, "2", running=True)) == 6
        item = db.session.get(Item, 4)
        assert item.kind.samples(item, [db.session.get(Item, 2)], [None])[0][0] == 6   # on without being ticked
        item.config = {**item.config, "count_running": False}
        db.session.commit()
        assert not item.kind.samples(item, [db.session.get(Item, 2)], [None])[0][1]
    # the tick, also in a running survey (it changes only who is counted)
    client.post("/surveys/1/editor/items/4", data={"question": "Profil", "count_running_shown": "1",
                                                   "count_running": "1", "sources": "2", "show_sample": "1"})
    with app.test_request_context():
        item = db.session.get(Item, 4)
        assert item.config.get("count_running") is True
        src = db.session.get(Item, 2)
        n, mean = item.kind.samples(item, [src], [None])[0]
        assert n == 6 and mean is not None
        g.participation = db.session.get(Response, 1)                          # one's own does not count
        lists = item.kind.answer_lists(item)
        assert [rid for rid, _ in lists[0]] == [2, 3, 4, 5, 6]
        app.config["BALANCE_SECONDS"] = 15                                     # kept: per person the same list,
        try:                                                                   # so derived() keeps its result
            first = item.kind.answer_lists(item)[0]
            assert item.kind.answer_lists(item)[0] is first and [r for r, _ in first] == [2, 3, 4, 5, 6]
        finally:
            app.config["BALANCE_SECONDS"] = 0
    client.post("/surveys/1/editor/items/4", data={"question": "Profil", "count_running_shown": "1", "sources": "2"})
    with app.app_context():
        assert db.session.get(Item, 4).config.get("count_running") is False
