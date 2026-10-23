"""ORCID iD display (rights review 2026-10-08): the own record shows the iD as https URI, lists the icon alone; the
official icon files, "unauthenticated" for iDs nobody signed in with here; ORCID's trademark notice on the terms."""
import os

from app.extensions import db
from app.models import User, WorkGroup, WorkGroupMember

ICON = 'class="orcid-icon" src="/static/img/orcid/orcid-id.svg'
UNAUTH = 'class="orcid-icon orcid-unauth" src="/static/img/orcid/orcid-id-unauth.svg'


def test_account_pages_show_the_full_id_lists_the_icon(app, client):
    page = client.get("/account/").data.decode()
    assert 'class="orcid-link orcid-full"' in page and 'alt="ORCID iD"> https://orcid.org/0000-0000-0000-001X</a>' in page
    with app.app_context():
        other = User(orcid="0000-0002-1825-0097", name="Kollegin")
        db.session.add(other)
        db.session.commit()
        user_id = other.id
    page = client.get(f"/admin/accounts/{user_id}").data.decode()
    assert 'alt="ORCID iD"> https://orcid.org/0000-0002-1825-0097</a>' in page and "unauthenticated" not in page
    page = client.get("/admin/people").data.decode()
    assert 'href="https://orcid.org/0000-0000-0000-001X"' in page and '> https://orcid.org/' not in page
    assert ICON in page and 'aria-label="ORCID-Eintrag ansehen: 0000-0000-0000-001X"' in page


def test_ids_without_an_account_are_unauthenticated(app, client):
    with app.app_context():
        lead = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        group = WorkGroup(name="Lehrstuhl")
        db.session.add(group)
        db.session.flush()
        db.session.add(WorkGroupMember(group_id=group.id, orcid=lead.orcid, name=lead.name, user_id=lead.id, role="lead"))
        db.session.add(WorkGroupMember(group_id=group.id, orcid="0000-0000-0000-0124", name="Neu", role="member"))
        db.session.commit()
        group_id = group.id
    page = client.get(f"/groups/{group_id}").data.decode()
    assert ICON in page and UNAUTH in page
    assert 'aria-label="ORCID-Eintrag ansehen: 0000-0000-0000-0124 (unauthenticated)"' in page
    assert '<span class="orcid-unauth-text">(unauthenticated)</span>' in page
    assert page.count("orcid-unauth-text") == 1


def test_lookup_without_a_hint(app, client):
    from flask import render_template_string

    with app.test_request_context():
        out = render_template_string('{% from "_macros.html" import orcid_link %}{{ orcid_link(a) }}|{{ orcid_link(b) }}',
                                     a="0000-0000-0000-001X", b="0000-0000-0000-0124")
    known, typed = out.split("|")
    assert ICON in known and "unauthenticated" not in known
    assert UNAUTH in typed and "(unauthenticated)" in typed


def test_icon_files_and_trademark_notice(app, client):
    folder = os.path.join(app.root_path, "static", "img", "orcid")
    for name in ("orcid-id.svg", "orcid-id-mono.svg", "orcid-id-reverse.svg", "orcid-id-unauth.svg",
                 "orcid-id-unauth-mono.svg", "orcid-id-unauth-reverse.svg"):
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            assert "<svg" in f.read(), name
    css = open(os.path.join(app.root_path, "static", "app.css"), encoding="utf-8").read()
    for name in ("mono", "reverse", "unauth-mono", "unauth-reverse"):
        assert f'url("img/orcid/orcid-id-{name}.svg")' in css
    page = client.get("/terms/").data.decode()
    assert "ORCID™, das ORCID-Logo und das iD-Logo sind Marken von ORCID, Inc." in page
