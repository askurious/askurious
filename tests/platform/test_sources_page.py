"""The page /sources (Lizenzen und Datenquellen) and the files behind it (copyright audit 2026-10-16, U8, U15,
U21, U41; licence audit 2026-10-23, A1): open without an account, the derived ODbL data described (no
downloads), the lists in step with the lock files."""
import hashlib
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


# SHA-256 (first 16 hex digits) of the paths of the icons that followed Feather Icons or Lucide before they were
# redrawn (provenance check 2026-10-22, K7). Only the hashes stand here, so the old drawings are not passed on
# with the tests (licence audit 2026-10-29, EXT-01).
OLD_ICON_PATH_HASHES = frozenset({
    "467e9794ce50a22c", "af0ef5978954a256", "493a59c82d4c9c1c", "9e60f6eac28da111",
    "d41035f7a59f2b24", "d3d129c66f9a6dc8", "101085557a261429", "06a333e2eb3d73b2",
    "21e7740068752316", "59a19f624eac67e7", "9783503d23cdbf2b", "c76bff783f35ac39",
    "e3516a3c393006be", "aa91fc24fc2b5863", "160e1b97d1d91b38", "2c8e93e0b9cac46b",
    "5395745855e7ab8e", "8a235148b1aff914", "83b1b91192f30750", "e0431baa0f2f34bb",
    "342f9ad1bb301bb6", "d75fd3c897dc6a06", "f147121a42a41bcb", "b7132300a271fdfd",
    "9f886b80125ac098", "2ae45fa3675a893f", "8e5bdcda6ed228f2", "94dc0776fa10f249",
    "cfae6658088f8d22", "27e56d16b1b36dc2", "83d8b4fe812cf869", "be083e3c9450ffa2",
    "5a1052da2c025bd8", "fc5f4739d95cff25", "5802c8d8920384b0", "3a950e9342638be1",
    "245d53607000b307", "77219c77b8f7917d", "6c0a221da14f828e", "69c790aac6ebfa68",
})


def test_icons_are_own_drawings_without_feather_or_lucide_paths(client):
    """Provenance check 2026-10-22, K7: the icons that followed Feather Icons and Lucide are redrawn for Askurious, so
    none of their old paths is left in a template or script, and the licence list and /sources name neither set."""
    files = [*(ROOT / "app/templates").rglob("*.html"), *(ROOT / "app/static").glob("*.js")]
    for f in files:
        text = f.read_text(encoding="utf-8")
        for d in re.findall(r'["\'](M[^"\'<>]*)["\']', text):
            assert hashlib.sha256(d.encode("utf-8")).hexdigest()[:16] not in OLD_ICON_PATH_HASHES, (f.name, d)
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
