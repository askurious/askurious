"""Rendered roles, navigation and accessible image choices (UI audit 2026-10-17)."""
import re
import pytest
from app.extensions import db
from app.models import Survey, SurveyMember
from tests.conftest import allow
from tests.dashboards.test_showcase import OTHER, _as


@pytest.mark.parametrize('state,reason', [('draft', None), ('running', None), ('closed', 'paused'), ('closed', 'manual')])
@pytest.mark.parametrize('role', ['viewer', 'editor', 'admin'])
def test_editor_explains_permissions_separately_from_lifecycle(app, client, survey, state, reason, role):
    allow(app, OTHER)
    reader = _as(app, OTHER, 'Reader')
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status, s.close_reason = state, reason or ''
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, role=role))
        db.session.commit()
    response = reader.get('/surveys/1/editor', follow_redirects=True)
    assert response.status_code == 200
    page = response.data.decode()
    sidebar = page.split('data-sidebar="left"', 1)[1].split('</aside>', 1)[0]
    if role == 'viewer':
        assert 'Ihre Rolle ist „Ansehen“' in sidebar
        assert 'Die Befragung ist geschlossen' not in sidebar
        assert '#reopen' not in sidebar
    elif state == 'draft':
        assert 'data-palette-search' in sidebar
    else:
        assert ('Die Befragung läuft' if state == 'running' else 'Die Befragung ist pausiert' if reason == 'paused' else 'Die Befragung ist geschlossen') in sidebar


def test_research_plan_keeps_the_surveys_area_active(client, survey):
    page = client.get('/surveys/1/researchdesign').data.decode()
    for nav in re.findall(r'<nav\b[^>]*class="[^"]*nav-(?:areas|narrow-areas)[^"]*"[^>]*>(.*?)</nav>', page, re.S):
        assert nav.count('aria-current="page"') == 1
        assert re.search(r'<a[^>]*href="/dashboard/"[^>]*aria-current="page"[^>]*>Meine Befragungen</a>', nav)
    assert re.search(r'<a[^>]*href="[^"]*/researchdesign"[^>]*aria-current="page"[^>]*>.*?Studienplan.*?</a>', page, re.S)


@pytest.mark.parametrize('orcid', ['', 'configured-client'])
@pytest.mark.parametrize('mail', [False, True])
def test_signin_only_offers_configured_orcid(app, orcid, mail):
    if mail:
        from tests.conftest import mail_on
        mail_on(app)
    app.config['ORCID_CLIENT_ID'] = orcid
    page = app.test_client().get('/auth/sign-in').data.decode()
    assert ('Mit ORCID anmelden' in page) == bool(orcid)
    assert ('persönliche Einladung' in page) == mail
    assert 'Konten mit Passwort legt ein Admin' not in page


def test_image_radio_labels_have_separate_descriptions(app):
    from flask import render_template
    from app import image_styles
    with app.test_request_context('/'):
        page = render_template('surveys/_fields_image_style.html', style_cfg={'key': '', 'custom': ''}, image_styles=image_styles)
    inputs = re.findall(r'<input\b[^>]*type="radio"[^>]*>', page)
    assert len(inputs) > 3
    for control in inputs:
        name = re.search(r'aria-labelledby="([^"]+)"', control)[1]
        description = re.search(r'aria-describedby="([^"]+)"', control)[1]
        assert page.count(f'id="{name}"') == 1
        assert page.count(f'id="{description}"') == 1
    assert all('alt=""' in image for image in re.findall(r'<img\b[^>]*>', page))
