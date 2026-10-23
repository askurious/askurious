"""The monitoring feed of plain counts (app/monitoring_feed.py), the block under Administration > Server where site admins make
its token, and the state of the services as JSON (app/status_node.py, /status/<token>.json)."""
import re
from datetime import datetime, timedelta, timezone

from app import monitoring_feed, status_node
from app.extensions import db
from app.models import Response, SiteSetting, Survey, utcnow
from app.timeutil import LOCAL_TZ


def _survey(app, opened_days_ago=40):
    with app.app_context():
        s = Survey(title="Feed", status=Survey.RUNNING, opened_at=utcnow() - timedelta(days=opened_days_ago))
        db.session.add(s)
        db.session.commit()
        return s.id


def _finished(app, survey_id, n, days_ago, hour, *, source="real", preview=False):
    """n participations finished days_ago at hour o'clock local time; returns the weekday (0 Monday) of that day."""
    day = (datetime.now(LOCAL_TZ) - timedelta(days=days_ago)).replace(hour=hour, minute=30, second=0, microsecond=0)
    done = day.astimezone(timezone.utc).replace(tzinfo=None)
    with app.app_context():
        for _ in range(n):
            db.session.add(Response(survey_id=survey_id, source=source, is_preview=preview, status=Response.COMPLETED,
                                    started_at=done - timedelta(minutes=8), completed_at=done, last_active_at=done))
        db.session.commit()
    return day.weekday()


def _make_token(app):
    with app.app_context():
        return monitoring_feed.make_token(None)


def _plain_client(app, orcid="0000-0000-0000-0042", name="Normal"):
    """A signed-in account that is not a site admin."""
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    c.post("/terms/", data={"accept": "1"})
    return c


# ---- the answer --------------------------------------------------------------------------------------------------
def test_payload_fills_up_in_front_and_keeps_the_last_day_last():
    body = monitoring_feed.payload(days=[3, 6], dates=["2026-10-20", "2026-10-21"], opened=[1, 0], hours=[1] * 30, quarters=[2] * 10,
                                   active=2, now="2026-10-21T16:35:12")
    assert body["v"] == 5 and body["active"] == 2 and body["now"] == "2026-10-21T16:35:12"
    assert len(body["days"]) == 31 and len(body["hours"]) == 168 and body["hours"][:138] == [0] * 138
    assert body["days"][-1] == {"date": "2026-10-21", "done": 6, "opened": 0}
    assert body["days"][-2] == {"date": "2026-10-20", "done": 3, "opened": 1}
    assert body["days"][0] == {"date": "", "done": 0, "opened": 0}              # no day before the first one known
    assert len(str(body).encode()) < 4096                                     # small: it is polled every few minutes


def test_feed_counts_real_participations_only(app, client):
    sid = _survey(app)
    token = _make_token(app)
    _finished(app, sid, 30, 2, 10)                                        # real: two days ago, 10 h
    _finished(app, sid, 30, 2, 10, source="dummy")                        # test data: not counted
    _finished(app, sid, 60, 5, 15, preview=True)                          # preview: not counted
    r = client.get(f"/api/monitoring/{token}")
    assert r.status_code == 200 and r.mimetype == "application/json"
    assert r.headers["Cache-Control"] == "no-store" and "noindex" in r.headers["X-Robots-Tag"]
    body = r.get_json()
    assert [d["done"] for d in body["days"]] == [0] * 28 + [30, 0, 0]       # two days ago is the third from last
    assert len(body["hours"]) == 168 and sum(body["hours"]) == 30 and sum(body["hours"][-24:]) == 0   # not in the last day
    ago = round((datetime.now(LOCAL_TZ).replace(minute=0, second=0, microsecond=0)
                 - (datetime.now(LOCAL_TZ) - timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)).total_seconds() / 3600)
    assert abs((167 - body["hours"].index(30)) - ago) <= 1                    # at its place, counted back from the hour now
    assert isinstance(body["active"], int) and body["now"][:10] == datetime.now(LOCAL_TZ).date().isoformat()


def test_surveys_opened_per_day(app, client):
    _survey(app, opened_days_ago=2)
    _survey(app, opened_days_ago=2)
    token = _make_token(app)
    assert [d["opened"] for d in client.get(f"/api/monitoring/{token}").get_json()["days"]] == [0] * 28 + [2, 0, 0]


def test_the_answer_is_kept_for_a_minute(app, client):
    sid = _survey(app)
    token = _make_token(app)
    first = client.get(f"/api/monitoring/{token}").get_json()
    _finished(app, sid, 30, 1, 10)
    assert client.get(f"/api/monitoring/{token}").get_json() == first         # computed once a minute, whoever asks


