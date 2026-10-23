"""The weather demo of an external section (app/demo_webapp.py, Philipp 2026-10-08): city in, temperature from
Open-Meteo, both back to the survey through the return link; never a redirect to another address."""
from urllib.parse import parse_qs, urlsplit

import pytest

from app import demo_webapp

T = "A" * 40


class _Answer:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.fixture
def weather(monkeypatch):
    calls = []

    def get(url, params=None, timeout=None):
        calls.append(url)
        if url == demo_webapp.GEOCODING:
            if params["name"] == "Nirgendwo":
                return _Answer({})
            return _Answer({"results": [{"name": "Aachen", "country": "Deutschland", "latitude": 50.8, "longitude": 6.1}]})
        return _Answer({"current": {"temperature_2m": 12.34}})

    demo_webapp._cache.clear()
    monkeypatch.setattr(demo_webapp.requests, "get", get)
    return calls


def test_city_in_temperature_back_to_the_survey(app, client, weather):
    back = app.config["PUBLIC_BASE_URL"] + f"/s/abc123/back?t={T}"
    html = client.get("/demo/weather", query_string={"lang": "de", "return": back, "stadt": "Aachen"}).data.decode()
    assert "In Aachen, Deutschland sind es gerade 12,3 °C." in html and "Zurück zur Befragung" in html
    link = html.split('class="button" href="')[1].split('"')[0].replace("&amp;", "&")
    query = parse_qs(urlsplit(link).query)
    assert link.startswith(back) and query["city"] == ["Aachen, Deutschland"] and query["temperature"] == ["12.3"]
    client.get("/demo/weather", query_string={"lang": "de", "return": back, "stadt": "aachen"})
    assert len(weather) == 2                                                     # the second time from the cache
    html = client.get("/demo/weather", query_string={"lang": "en", "stadt": "Nirgendwo"}).data.decode()
    assert "This city was not found" in html and "without a return link" in html and "Back to the survey" not in html
    html = client.get("/demo/weather", query_string={"lang": "fr"}).data.decode()
    assert "Démo météo" in html and 'lang="fr"' in html


@pytest.mark.parametrize("bad", ["https://evil.example/s/abc/back?t=" + T, "javascript:alert(1)",
                                 "/s/abc/back?t=" + T, "{base}/s/abc/anders?t=" + T, "{base}/s/abc/back?t=x&y=1"])
def test_only_own_return_links(app, client, weather, bad):
    bad = bad.format(base=app.config["PUBLIC_BASE_URL"])
    html = client.get("/demo/weather", query_string={"return": bad, "stadt": "Aachen"}).data.decode()
    assert "Zurück zur Befragung" not in html and "ohne Rückkehrlink" in html


def test_service_down_still_lets_the_person_go_back(app, client, monkeypatch):
    def down(*a, **k):
        raise demo_webapp.requests.ConnectionError("no")

    demo_webapp._cache.clear()
    monkeypatch.setattr(demo_webapp.requests, "get", down)
    back = app.config["PUBLIC_BASE_URL"] + f"/s/abc123/back?t={T}"
    html = client.get("/demo/weather", query_string={"return": back, "stadt": "Aachen"}).data.decode()
    assert "nicht erreichbar" in html and "Zurück zur Befragung" in html and "temperature=&amp;" not in html
