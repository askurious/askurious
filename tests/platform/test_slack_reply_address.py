"""Replies to a click in Slack go only to Slack over https, and no redirect is followed (security and code audit
2026-10-13, IN-4)."""
import urllib.request

from app import notify, slack_actions


def test_reply_only_to_hooks_slack_com_over_https(app, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "post", lambda url, payload: sent.append(url))
    with app.app_context():
        for url in ("http://hooks.slack.com/actions/x", "https://hooks.slack.com:8443/actions/x",
                    "ftp://hooks.slack.com/x", "https://hooks.slack.com.evil.example/x", "https://[::1/x"):
            slack_actions._answer(url, {"text": "x"})
        assert sent == []
        slack_actions._answer("https://hooks.slack.com/actions/T1/2/abc", {"text": "x"})
    assert sent == ["https://hooks.slack.com/actions/T1/2/abc"]


def test_a_redirect_is_not_followed():
    handler = next(h for h in notify._opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
    request = urllib.request.Request("https://hooks.slack.com/x", data=b"{}", method="POST")
    assert handler.redirect_request(request, None, 302, "Found", {}, "http://intern.example/") is None
