"""Confirmed P1 audit regressions: persisted timing and accessible participant states."""
import re
import pytest
from app import theme
from tests.analysis.test_pagetime import setup, start, response, wait
from tests.interview.test_voice import _voice


def test_ten_extensions_survive_reload_language_and_retry(app, client, survey):
    setup(client, required=True, max_seconds=30)
    p, url = start(app, client)
    p.get(url)
    for count in range(1, 11):
        # No answer fields: requesting time must neither validate nor advance the page.
        data = {"pos": "r1", "nav": "time_more", "time_more": str(count)}
        result = p.post(url, data=data, follow_redirects=True)
        assert result.status_code == 200 and b"has-error" not in result.data
        assert response(app)[1] == 1
        assert response(app)[3]["_hold"]["more"] == count
        p.post(url, data=data)  # network retry is idempotent
        html = p.get(url).data.decode()
        assert f'name="time_more" value="{count}"' in html
        assert int(re.search(r'data-max="(\d+)"', html)[1]) >= 30 * (count + 1) - 2
    assert len(response(app)[3]["_more"]) == 1
    assert 'data-hold-more-field' not in html
    p.post(url, data={"pos": "r1", "set_lang": "en", "i2": "draft", "time_more": "10"})
    assert response(app)[2]["2"] == "draft" and response(app)[3]["_hold"]["more"] == 10
    wait(app, 330)
    assert p.post(url, data={"pos": "r1", "nav": "next", "time_up": "1", "time_more": "10"}).status_code == 302
    assert response(app)[1] == 2
    p.get(url)
    p.post(url, data={"pos": "r2", "nav": "back"})
    assert response(app)[1] == 1
    assert 'data-more="30"' in p.get(url).data.decode()  # returning starts a fresh allowance


def test_short_nonessential_timer_leaves_reaction_time(app, client, survey):
    setup(client, max_seconds=5)
    p, url = start(app, client)
    html = p.get(url).data.decode()
    assert 'data-max="30"' in html and 'data-more="30"' in html


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("fallback", [False, True])
def test_voice_guidance_matches_available_actions(app, client, survey, required, fallback):
    _voice(app, client, answer_mode="required" if required else "optional", text_fallback="1" if fallback else "")
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert ('class="p-text p-voice-text"' in html) == fallback
    assert ("Ohne Aufnahme können Sie nicht weitergehen" in html) == (required and not fallback)
    assert ("diese Frage mit „Weiter“ überspringen" in html) == (not required and not fallback)
    assert 'role="alert" data-voice-problem' in html


def test_notice_contrast_all_presets_and_auto_custom():
    designs = [{**theme.standard(), "preset": key, "colors": dict(value["colors"]), **value.get("look", {}), **value.get("fixed", {})}
               for key, value in theme.BUILTIN.items()]
    auto = theme.clean({"scheme": "auto", "colors": {**theme.STANDARD, "accent": "#8a1c3b"}})
    designs += [auto, theme.dark_side(auto)]
    for design in designs:
        t = theme.tokens(design)
        assert theme.contrast(t["--on-nudge"], t["--nudge-soft"]) >= 4.5, design["preset"]

@pytest.mark.parametrize('kind,config,texts,answer,expected', [
    ('multiple_choice', {'options':[{'id':'a','code':'1'},{'id':'b','code':'2'}]}, {'question':'Auswahl','options':{'a':'Alpha','b':'Beta'}}, ['1','2'], ['Alpha','Beta']),
    ('likert', {'points':5,'statements':[{'id':'a','code':'A','reverse':False}]}, {'question':'Bewertung','statements':{'a':'Aussage Alpha'}}, {'a':3}, ['Aussage Alpha','teils']),
    ('rating', {'min':1,'max':5,'points':5,'style':'stars'}, {'question':'Sterne'}, 3, ['Sterne','3']),
])
def test_locked_answers_have_readable_text_without_inert(app, kind, config, texts, answer, expected):
    from flask import render_template
    from types import SimpleNamespace
    from app.models import Item, Block, Survey
    with app.test_request_context('/'):
        item=Item(id=42,type=kind,code='X',config=config,texts={'de':texts},answer_mode='optional')
        item.block = Block(survey=Survey(default_language='de'))
        html=render_template('participant/_item.html',item=item,lang='de',answers={42:answer},errors={},variables={},
                             assets=SimpleNamespace(extra_scripts=[]),locked_items={42},ui=lambda de,en:de)
        assert ' inert' not in html and 'p-fixed-note' in html
        assert '<input' not in html and '<button' not in html
        assert all(word in html for word in expected), html