def test_feed_is_on_the_researchers_address():
    from app.hosts import BLUEPRINT_SIDES, RESEARCHER

    assert BLUEPRINT_SIDES["monitoring_feed"] == RESEARCHER


def test_quarters_count_the_last_day_in_fifteen_minutes_with_the_running_quarter_last(app, client):
    sid = _survey(app)
    token = _make_token(app)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    quarter_now = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    with app.app_context():
        for done in (quarter_now + timedelta(seconds=1), quarter_now - timedelta(minutes=14), quarter_now - timedelta(minutes=14),
                     quarter_now - timedelta(hours=23, minutes=45), quarter_now - timedelta(hours=24, minutes=1)):
            db.session.add(Response(survey_id=sid, source="real", is_preview=False, status=Response.COMPLETED,
                                    started_at=done - timedelta(minutes=8), completed_at=done, last_active_at=done))
        db.session.commit()
    q = client.get(f"/api/monitoring/{token}").get_json()["quarters"]
    assert len(q) == 96 and q[-1] == 1 and q[-2] == 2 and q[0] == 1 and sum(q) == 4   # the one before the first quarter is out


# ---- the token ---------------------------------------------------------------------------------------------------
def _page(response):
    """The 404 page without its CSRF token, which carries the second it was made (two requests can straddle one)."""
    return re.sub(rb'name="csrf-token" content="[^"]*"', b'name="csrf-token"', response.data)


def test_without_a_token_there_is_no_feed(app, client):
    assert client.get("/api/monitoring/").status_code == 404
    assert client.get("/api/monitoring/" + "x" * 24).status_code == 404
    assert _page(client.get("/api/monitoring/")) == _page(client.get("/api/monitoring/" + "x" * 24))


def test_a_wrong_token_is_a_plain_404(app, client):
    _make_token(app)
    wrong = client.get("/api/monitoring/" + "x" * 32)
    assert wrong.status_code == 404
    assert _page(wrong) == _page(client.get("/api/monitoring/anything-else-here-too"))  # the same page as for any address


def test_the_token_is_kept_locked_not_in_clear(app):
    token = _make_token(app)
    with app.app_context():
        row = db.session.get(SiteSetting, monitoring_feed.SETTING)
        assert row.value and token not in row.value and row.value.startswith("gAAAA")   # a Fernet token
        assert monitoring_feed.token() == token and len(token) >= monitoring_feed.MIN_TOKEN


def test_a_new_token_replaces_the_old_one(app, client):
    old = _make_token(app)
    assert client.get(f"/api/monitoring/{old}").status_code == 200
    new = _make_token(app)
    assert new != old
    assert client.get(f"/api/monitoring/{old}").status_code == 404
    assert client.get(f"/api/monitoring/{new}").status_code == 200


def test_a_removed_token_closes_the_feed(app, client):
    token = _make_token(app)
    with app.app_context():
        monitoring_feed.remove_token(None)
        assert monitoring_feed.token() == "" and monitoring_feed.feed_url() == ""
    assert client.get(f"/api/monitoring/{token}").status_code == 404


# ---- under Administration > Server ---------------------------------------------------------------------------------
def test_the_server_page_offers_a_token_and_then_the_address(app, client):
    page = client.get("/admin/database").get_data(as_text=True)
    assert 'id="monitoring"' in page and 'value="monitoring_make"' in page and "Token erzeugen" in page
    assert "/api/monitoring/" not in page                                  # no token yet: no address either
    r = client.post("/admin/database", data={"action": "monitoring_make"})
    assert r.status_code == 302 and r.headers["Location"].endswith("#monitoring")
    page = client.get("/admin/database").get_data(as_text=True)
    with app.app_context():
        token = monitoring_feed.token()
    assert f"/api/monitoring/{token}" in page                              # the address with the token, for the admin
    assert "JSON mit Token öffnen" not in page and "Adresse mit Token" not in page     # no icon nobody understands
    assert "Monitoring Feed" in page and "Statistik Teilnahmen und Befragungen" in page and "Zustand der Dienste" in page
    assert 'value="monitoring_remove"' in page and f'data-copy-tsv="http' in page
    assert client.get(f"/api/monitoring/{token}").status_code == 200       # and it works


def test_a_site_admin_can_change_and_remove_the_token_there(app, client):
    client.post("/admin/database", data={"action": "monitoring_make"})
    with app.app_context():
        first = monitoring_feed.token()
        row = db.session.get(SiteSetting, monitoring_feed.SETTING)
        assert row.changed_by_id is not None                                # who made it is kept with the setting
    client.post("/admin/database", data={"action": "monitoring_make"})
    with app.app_context():
        second = monitoring_feed.token()
    assert first and second and first != second
    client.post("/admin/database", data={"action": "monitoring_remove"})
    assert client.get(f"/api/monitoring/{second}").status_code == 404
    assert "/api/monitoring/" not in client.get("/admin/database").get_data(as_text=True)


