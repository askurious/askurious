"""The public status page names why a check failed with a fixed word, not with the name of a Python exception
(security and code audit 2026-10-13, TN-36)."""
import socket
import ssl

import requests

from app import status_node


def test_failures_are_fixed_words(monkeypatch):
    for error, word in ((requests.ConnectTimeout("x"), "timeout"), (socket.timeout("x"), "timeout"),
                        (requests.ConnectionError("x"), "unreachable"), (ConnectionRefusedError(), "unreachable"),
                        (socket.gaierror("x"), "unreachable"), (ssl.SSLError("x"), "tls"),
                        (FileNotFoundError("x"), "missing"), (ValueError("secret detail"), "error")):
        def boom(*a, error=error, **k):
            raise error
        monkeypatch.setattr(status_node.requests, "get", boom)
        assert status_node.check("http", "http://app:8000/healthz")[2] == word, error
    assert status_node.check("age", "/nowhere/at/all:24")[2] == "missing"
