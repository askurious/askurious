
def test_search_and_sort_on_my_surveys(app, client):
    # search and sort in the browser; the cards carry what it needs (Ralf, 2026-10-05)
    for title in ("Alpha", "Beta"):
        client.post("/surveys/new", data={"title": title})
    html = client.get("/dashboard/").data.decode()
    assert "data-dash-find hidden" in html and "data-dash-search" in html and 'value="title">Titel A–Z' in html
    assert 'data-title="alpha"' in html and 'data-done="0"' in html and "data-created=" in html
    assert "data-dash-none hidden" in html
