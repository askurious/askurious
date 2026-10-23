"""Browser form revisions and incomplete selections across navigation and language changes."""
import re
import pytest
from werkzeug.datastructures import MultiDict
from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent
from tests.participation.test_keep_page import build


def token(page):
    return re.search(r'name="pos" value="([^"]+)"', page.get_data(as_text=True)).group(1)


def last(app):
    with app.app_context():
        r = db.session.scalar(db.select(Response).order_by(Response.id.desc()))
        return dict(r.answers or {}), r.position, r.status


def test_old_same_page_form_cannot_overwrite_after_roundtrip(app, client, survey):
    url = build(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.allow_back = True
        db.session.commit()
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    old = token(p.get(url))
    p.post(url, data={'pos': old, 'nav': 'next', 'i2': 'NEW'})
    assert last(app)[0]['2'] == 'NEW'
    next_token = token(p.get(url))
    p.post(url, data={'pos': next_token, 'nav': 'back'})
    current = token(p.get(url))
    assert old != current
    result = p.post(url, data={'pos': old, 'nav': 'next', 'i2': 'OLD'})
    answers, position, status = last(app)
    print({'old_token': old, 'current_token': current, 'http': result.status_code,
           'stored': answers['2'], 'position': position, 'status': status})
    assert answers['2'] == 'NEW'


@pytest.mark.parametrize("navigation", ["back", "language"])
def test_incomplete_minimum_selection_returns_as_draft_on_back(app, client, survey, navigation):
    fill_consent(client)
    add(client, 'multiple_choice')
    client.post('/surveys/1/editor/items/2', data=MultiDict([
        ('code','multi'), ('question','Bitte auswaehlen'), ('answer_mode','required'), ('min','2'),
        ('option_id','a'), ('option_code','a'), ('option_label','Alpha'),
        ('option_id','b'), ('option_code','b'), ('option_label','Beta'),
        ('option_id','c'), ('option_code','c'), ('option_label','Gamma')]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.allow_back = True
        db.session.commit()
    client.post('/surveys/1/start', data={'use_end':'1', 'field_end':'2031-01-01'})
    with app.app_context():
        url = '/s/' + db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    pos = token(p.get(url))
    p.post(url, data=MultiDict([('pos',pos), ('nav','back') if navigation == 'back' else ('set_lang','en'), ('i2','a')]))
    answers, position, _ = last(app)
    print({'case':'partial_selection_back','stored':answers.get('2'), 'position':position})
    assert '2' not in answers
    assert position == (0 if navigation == 'back' else 1)
    if navigation == 'back':
        p.post(url, data={**CONSENT_OK, 'pos': token(p.get(url))})
    page = p.get(url).get_data(as_text=True)
    assert re.search(r'name="i2"[^>]*value="a"[^>]*checked', page)
    # A draft still fails the normal minimum on Next, then can be completed.
    failed = p.post(url, data=MultiDict([('pos', token(p.get(url))), ('nav', 'next'), ('i2', 'a')]))
    assert failed.status_code == 200 and last(app)[2] == Response.ACTIVE
    p.post(url, data=MultiDict([('pos', token(failed)), ('nav', 'next'), ('i2', 'a'), ('i2', 'b')]))
    assert last(app)[0]['2'] == ['a', 'b']


def test_zero_and_cleared_optional_number_roundtrip(app, client, survey):
    url = build(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.allow_back = True
        i = db.session.get(Item, 2)
        i.config = {**i.config, 'validation':'decimal'}
        db.session.commit()
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data={'pos': token(p.get(url)), 'nav':'next', 'i2':'0'})
    assert last(app)[0]['2'] == 0
    p.post(url, data={'pos': token(p.get(url)), 'nav':'back'})
    page = p.get(url)
    assert re.search(r'name="i2"[^>]*value="0"', page.get_data(as_text=True))
    p.post(url, data={'pos': token(page), 'nav':'next', 'i2':''})
    assert last(app)[0]['2'] is None
    print({'case':'number_zero_and_clear', 'zero_saved_and_restored':True, 'cleared':None})


def test_a_validation_error_keeps_the_token_of_the_page(app, client, survey):
    """An error keeps the person on the page: its token stays, only leaving the page renews it (D-01)."""
    url = build(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, 'validation': 'decimal'}
        db.session.commit()
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    old = token(p.get(url))
    error = p.post(url, data={'pos': old, 'nav': 'next', 'i2': 'not numeric'})
    assert token(error) == old
    assert last(app)[0].get('2') is None
    p.post(url, data={'pos': old, 'nav': 'next', 'i2': '0'})
    assert last(app)[0]['2'] == 0
