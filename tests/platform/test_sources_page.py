"""The page /sources (Lizenzen und Datenquellen) and the files behind it (copyright audit 2026-10-16, U8, U15,
U21, U41; licence audit 2026-10-23, A1): open without an account, the derived ODbL data described (no
downloads), the lists in step with the lock files."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_sources_page_is_open_and_describes_the_derived_data_without_downloads(client):
    """Licence audit 2026-10-23, A1 (Ralf): the derived data files are no downloads any more; ODbL 4.6 is met by the
    description of how they came about and a copy on request. README and the library list come as plain text."""
    html = client.get("/sources").data.decode()
    assert "Lizenzen und Datenquellen" in html and ">BKG</a> (2026)" in html and "datenquellen_vg_nuts.pdf" in html and "OpenJPEG" in html
    assert "Abgeleitete Postleitzahldaten" in html and "auf Anfrage" in html and "Herunterladen" not in html
    for name in ("README.md", "THIRD_PARTY_LICENSES.txt"):
        assert f"/sources/{name}" in html
        r = client.get(f"/sources/{name}")
        assert r.status_code == 200 and r.mimetype == "text/plain" and "attachment" not in r.headers.get("Content-Disposition", "")
    assert "build_plz_areas.py" in client.get("/sources/README.md").data.decode()
    for name in ("plz_areas.zip", "plz_states_de.tsv", "states_de.json.gz", "plz_de.tsv", "population_de.json.gz",
                 "geo_ip.bin.gz", "../config.py"):
        assert f"/sources/{name}" not in html and client.get(f"/sources/{name}").status_code == 404, name


def test_sources_page_serves_the_licence_notice_and_links_the_source_code(client, app):
    """EUPL Art. 5: the licence texts, NOTICE (the parts not under the EUPL) and where the source code is, on the page
    of every installation; the Docker image copies the three files (deploy/docker/Dockerfile)."""
    html = client.get("/sources").data.decode()
    for name, marker in (("LICENSE", "EUROPEAN UNION PUBLIC LICENCE"), ("LICENSE.de.txt", "EUPL"),
                         ("NOTICE", "Parts NOT licensed under the EUPL-1.2")):
        assert f"/sources/{name}" in html
        r = client.get(f"/sources/{name}")
        assert r.status_code == 200 and r.mimetype == "text/plain" and marker in r.data.decode()
    assert "Datei NOTICE" in html and "Anfang der Datei LICENSE" not in html
    assert 'href="https://github.com/askurious/askurious"' in html
    app.config["SOURCE_CODE_URL"] = "https://example.org/fork"
    assert 'href="https://example.org/fork"' in client.get("/sources").data.decode()
    dockerfile = (ROOT / "deploy/docker/Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^COPY .*LICENSE LICENSE\.de\.txt NOTICE", dockerfile, re.M)
    whisper = (ROOT / "deploy/docker/whisper/Dockerfile").read_text(encoding="utf-8")
    assert "COPY licenses ./licenses" in whisper
    for name in ("ctranslate2-4.8.3", "flatbuffers-25.12.19", "tokenizers-0.22.2", "primepy-1.3"):
        assert (ROOT / f"deploy/docker/whisper/licenses/{name}.LICENSE.txt").is_file(), name


def test_lgpl_test_schemas_come_with_the_gnu_texts():
    """The DDI Codebook 2.5 schemas (LGPL 3.0 or later) are passed on with the code; the LGPL and the GPL it builds on
    lie next to them."""
    ddi = ROOT / "tests/data/ddi"
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in (ddi / "codebook25-lgpl-3.0.txt").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in (ddi / "codebook25-gpl-3.0.txt").read_text(encoding="utf-8")
    assert "codebook25-lgpl-3.0.txt" in (ROOT / "NOTICE").read_text(encoding="utf-8")


def test_privacy_and_terms_pages_link_the_sources(client):
    """Not the footer any more: there only the imprint, the accessibility statement and the terms (Ralf, 2026-10-17)."""
    page = client.get("/imprint").data.decode()
    assert 'href="/sources"' in page[:page.index('<footer')]
    page = client.get("/terms/").data.decode()
    assert 'href="/sources"' in page[:page.index('<footer')]
    assert 'href="/sources"' not in page[page.index('<footer'):]


def test_third_party_list_has_every_locked_package():
    """tools/third_party_licences.py writes the list; a package added to a lock file has to be in it."""
    listed = (ROOT / "app/data/THIRD_PARTY_LICENSES.txt").read_text(encoding="utf-8")
    pin = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", re.M)
    for lock in ("requirements.lock", "deploy/docker/requirements-docker.lock", "deploy/docker/whisper/requirements.txt",
                 "deploy/docker/whisper/requirements-cpu.txt", "deploy/docker/requirements-static.lock"):
        for name, version in pin.findall((ROOT / lock).read_text(encoding="utf-8")):
            assert f"{name.lower().replace('_', '-')}=={version}  " in listed, (lock, name, version)


# Paths of the icons that followed Feather Icons or Lucide before they were redrawn (provenance check 2026-10-22, K7).
OLD_ICON_PATHS = (
    'M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6',
    'M12 19V5M5 12l7-7 7 7',
    'M12 5v14M19 12l-7 7-7-7',
    'M19 12H5M12 19l-7-7 7-7',
    'M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3',
    'M9 21H5V3h4M16 17l5-5-5-5M21 12H9',
    'M11 5L6 9H3v6h3l5 4z',
    'M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13',
    'M22 9l-6 6M16 9l6 6',
    'M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z',
    'M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z',
    'M9 14L4 9l5-5',
    'M4 9h11a5 5 0 010 10h-3',
    'M15 14l5-5-5-5',
    'M20 9H9a5 5 0 000 10h3',
    'M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v5M14 11v5',
    'M2 12s3.5-7 10-7c2 0 3.7.6 5.1 1.5M22 12s-3.5 7-10 7c-2 0-3.7-.6-5.1-1.5',
    'M9.9 9.9a3 3 0 004.2 4.2',
    'M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8',
    'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5',
    'M12 8v13M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7',
    'M12 8C11 5 9.5 3 7.5 3a2.5 2.5 0 0 0 0 5M12 8c1-3 2.5-5 4.5-5a2.5 2.5 0 0 1 0 5',
    'M12 3a9 9 0 0 0 0 18c1.1 0 1.8-.8 1.8-1.7 0-.5-.2-.8-.5-1.2-.3-.3-.5-.7-.5-1.2 0-.9.8-1.7 1.7-1.7H16a5 5 0 0 0 5-5c0-4-4-7.2-9-7.2z',
    'M2 21c0-4 3-6.5 7-6.5s7 2.5 7 6.5',
    'M16 4.2a4 4 0 0 1 0 7.6M18.5 14.8c2 .8 3.5 2.8 3.5 6.2',
    'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z',
    'M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3',
    'M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1',
    'M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1',
    'M21 16l-5-5-9 9',
    'M5 9v10h14V9M10 13h4',
    'M5 7l-3 7a3 3 0 0 0 6 0z',
    'M19 7l-3 7a3 3 0 0 0 6 0z',
    'M12 7v5l3 2',
    'M8 11V7a4 4 0 0 1 8 0v4',
    'M3 7l9 6 9-6',
    'M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4',
    'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
    'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
    'M3 12a9 9 0 1 0 3-6.7',
)


def test_icons_are_own_drawings_without_feather_or_lucide_paths(client):
    """Provenance check 2026-10-22, K7: the icons that followed Feather Icons and Lucide are redrawn for Askurious, so
    none of their old paths is left in a template or script, and the licence list and /sources name neither set."""
    files = [*(ROOT / "app/templates").rglob("*.html"), *(ROOT / "app/static").glob("*.js")]
    for f in files:
        text = f.read_text(encoding="utf-8")
        for d in OLD_ICON_PATHS:
            assert f'd="{d}"' not in text, (f.name, d)
    listed = (ROOT / "app/data/THIRD_PARTY_LICENSES.txt").read_text(encoding="utf-8")
    html = client.get("/sources").data.decode()
    head = (ROOT / "app/templates/_icons.html").read_text(encoding="utf-8").split("{% set _paths", 1)[0]
    for name in ("Feather", "Lucide"):
        assert name not in listed and name not in html
    assert "All icons here are drawn for Askurious" in head and "ORCID Brand Library" in html


def test_vendored_code_brings_its_licence_texts():
    vendor = ROOT / "app/static/vendor"
    for folder in ("leaflet", "leaflet-heat", "pdfjs"):
        assert (vendor / folder / "LICENSE").is_file()
    assert "Universite catholique de Louvain" in (vendor / "pdfjs/LICENSE_OPENJPEG").read_text(encoding="utf-8")
    assert "Mozilla Foundation" in (vendor / "pdfjs/LICENSE_PDFJS_OPENJPEG").read_text(encoding="utf-8")


def test_licence_files_without_extension_are_shown_as_text(client):
    """Licence audit 2026-10-21, B-1 and B-3: simpleheat's own licence is there and linked; LICENSE files without an
    extension come as plain text (from Flask and from nginx), not as a download."""
    html = client.get("/sources").data.decode()
    assert "/static/vendor/leaflet-heat/LICENSE_SIMPLEHEAT" in html
    assert "Copyright (c) 2015, Vladimir Agafonkin" in (ROOT / "app/static/vendor/leaflet-heat/LICENSE_SIMPLEHEAT").read_text(
        encoding="utf-8")
    for path in ("vendor/leaflet/LICENSE", "vendor/leaflet-heat/LICENSE_SIMPLEHEAT", "vendor/pdfjs/LICENSE_CORE_JS"):
        r = client.get(f"/static/{path}")
        assert r.status_code == 200 and r.headers["Content-Type"] == "text/plain; charset=utf-8", path
    assert client.get("/static/app.js").mimetype != "text/plain"
    for side in ("researcher", "participant"):
        conf = (ROOT / f"deploy/docker/nginx/locations/{side}.conf").read_text(encoding="utf-8")
        static = conf[conf.index("location ^~ /static/ {"):]
        rule = static.index("location ~ /LICENSE[^/.]*$ {")
        assert rule < static.index("\n    }\n") and "default_type text/plain;" in static[rule:static.index("}", rule)]


def test_sources_page_carries_the_brand_of_its_domain(app):
    """Licence audit 2026-10-21, B-9: under a second brand the page names that brand, not the main name."""
    app.config.update(PUBLIC_BASE_URL="https://askurious.de", BRANDS="polltergeist.de=POLLtergeist", SECRET_KEY="x" * 32)
    html = app.test_client().get("/sources", headers={"Host": "polltergeist.de"}).data.decode()
    assert "POLLtergeist enthält Daten" in html and not re.search(r"\bAskurious\b", html)
