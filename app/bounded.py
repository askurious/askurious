"""Calls to fixed outside services (the DFN timestamp service, Coscine) with a limit on the whole time and on the size of
the answer (audit 2026-10-11, AU7), like the web service (app/webservice.py, W1) and the own AI access.

requests' `timeout` holds for each single read only: a server, or anyone on the way of a plain http connection, that
sends a byte every few seconds kept the call open for hours, and an answer was read whole however large it was. Here
the answer is streamed, at most `max_bytes` of it are read, and a watchdog closes the connection when `seconds` have
passed since the call began. The hosts are fixed in the configuration, so this is about time and memory, not SSRF.

The watchdog reaches the connection in every phase, not only once the headers are in:
calls through requests go through
a session of their own whose connections hand their socket to the watchdog as soon as it is connected, so it shuts it
down during the TLS handshake, the headers and the body alike, and no single timeout passed in waits longer than the
whole call. Left open: connecting waits up to the whole limit for each address of a host, and the name lookup (DNS)
runs in the C library, which no other thread can interrupt; both end in TooSlow once they return after the limit.
"""
import socket
import threading
import time

import requests
import requests.api
from requests.adapters import HTTPAdapter
from urllib3 import poolmanager
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

_Thread = threading.Thread  # taken at import: a stand-in a test puts into threading later does not reach the watchdog

# requests' own functions, by what they send: a call through one of them goes through the guarded session below; a
# stand-in a test put in their place (monkeypatch of requests.get and the like) is called as it is
_SENDERS = {requests.api.request: None, requests.api.get: "GET", requests.api.post: "POST", requests.api.put: "PUT",
            requests.api.patch: "PATCH", requests.api.delete: "DELETE", requests.api.head: "HEAD",
            requests.api.options: "OPTIONS"}


class TooLarge(requests.RequestException):
    """The answer is larger than allowed."""


class TooSlow(requests.Timeout):
    """The whole call took longer than allowed."""


