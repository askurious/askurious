"""Helpers of the tests from test_audit_2026_10_06_reste.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/editor/test_editor_audit_findings.py, tests/platform/test_platform_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Security audit 2026-10-06, the rests S17 and H5: the setting "eigene Befragungen anlegen" on an account has three
states, and a site admin's "no" outweighs a group with "darf Befragungen anlegen" (S17, migration 0013); a
condition reads a Rechenwert only of its own survey at every call, also for rows shown to some (export, previews),
the rules of the completion links and the pre-launch check of the quotas (H5). Runs on PostgreSQL too
(TEST_DATABASE_URL).
"""
import ast


import inspect


import pathlib


from alembic import command


from sqlalchemy import text


from app import accounts, flow, migrations, rows, signin


from app.auth import may_create_surveys


from app.extensions import db


from app.models import Item, Survey, User, UserLogin, WorkGroup, WorkGroupMember


from tests.conftest import allow


ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)


OTHER = "0000-0002-1825-0097"


THIRD = "0000-0000-0000-0124"


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _uid(app, orcid):
    with app.app_context():
        return db.session.scalar(db.select(User.id).filter_by(orcid=orcid))


def _may(app, orcid):
    with app.test_request_context():
        return may_create_surveys(db.session.scalar(db.select(User).filter_by(orcid=orcid)))


def _stored(app, orcid):
    """The former column: since 2026-10-18 only an admin's "no" (False) is read (kind_access.said_no)."""
    with app.app_context():
        row = db.session.get(UserLogin, _uid(app, orcid))
        return row.may_create if row is not None else "no row"


def _in_group(app, orcid, name="Lehrstuhl Test"):
    with app.app_context():
        group = WorkGroup(name=name, may_create=True)
        db.session.add(group)
        db.session.flush()
        db.session.add(WorkGroupMember(group_id=group.id, orcid=orcid, user_id=_uid(app, orcid), role="member"))
        from app import kind_access

        kind_access.joined(group, db.session.get(User, _uid(app, orcid)))   # "create" once on joining (E3)
        db.session.commit()


def _set(client, uid, value):
    return client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": value},
                       follow_redirects=True).data.decode()


def _calc_in(app, client, title):
    """A draft of `client` with a number question and a Rechenwert of 12 times it; returns (survey id, source, calc)."""
    client.post("/surveys/new", data={"title": title, "languages": ["de"], "default_language": "de"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title=title))
        sid, block = s.id, next(b.id for b in s.blocks if not any(i.type == "consent" for i in b.items))
    client.post(f"/surveys/{sid}/editor/blocks/{block}/items", data={"type": "text_input"})
    client.post(f"/surveys/{sid}/editor/blocks/{block}/items", data={"type": "calc"})
    with app.app_context():
        source, calc = db.session.scalars(db.select(Item.id).filter_by(block_id=block).order_by(Item.id)).all()
    client.post(f"/surveys/{sid}/editor/items/{source}", data={"code": "x", "question": "x?", "validation": "integer"})
    client.post(f"/surveys/{sid}/editor/items/{calc}", data={"code": "y", "question": "y", "calc_a": str(source),
                                                            "calc_op": "mul", "calc_b": "", "calc_fixed": "12",
                                                            "calc_places": "0"})
    return sid, source, calc
