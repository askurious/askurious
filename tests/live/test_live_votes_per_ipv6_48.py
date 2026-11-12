"""Live votes per question from one IPv6 /48 are limited as well (app/live/devices.py, allow_sender): at most
LIVE_SENDER_WIDE_FACTOR times LIVE_SENDER_QUESTION_VOTES devices of all its networks /64 together. Before, each of the
65 536 networks /64 of a /48 had the whole limit, so a cheap rented /48 voted without end (business logic audit
2026-11-11)."""
from app import limits
from tests.live.test_live import send
from tests.live.test_live_limits import _participations, _phone, _running


def test_one_ipv6_48_is_limited_beyond_its_networks_64(app, client):
    code, sid, q = _running(app, client)
    app.config.update(LIVE_SENDER_QUESTION_VOTES=3, LIVE_SENDER_WIDE_FACTOR=2)
    codes = [send(_phone(app, {"REMOTE_ADDR": f"2001:db8:1:{n:x}::1"}), code, {f"i{q}": "1"}).status_code
             for n in range(10)]
    assert codes == [200] * 6 + [429] * 4                        # 3 × 2 from the whole /48, each /64 alone had 3
    for store in (limits._asked, limits._fallback, limits._load_fallback):
        store.clear()
    assert send(_phone(app, {"REMOTE_ADDR": "2001:db8:2::1"}), code, {f"i{q}": "1"}).status_code == 200  # another /48
    assert _participations(app, sid) == 7


def test_the_factor_0_counts_no_48(app, client):
    code, sid, q = _running(app, client)
    app.config.update(LIVE_SENDER_QUESTION_VOTES=3, LIVE_SENDER_WIDE_FACTOR=0)
    codes = [send(_phone(app, {"REMOTE_ADDR": f"2001:db8:1:{n:x}::1"}), code, {f"i{q}": "1"}).status_code
             for n in range(8)]
    assert codes == [200] * 8
