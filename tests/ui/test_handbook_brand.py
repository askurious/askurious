"""The handbook carries the brand of its domain in its headings (UI audit 2026-10-12, U50, decided by Ralf): on
POLLtergeist "POLLtergeist: the concept"; under the main name it stays "Askurious"."""


def _setup(app):
    app.config.update(PUBLIC_BASE_URL="https://askurious.de", BRANDS="polltergeist.de=POLLtergeist", SECRET_KEY="x" * 32)
    return app.test_client()


def test_the_handbook_heading_takes_the_brand(app):
    c = _setup(app)
    page = c.get("/docs/", headers={"Host": "polltergeist.de", "Accept-Language": "en"}).get_data(as_text=True)
    assert "POLLtergeist: the concept</h1>" in page or "POLLtergeist: das Konzept</h1>" in page
    ai = c.get("/docs/ai", headers={"Host": "polltergeist.de"}).get_data(as_text=True)
    assert "in POLLtergeist</a>" in ai
    main = c.get("/docs/", headers={"Host": "askurious.de"}).get_data(as_text=True)
    assert "Askurious: das Konzept</h1>" in main or "Askurious: the concept</h1>" in main
    assert "POLLtergeist" not in main
