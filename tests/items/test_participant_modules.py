"""Only rendered rare types load their scripts, after core helpers, once per page (SP13 PF-17a)."""
from flask import render_template_string

from app.extensions import db
from app.items import get
from app.models import Item, Survey


def test_simple_page_has_no_rare_modules_and_voice_page_loads_it_once(app, client, survey):
    from tests.conftest import add

    plain = client.get("/surveys/1/editor/items/1/frame").get_data(as_text=True)
    assert "participant.js" in plain and "participant-voice.js" not in plain and "participant-tasks.js" not in plain
    with app.test_request_context():
        study = db.session.get(Survey, 1)
        kind = get("voice")
        for n in range(2):
            study.blocks[1].items.append(Item(type="voice", code=f"voice{n}", position=n,
                                               config=kind.default_config(), texts={"de": kind.default_texts("de")}))
        db.session.commit()
        html = render_template_string("{% extends 'participant/base.html' %}{% block content %}"
                                      "{% for item in items %}{% include 'participant/_item.html' %}{% endfor %}{% endblock %}",
                                      items=study.blocks[1].items, survey=study, lang="de", answers={}, errors={}, variables={})
        assert html.count("participant-voice.js") == 1
        assert html.index("participant.js") < html.index("participant-voice.js")
        assert "participant-tasks.js" not in html and "participant-sort.js" not in html


def test_every_script_of_a_rare_type_is_named_by_a_registered_type():
    """The crowd figures stayed away (pre-test 2026-10-20): the map said 'crowd', the type is 'sample_crowd', so
    participant-crowd.js was never loaded and the stage stayed empty. Every key of the map is a real type and every
    script it names exists."""
    import os
    import re

    from app import items

    line = open(os.path.join("app", "templates", "participant", "_item.html"), encoding="utf-8").readline()
    pairs = re.findall(r"'(\w+)':'(\w+)'", line)
    assert pairs and ("sample_crowd", "crowd") in pairs
    for key, script in pairs:
        assert key in items.REGISTRY, key
        assert os.path.exists(os.path.join("app", "static", f"participant-{script}.js")), script
