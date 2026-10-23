"""Audit 2026-10-09, R1 and R2: a dashboard link per person never shows quota tiles.

A cell going from 0 to 1 told a student the answer of the one person she recruited (R1), and the quota of
"Höchstzahl je Link" named the codes of all the other links (R2). A panel provider's link keeps its quotas.
"""
import secrets
from datetime import timedelta

from app.extensions import db
from app.models import Board, BoardShare, Survey, utcnow
from tests.conftest import add


def _link(app, board_id, param, value):
    with app.app_context():
        share = BoardShare(board_id=board_id, token=secrets.token_urlsafe(24), label="x", hall=False, revoked=False,
                           opens=0, param=param, value=value,
                           expires_at=utcnow() + timedelta(days=30))
        db.session.add(share)
        db.session.commit()
        return share.token


def _seen(app, token):
    client = app.test_client()
    return client.get(f"/d/{token}").get_data(as_text=True) + client.get(f"/d/{token}/state").get_data(as_text=True)


def test_link_per_person_shows_no_quotas_and_no_other_codes(app, client, survey):
    app.config["BOARD_CHECK_SECONDS"] = 0
    add(client, "single_choice")
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna\nBen\nCem"})
    client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": "5"})
    client.post("/surveys/1/dashboards/new", data={"template": "field"})
    with app.app_context():
        from app import panel

        a, b, c = next(p for p in panel.config(db.session.get(Survey, 1))["params"] if p["name"] == "w")["allowed"]
        keys = [t["key"] for t in db.session.get(Board, 1).tiles]
    assert any(k.startswith("quota:") for k in keys)  # the dashboard itself has the quota of the links
    seen = _seen(app, _link(app, 1, "w", a))
    assert 'data-key="quota:' not in seen
    assert b not in seen and c not in seen
    # the researcher's own view of the same dashboard keeps the quota
    with app.test_request_context():
        from app import boards

        assert any(t["key"].startswith("quota:") for t in boards.build(db.session.get(Board, 1), "de")["tiles"])


def test_panel_provider_link_keeps_its_quotas(app, client, survey):
    from app.models import Quota, QuotaCell

    app.config["BOARD_CHECK_SECONDS"] = 0
    add(client, "single_choice")
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna"})
    client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": "5"})
    client.post("/surveys/1/dashboards/new", data={"template": "field"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {**s.panel, "params": s.panel["params"] + [{"name": "pv", "mode": "store", "required": False,
                                                              "allowed": ["dynata"]}]}
        s.quotas.append(Quota(code="gender", name="Geschlecht", cells=[QuotaCell(code="w", name="Frauen", target=20)]))
        db.session.commit()
        board = db.session.get(Board, 1)
        board.tiles = [*board.tiles, {"key": f"quota:{s.quotas[-1].id}"}]
        cap = next(q.id for q in s.quotas if q.code.startswith("links_"))
        db.session.commit()
    seen = _seen(app, _link(app, 1, "pv", "dynata"))
    # an ordinary quota stays; the one of "Höchstzahl je Link" names the links per person, on no shared link
    # (audit 2026-10-09b, V2)
    assert 'data-key="quota:' in seen and f'data-key="quota:{cap}"' not in seen