def _shutdown(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _shut(response):
    """Close the socket under a streamed response from another thread, so a read waiting on it returns at once."""
    raw = getattr(response, "raw", None)
    for sock in (getattr(getattr(raw, "_connection", None), "sock", None),
                 getattr(getattr(getattr(getattr(raw, "_fp", None), "fp", None), "raw", None), "_sock", None)):
        if sock is not None:
            _shutdown(sock)
            return


class _Guard:
    """The sockets of one call, so the watchdog can shut them in every phase: while TLS is negotiated, while the
    headers come and while the body is read. Each socket is kept as a duplicate taken
    right after its TCP connection stands: wrapping it in TLS detaches the original object, the duplicate still names
    the same connection, and shutting it down ends a read waiting on any of its handles. The duplicates are closed
    when the call ends; the watchdog only shuts them down and never closes a handle another thread may still use."""

    def __init__(self, deadline):
        self.deadline, self.over, self.lock, self.socks = deadline, False, threading.Lock(), []

    def rest(self):
        return self.deadline - time.monotonic()

    def add(self, sock):
        dup = sock.dup()
        with self.lock:
            self.socks.append(dup)
            over = self.over
        if over:
            _shutdown(dup)

    def shut(self):
        with self.lock:
            self.over = True
            socks = list(self.socks)
        for sock in socks:
            _shutdown(sock)

    def close(self):
        with self.lock:
            socks, self.socks = self.socks, []
        for sock in socks:
            try:
                sock.close()
            except OSError:
                pass


class _Guarded:
    """Mixed into urllib3's connections: the socket goes to the guard as soon as it is connected. Connecting itself
    waits at most the rest of the time for each address of the host (the connect timeout is capped); looking up the
    name (DNS) is not bounded here: it runs in the C library and cannot be interrupted from another thread."""

    _guard = None

    def _new_conn(self):
        if self._guard.over or self._guard.rest() <= 0:
            raise TooSlow("the whole call took too long")
        sock = super()._new_conn()
        try:
            self._guard.add(sock)
        except OSError:
            sock.close()
            raise
        if self._guard.over:
            sock.close()
            raise TooSlow("the whole call took too long")
        return sock


class _Adapter(HTTPAdapter):
    """An HTTPAdapter whose connection pools build guarded connections, directly and through an HTTP proxy."""

    def __init__(self, pools):
        self._pools = pools
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = self._pools

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        manager = super().proxy_manager_for(proxy, **proxy_kwargs)
        if manager.pool_classes_by_scheme is poolmanager.pool_classes_by_scheme:  # not SOCKS: it has its own pools
            manager.pool_classes_by_scheme = self._pools
        return manager


def _session(guard):
    """A requests session whose connections report their sockets to `guard`."""
    conn = type("GuardedHTTPConnection", (_Guarded, HTTPConnection), {"_guard": guard})
    sconn = type("GuardedHTTPSConnection", (_Guarded, HTTPSConnection), {"_guard": guard})
    pools = {"http": type("GuardedHTTPConnectionPool", (HTTPConnectionPool,), {"ConnectionCls": conn}),
             "https": type("GuardedHTTPSConnectionPool", (HTTPSConnectionPool,), {"ConnectionCls": sconn})}
    session = requests.Session()
    adapter = _Adapter(pools)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _capped(timeout, rest):
    """requests' timeout (seconds, (connect, read) or None) with each part at most `rest` seconds."""
    rest = max(rest, 0.001)
    if isinstance(timeout, tuple):
        connect, read = timeout
        return min(connect or rest, rest), min(read or rest, rest)
    return min(timeout or rest, rest)


def request(method, url, *, max_bytes, seconds, via=None, **kwargs):
    """requests.request with a limit on the whole call (`seconds`) and on the answer (`max_bytes`). Returns the
    response with its content read; raises TooSlow or TooLarge (both requests.RequestException) besides what requests
    raises. kwargs go to requests (timeout per read: at most `seconds`). via: the function that sends, called as
    via(url, stream=True, timeout=..., **kwargs) (e.g. requests.post); default requests.request with `method`."""
    deadline = time.monotonic() + seconds
    timeout = _capped(kwargs.pop("timeout", None), seconds)  # no single wait longer than the whole call
    over, done, held = threading.Event(), threading.Event(), {}
    guard = _Guard(deadline)
    sender = requests.request if via is None else via
    sends = next((m for f, m in _SENDERS.items() if f is sender), False)
    session = None
    if sends is not False:  # requests itself, not a stand-in: through a session whose sockets the watchdog can shut
        session = _session(guard)
        if sends is not None:
            method = sends
            if method == "HEAD":
                kwargs.setdefault("allow_redirects", False)  # as requests.head

    def watch():
        if not done.wait(seconds):  # the call did not end in time: shut its connection, in whatever phase it is
            over.set()
            guard.shut()
            if "r" in held:
                _shut(held["r"])

    watchdog = _Thread(target=watch, daemon=True, name="bounded-watchdog")
    watchdog.start()
    response = None
    try:
        if session is not None:
            response = session.request(method, url, stream=True, timeout=timeout, **kwargs)
        elif via is None:
            response = requests.request(method, url, stream=True, timeout=timeout, **kwargs)
        else:
            response = via(url, stream=True, timeout=timeout, **kwargs)
        held["r"] = response
        if over.is_set():
            raise TooSlow("the whole call took too long")
        if not hasattr(response, "iter_content"):  # a stand-in of the tests with its content at hand
            if len(getattr(response, "content", None) or b"") > max_bytes:
                raise TooLarge("answer too large")
            return response
        length = str((getattr(response, "headers", None) or {}).get("Content-Length", ""))
        if length.isdigit() and int(length) > max_bytes:
            raise TooLarge("answer too large")
        data = bytearray()
        try:
            for chunk in response.iter_content(65536):
                data += chunk
                if len(data) > max_bytes:
                    raise TooLarge("answer too large")
                if over.is_set() or time.monotonic() > deadline:
                    raise TooSlow("the whole call took too long")
        except (TooLarge, TooSlow):
            raise
        except (requests.RequestException, OSError) as e:
            if over.is_set() or time.monotonic() >= deadline:
                raise TooSlow("the whole call took too long") from e
            raise
        if over.is_set():
            raise TooSlow("the whole call took too long")  # closed by the watchdog: what came is not the whole answer
        response._content = bytes(data)
        response._content_consumed = True
        if not hasattr(type(response), "content"):  # a stand-in of the tests that streams: its content at hand too
            response.content = response._content
        return response
    except requests.RequestException as e:
        if (over.is_set() or time.monotonic() >= deadline) and not isinstance(e, (TooSlow, TooLarge)):
            raise TooSlow("the whole call took too long") from e
        raise
    finally:
        done.set()
        if response is not None and hasattr(response, "close"):
            response.close()
        if session is not None:
            session.close()
        guard.close()
