"""Two maps share deferred Leaflet/helpers; Bundesland geometry stays exact (SP13 PF-15/PF-16)."""
from pathlib import Path
from xml.etree import ElementTree

from flask import render_template_string

from app.items import get
from app.items.federal_state import outline
from app.models import Item, Survey
from app.extensions import db


def test_two_maps_load_leaflet_and_helpers_once(app, client, survey):
    with app.test_request_context():
        study = db.session.get(Survey, 1)
        block = study.blocks[1]
        for n in range(2):
            kind = get("map_marker")
            block.items.append(Item(type="map_marker", code=f"map{n}", position=n,
                                    config=kind.default_config(), texts={"de": kind.default_texts("de")}))
        db.session.commit()
        html = render_template_string("{% for item in items %}{% include 'participant/_item.html' %}{% endfor %}",
                                      items=block.items, survey=study, lang="de", answers={}, errors={}, variables={})
        assert html.count("vendor/leaflet/leaflet.js") == 1
        assert html.count("vendor/leaflet/leaflet.css") == 1 and html.count("map_marker.js") == 1
        import re

        scripts = re.findall(r"<script[^>]*>", html)
        assert scripts and all("defer" in script for script in scripts)


def test_external_state_sprite_contains_the_original_sixteen_outlines(app):
    svg = ElementTree.fromstring((Path(app.static_folder) / "geo/state-outlines.svg").read_bytes())
    paths = {path.attrib["id"].removeprefix("state-"): path.attrib["d"]
             for path in svg.iter("{http://www.w3.org/2000/svg}path")}
    with app.app_context():
        assert paths == outline()["paths"]
