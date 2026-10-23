"""Calls to fixed outside services (the DFN timestamp service, Coscine) with a limit on the whole time and on the size of
the answer (audit 2026-10-11, AU7), like the web service (app/webservice.py, W1) and the own AI access.

requests' `timeout` holds for each single read only: a server, or anyone on the way of a plain http connection, that
sends a byte every few seconds kept the call open for hours, and an answer was read whole however large it was. Here
the answer is streamed, at most `max_bytes` of it are read, and a watchdog closes the connection when `seconds` have
passed since the call began. The hosts are fixed in the configuration, so this is about time and memory, not SSRF.
"""
import socket
import threading
import time

import requests

_Thread = threading.Thread  # taken at import: a stand-in a test puts into threading later does not reach the watchdog


class TooLarge(requests.RequestException):
    """The answer is larger than allowed."""


class TooSlow(requests.Timeout):
    """The whole call took longer than allowed."""


def _shut(response):
    """Close the socket under a streamed response from another thread, so a read waiting on it returns at once."""
    raw = getattr(response, "raw", None)
    for sock in (getattr(getattr(raw, "_connection", None), "sock", None),
                 getattr(getattr(getattr(getattr(raw, "_fp", None), "fp", None), "raw", None), "_sock", None)):
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return


def request(method, url, *, max_bytes, seconds, via=None, **kwargs):
    """requests.request with a limit on the whole call (`seconds`) and on the answer (`max_bytes`). Returns the
    response with its content read; raises TooSlow or TooLarge (both requests.RequestException) besides what requests
    raises. kwargs go to requests (timeout per read: at most `seconds`). via: the function that sends, called as
    via(url, stream=True, timeout=..., **kwargs) (e.g. requests.post); default requests.request with `method`."""
    deadline = time.monotonic() + seconds
    timeout = kwargs.pop("timeout", None) or seconds
    over, done, held = threading.Event(), threading.Event(), {}

    def watch():
        if not done.wait(seconds):  # the call did not end in time: close its connection
            over.set()
            if "r" in held:
                _shut(held["r"])

    watchdog = _Thread(target=watch, daemon=True, name="bounded-watchdog")
    watchdog.start()
    response = None
    try:
        if via is None:
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
            if over.is_set():
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
        if over.is_set() and not isinstance(e, (TooSlow, TooLarge)):
            raise TooSlow("the whole call took too long") from e
        raise
    finally:
        done.set()
        if response is not None and hasattr(response, "close"):
            response.close()
