"""The whole-call limit of app/bounded.py holds in every phase of an outgoing call, against a loopback server only:
a server that sends its headers, its TLS handshake or its body a byte at a time is cut off at the limit, and a timeout
passed in (Zenodo: 120 s) is capped by it."""
import socket
import threading
import time

import pytest
import requests

from app import bounded

LIMIT = 0.5
TOLERANCE = 1.5  # generous for a loaded machine; before the fix the slow servers below held the call for ~10 s


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def server():
    """serve(handler, connections=1) -> port; handler(conn, stop) runs for each accepted connection."""
    stop, threads, listeners = threading.Event(), [], []

    def serve(handler, connections=1):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        listener.settimeout(10)
        listeners.append(listener)

        def run():
            try:
                for _ in range(connections):
                    conn, _ = listener.accept()
                    with conn:
                        conn.settimeout(10)
                        try:
                            handler(conn, stop)
                        except OSError:
                            pass  # the client shut the connection: what the test wants
            except OSError:
                pass

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        threads.append(thread)
        return listener.getsockname()[1]

    yield serve
    stop.set()
    for listener in listeners:
        listener.close()
    for thread in threads:
        thread.join(10)


def _trickle(conn, stop, head):
    """Sends head, then a byte every 50 ms for about ten seconds: each read is quick, the whole never ends in time."""
    conn.sendall(head)
    for _ in range(200):
        if stop.wait(0.05):
            return
        conn.sendall(b"a")


def _call(port, scheme="http", via=requests.get, **kwargs):
    began = time.monotonic()
    with pytest.raises(bounded.TooSlow):
        bounded.request("GET", f"{scheme}://127.0.0.1:{port}/", max_bytes=4096, seconds=LIMIT, via=via, **kwargs)
    return time.monotonic() - began


def test_slow_headers_end_at_the_limit_even_with_a_long_timeout(server):
    def handler(conn, stop):
        conn.recv(4096)
        _trickle(conn, stop, b"HTTP/1.1 200 OK\r\nX-Delay: ")

    assert _call(server(handler), timeout=120) < LIMIT + TOLERANCE  # as zenodo.py passes it
    assert _call(server(handler), via=None, timeout=(120, 120)) < LIMIT + TOLERANCE


def test_a_slow_tls_handshake_ends_at_the_limit(server):
    def handler(conn, stop):
        conn.recv(4096)  # the ClientHello
        _trickle(conn, stop, b"\x16\x03\x03\x40\x00")  # a handshake record that never completes in time

    assert _call(server(handler), scheme="https") < LIMIT + TOLERANCE


def test_a_slow_body_ends_at_the_limit(server):
    def handler(conn, stop):
        conn.recv(4096)
        _trickle(conn, stop, b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n")

    assert _call(server(handler)) < LIMIT + TOLERANCE


def test_normal_answers_and_redirects_still_arrive_whole(server):
    def handler(conn, stop):
        request = conn.recv(4096)
        if b"GET /next" in request:
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\ndone")
        else:
            conn.sendall(b"HTTP/1.1 302 Found\r\nLocation: /next\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

    port = server(handler, connections=2)
    r = bounded.request("GET", f"http://127.0.0.1:{port}/", max_bytes=4096, seconds=5, via=requests.get)
    assert (r.status_code, r.content) == (200, b"done")


def test_timeouts_are_capped_by_the_whole_limit():
    assert bounded._capped(120, 20) == 20
    assert bounded._capped(None, 20) == 20
    assert bounded._capped(3, 20) == 3
    assert bounded._capped((5, 120), 20) == (5, 20)
    assert bounded._capped((None, 4), 20) == (20, 4)
