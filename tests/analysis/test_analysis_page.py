"""The page of one analysis below "Ergebnisse" (restructuring of the results part, stage 1): a question of the
conjoint family has its analysis on a page of its own, the results page keeps a short summary and lists the analyses."""
from html.parser import HTMLParser

from tests.analysis.test_cbc_results import setup
from tests.analysis.test_conjoint_hb import HB_FAST, simulate_segments

FETCH = {"X-Requested-With": "fetch"}
PAGE = "/surveys/1/results/analysis/2"


class Folds(HTMLParser):
    """The deepest nesting of folds (<details>) in a page. The learning aids "Warum?" and "Wie gerechnet?"
    (_learn.html, class learn-why) are explanations at a place, not folds of content: they do not count."""

    def __init__(self):
        super().__init__()
        self.open, self.deepest = [], 0

    def handle_starttag(self, tag, attrs):
        if tag == "details":
            self.open.append("learn-why" not in (dict(attrs).get("class") or ""))
            self.deepest = max(self.deepest, sum(self.open))

    def handle_endtag(self, tag):
        if tag == "details":
            self.open.pop()


def folds(html):
    parser = Folds()
    parser.feed(html)
    assert not parser.open
    return parser.deepest


def prepared(app, client):
    setup(client, price_attr=True, none=True, tasks=12, versions=6)
    app.config.update(HB_FAST)
    simulate_segments(app, 80, none_asc=-0.5)


def test_results_page_keeps_summary_and_lists_the_analysis(app, client, survey):
    prepared(app, client)
    html = client.get("/surveys/1/results").data.decode()
    # the section "Analysen": one line with the state and the way to the page
    assert 'id="analyses"' in html and f'href="{PAGE}" data-analysis-link="2"' in html
    assert "noch nicht geschätzt" in html and ">Öffnen</a>" in html
    # at the question: people and tasks, the importance as bars, the button; nothing of the analysis itself
    assert "80 Personen, 960 beantwortete Aufgaben" in html and "Wichtigkeit der Merkmale" in html
    assert "aggregiertes Logit-Modell" in html and "Zur Conjoint-Auswertung" in html
    for gone in ('class="hb-part"', "sim-fold", "lc-part", "simulator.js", "hb.js", "segments.js", "Zählwerte: Wahlanteile"):
        assert gone not in html, gone


def test_page_of_the_analysis_has_flat_sections(app, client, survey):
    prepared(app, client)
    r = client.get(PAGE)
    assert r.status_code == 200
    html = r.data.decode()
    assert "<h1>Conjoint: " in html and 'class="back-link" href="/surveys/1/results#res-cbc"' in html
    assert "80 Personen, 960 beantwortete Aufgaben" in html and "Datenpool und Ansicht ändern" in html
    heads = ["<h2>Wichtigkeit und Teilnutzen</h2>", 'class="sim-title"', "<h2>Segmente</h2>", "<h2>Modellgüte</h2>",
             "<h2>Details</h2>", "<h2>Herunterladen</h2>"]
    places = [html.index(h) for h in heads]
    assert places == sorted(places)  # in this order
    # the parts fetch themselves from this page, so an estimation touches this page only
    for attr in ("data-hb-url", "data-lc-url", "data-sim-url"):
        assert f'{attr}="{PAGE}"' in html, attr
    assert 'id="hb-2"' in html and 'id="lc-2"' in html and 'id="sim-2"' in html and 'id="quality-2"' in html
    assert "Langformat (CSV)" in html and "/surveys/1/export/conjoint/2.csv" in html
    assert folds(html) <= 1  # folds at most one level deep
    assert html.count("<h1") == 1


def test_estimation_stays_on_the_page(app, client, survey):
    prepared(app, client)
    r = client.post("/surveys/1/results/hb/2", data={"source": "real", "speeder": "50"})
    assert r.status_code == 302 and r.location.endswith("#hb-2") and PAGE + "?" in r.location
    part = client.get(PAGE + "?source=real&drop=1&hb_part=2", headers=FETCH)
    assert part.status_code == 200
    block = part.data.decode()
    assert 'class="hb-part" data-hb-current' in block and f'data-hb-url="{PAGE}"' in block and "<h1" not in block
    sim = client.get(PAGE + "?source=real&drop=1&sim_part=2&sim=2", headers=FETCH).data.decode()
    assert 'class="sim-fold' in sim and f'data-sim-url="{PAGE}"' in sim
    started = client.post("/surveys/1/results/lc/2", data={"source": "real", "drop": "1"})
    assert started.status_code == 302 and started.location.endswith("#lc-2") and PAGE in started.location
    seg = client.get(PAGE + "?source=real&drop=1&lc_part=2", headers=FETCH).data.decode()
    assert 'class="lc-part"' in seg and f'data-lc-url="{PAGE}"' in seg
    listed = client.get("/surveys/1/results?source=real&drop=1").data.decode()
    assert "HB aktuell" in listed and PAGE + "?source=real&amp;drop=1" in listed  # the pool of the address goes along


def test_only_questions_of_the_conjoint_family(app, client, survey):
    prepared(app, client)
    assert client.get("/surveys/1/results/analysis/1").status_code == 404    # the consent
    assert client.get("/surveys/1/results/analysis/999").status_code == 404