def test_only_site_admins_reach_the_block_and_the_token(app):
    token = _make_token(app)
    plain = _plain_client(app)
    assert plain.get("/admin/database").status_code == 403
    assert plain.post("/admin/database", data={"action": "monitoring_make"}).status_code == 403
    assert plain.post("/admin/database", data={"action": "monitoring_remove"}).status_code == 403
    with app.app_context():
        assert monitoring_feed.token() == token                             # nothing changed
    assert app.test_client().get("/admin/database").status_code in (302, 401)       # not signed in: to the sign-in


def test_the_server_page_links_the_status_json_when_its_token_is_set(app, client):
    app.config.update(STATUS_URL="https://status.example.org/", STATUS_JSON_TOKEN="s" * 24)
    page = client.get("/admin/database").get_data(as_text=True)
    assert "https://status.example.org/status/" + "s" * 24 + ".json" in page
    app.config.update(STATUS_JSON_TOKEN="short")
    page = client.get("/admin/database").get_data(as_text=True)
    assert "/status/" + "short" not in page and "STATUS_JSON_TOKEN" in page      # too short: no link, a hint instead


def test_one_qr_code_carries_both_addresses_for_the_android_app(app, client):
    import json

    client.post("/admin/database", data={"action": "monitoring_make"})
    page = client.get("/admin/database").get_data(as_text=True)
    assert "feed-qr" not in page                                           # the feed has a token, the status page has none yet
    app.config.update(STATUS_URL="https://status.example.org/", STATUS_JSON_TOKEN="s" * 24)
    page = client.get("/admin/database").get_data(as_text=True)
    assert 'class="feed-qr"' in page and "<svg" in page.split('class="feed-qr"')[1]
    with app.test_request_context():                                       # the address is built outside a request, with its host
        feed, status = monitoring_feed.feed_url(), monitoring_feed.status_json_url()
        assert monitoring_feed.setup_qr()
    text = json.loads(monitoring_feed.setup_text(feed, status))
    assert text == {"askurious": "askurious-dashboard-feeds", "v": 1, "feed": feed, "status": status}
    assert status == "https://status.example.org/status/" + "s" * 24 + ".json" and "/api/monitoring/" in feed
    client.post("/admin/database", data={"action": "monitoring_remove"})
    assert "feed-qr" not in client.get("/admin/database").get_data(as_text=True)    # a token removed: no code either


# ---- the status page ---------------------------------------------------------------------------------------------
def _node(monkeypatch, tmp_path, token="s" * 24):
    monkeypatch.setenv("STATUS_CHECKS", "web=http:http://127.0.0.1:9/healthz,compute=http:http://127.0.0.1:9/healthz")
    monkeypatch.setenv("STATUS_DB", str(tmp_path / "status.db"))
    monkeypatch.setenv("STATUS_JSON_TOKEN", token)
    monkeypatch.setattr(status_node.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    return status_node.create(), status_node.Store(str(tmp_path / "status.db"))


def test_status_json_needs_the_token(monkeypatch, tmp_path):
    node, _ = _node(monkeypatch, tmp_path)
    c = node.test_client()
    assert c.get("/status/" + "x" * 24 + ".json").status_code == 404          # a wrong token
    assert c.get("/status/.json").status_code == 404 and c.get("/status.json").status_code == 404
    right = c.get("/status/" + "s" * 24 + ".json")
    assert right.status_code == 200 and right.is_json
    assert right.headers["Cache-Control"] == "no-store" and "noindex" in right.headers["X-Robots-Tag"]


def test_status_json_is_off_without_a_long_token(monkeypatch, tmp_path):
    for token in ("", "short"):
        node, _ = _node(monkeypatch, tmp_path, token=token)
        assert node.test_client().get(f"/status/{token}.json").status_code == 404


def test_status_json_lists_every_service_and_the_state_of_the_whole(monkeypatch, tmp_path):
    node, store = _node(monkeypatch, tmp_path)
    c = node.test_client()
    url = "/status/" + "s" * 24 + ".json"
    assert c.get(url).get_json() == {"state": 1, "services": [{"key": "web", "state": "none"}, {"key": "compute", "state": "none"}]}
    now = int(status_node.time.time())
    store.add([(now, "web", 1, 5, "ok"), (now, "compute", 0, 0, "unreachable")])
    assert c.get(url).get_json() == {"state": 2, "services": [{"key": "web", "state": "ok"}, {"key": "compute", "state": "down"}]}
    store.add([(now + 40, "compute", 1, 4, "ok")])
    monkeypatch.setattr(status_node.time, "time", lambda real=status_node.time.time: real() + 70)
    assert c.get(url).get_json()["state"] == 0
