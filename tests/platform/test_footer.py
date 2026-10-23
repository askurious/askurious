"""The footer of the app: three links (imprint and privacy, accessibility, terms) and the status apart with a dot in
the colour of the whole, served by the status page (Ralf, 2026-10-17)."""
from app import status_node


def _footer(page):
    return page[page.index("<footer"):page.index("</footer>")]


def test_three_links_then_the_status(app, client):
    app.config["STATUS_URL"] = "https://status.example.org/"
    footer = _footer(client.get("/").data.decode())
    legal = footer[footer.index('class="footer-legal"'):]
    assert legal.index("/imprint") < legal.index("/accessibility") < legal.index("/terms/")
    assert 'href="/sources"' not in footer and "Lizenzen" not in footer
    assert '<a class="footer-status" href="https://status.example.org/"><img src="https://status.example.org/dot.svg"' in footer


def test_no_status_without_a_status_page(app, client):
    app.config["STATUS_URL"] = ""
    assert "footer-status" not in _footer(client.get("/").data.decode())


def test_dot_has_the_colour_of_the_whole(monkeypatch, tmp_path):
    monkeypatch.setenv("STATUS_CHECKS", "web=http:http://127.0.0.1:9/healthz")
    monkeypatch.setenv("STATUS_DB", str(tmp_path / "status.db"))
    monkeypatch.setattr(status_node.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    node = status_node.create()
    c = node.test_client()
    r = c.get("/dot.svg")
    assert r.status_code == 200 and r.mimetype == "image/svg+xml" and "max-age=60" in r.headers["Cache-Control"]
    assert 'fill="#9e9e9e"' in r.get_data(as_text=True)          # no measurement yet
    store = status_node.Store(str(tmp_path / "status.db"))
    store.add([(int(status_node.time.time()), "web", 0, 0, "unreachable")])
    assert 'fill="#9e9e9e"' in c.get("/dot.svg").get_data(as_text=True)  # kept 30 s
    monkeypatch.setattr(status_node.time, "time", lambda real=status_node.time.time: real() + 31)
    assert 'fill="#c62828"' in c.get("/dot.svg").get_data(as_text=True)
