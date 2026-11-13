"""The KI-Chat of the participants keeps below the threads of a participants' worker (app/ai_chat.py): at most
PARALLEL_DEFAULT calls at once per process, well below gunicorn's --threads of the participants' containers, and the
whole call of the model within CALL_TIMEOUT, also in the transport's watchdog. Before, four calls of up to 90 s could
hold all four threads of a worker, and a slow model stopped the participants' pages (business logic audit
2026-11-11)."""
import re

import pytest

from app import ai_access, ai_chat
from tests.platform.test_audit8_b_deploy import _read


def _threads(service):
    compose = _read("deploy", "docker", "compose.yaml")
    block = compose[compose.index(f"\n  {service}:"):]
    command = re.search(r'command: \[.*?"--threads", "(\d+)"', block, re.S)
    return int(command.group(1))


def test_the_limit_stays_well_below_the_threads_of_a_participants_worker():
    threads = min(_threads("participant-a"), _threads("brand-participant"))
    assert ai_chat.PARALLEL_DEFAULT * 2 <= threads, (ai_chat.PARALLEL_DEFAULT, threads)


def test_a_call_of_the_chat_ends_within_its_own_time(app, monkeypatch):
    seen = {}

    class _Transport(ai_access.httpx.BaseTransport):
        def __init__(self, seconds, user_id=None, allowed=frozenset()):
            seen["watchdog"] = seconds

    monkeypatch.setattr(ai_access, "GuardedTransport", _Transport)
    with app.test_request_context():
        c = ai_access.client(None, base_url="https://api.openai.com/v1", key="k", seconds=ai_chat.CALL_TIMEOUT)
    assert seen["watchdog"] == ai_chat.CALL_TIMEOUT <= 30 and c.timeout == ai_chat.CALL_TIMEOUT


def test_a_second_call_in_the_same_process_is_busy_at_once(app):
    with app.app_context():
        app.config.pop("AI_CHAT_PARALLEL", None)
        with ai_chat._Slot(1):
            with pytest.raises(ai_chat.Busy):
                with ai_chat._Slot(2):
                    pass
        with ai_chat._Slot(3):   # free again
            pass
