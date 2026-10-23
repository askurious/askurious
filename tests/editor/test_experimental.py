"""Experimental features (app/experimental.py): site admins switch them on for persons, groups or everyone, and keep
persons out (Ralf, 2026-10-05: no self-selection); the page "Konto" only lists what is on."""
import pytest

from app import experimental
from app.extensions import db
from app.models import AccessLog, ExperimentGrant, User, WorkGroup, WorkGroupMember
from tests.conftest import experiments_on

ME = "0000-0000-0000-001X"   # site admin in the tests
OTHER = "0000-0002-1825-0097"


def _user(app, orcid=ME):
    with app.app_context():
        return db.session.scalar(db.select(User).filter_by(orcid=orcid))


def _other(app):
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Andere")
    other.post("/terms/", data={"accept": "1"})
    return other


def _active(app, orcid=ME):
    with app.app_context():
        return experimental.active(db.session.scalar(db.select(User).filter_by(orcid=orcid)))


def change(client, feature, target, action):
    return client.post("/admin/experimental", data={"feature": feature, "target": target, "action": action})


@pytest.fixture
def two(monkeypatch):
    """A second experimental feature next to the real ones, to see that the switches are independent."""
    features = experimental.FEATURES + [("probe", "Probefunktion", "nur für den Test")]
    monkeypatch.setattr(experimental, "FEATURES", features)
    monkeypatch.setattr(experimental, "KEYS", [k for k, _l, _t in features])


def test_konto_only_lists_what_an_admin_switched_on(app, client, two):
    page = client.get("/account/").get_data(as_text=True)
    assert 'id="experimentell"' not in page and "Probefunktion" not in page  # nothing at all without a feature
    assert client.post("/account/experimental", data={"feature": "probe"}).status_code in (404, 405)  # no self-selection
    assert _active(app) == []
    experiments_on(client, "probe")
    page = client.get("/account/").get_data(as_text=True)
    section = page[page.index('id="experimentell"'):].split("</section>")[0]  # its place: after "Lernhilfen" (UI/UX audit 2026-10-16, 22)
    assert "Probefunktion" in section and 'type="checkbox"' not in section and "nur ein Admin" in section
    assert "Coscine (Forschungsdatenarchiv" not in section and "Greenwald" not in section  # sources only of what is on


def test_enabled_and_require_per_feature(app, client, two):
    from flask_login import login_user
    from werkzeug.exceptions import NotFound

    with app.test_request_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=ME))
        login_user(user)
        assert not experimental.enabled() and not experimental.enabled("coding")  # admins are no exception
        with pytest.raises(NotFound):
            experimental.require("coding")
        user.experiments = ["probe"]
        assert experimental.enabled("probe") and experimental.enabled()  # without a key: any of them
        assert not experimental.enabled("coding")
        experimental.require("probe")
        user.experiments = ["coding", "coscine", "vignettes"]  # features that left the group are ignored
        assert experimental.active(user) == ["coding"]
        user.experimental = True  # all at once (tests, older code)
        assert experimental.active(user) == experimental.KEYS and "probe" in experimental.KEYS
    with app.test_request_context():
        assert not experimental.enabled("coding")  # nobody signed in


def test_admin_switches_a_person_on_and_off(app, client, two):
    other = _other(app)
    uid = _user(app, OTHER).id
    page = client.get("/admin/experimental").get_data(as_text=True)
    assert "Probefunktion" in page and 'id="exp-probe"' in page and "Andere" in page  # the choice lists accounts
    change(client, "probe", f"u:{uid}", "on")
    assert _active(app, OTHER) == ["probe"] and _active(app) == []
    assert "Probefunktion" in other.get("/account/").get_data(as_text=True)
    page = client.get("/admin/experimental").get_data(as_text=True)
    assert "Ausschalten" in page[page.index('id="exp-probe"'):]
    # only switched on for single people: nothing to exclude from, "Ausschalten" does it (Ralf, 2026-10-07)
    probe = page[page.index('id="exp-probe"'):].split('<section', 1)[0]  # up to the next feature
    assert "Person ausschließen" not in probe and "Ausschließen" not in probe
    change(client, "probe", f"u:{uid}", "off")
    assert _active(app, OTHER) == []
    assert other.get("/admin/experimental").status_code == 403  # only site admins
    assert other.post("/admin/experimental", data={"feature": "probe", "target": f"u:{uid}", "action": "on"}).status_code == 403
    assert change(client, "nicht-vorhanden", f"u:{uid}", "on").status_code == 400


def test_groups_everyone_and_who_is_kept_out(app, client, two):
    _other(app)
    with app.app_context():
        group = WorkGroup(name="Lehrstuhl")
        db.session.add(group)
        db.session.flush()
        db.session.add(WorkGroupMember(group_id=group.id, orcid=OTHER, role="member"))
        db.session.commit()
        gid, uid = group.id, db.session.scalar(db.select(User.id).filter_by(orcid=OTHER))
    change(client, "probe", f"g:{gid}", "on")
    assert _active(app, OTHER) == ["probe"] and _active(app) == []  # live for the members only
    page = client.get("/admin/experimental").get_data(as_text=True)
    assert "Person ausschließen" in page[page.index('id="exp-probe"'):]    # through a group: excluding matters
    change(client, "probe", f"u:{uid}", "blocked")
    assert _active(app, OTHER) == []  # kept out wins over the group
    change(client, "probe", "all", "on")
    assert _active(app) == ["probe"] and _active(app, OTHER) == []  # ... and over everyone
    page = client.get(f"/admin/accounts/{uid}").get_data(as_text=True)
    assert "ausgeschlossen" in page and "Ausschluss aufheben" in page
    change(client, "probe", f"u:{uid}", "off")
    assert _active(app, OTHER) == ["probe"]
    change(client, "probe", "all", "off")
    change(client, "probe", f"g:{gid}", "off")
    assert _active(app, OTHER) == [] and _active(app) == []
    change(client, "probe", f"g:{gid}", "on")
    with app.app_context():
        db.session.delete(db.session.get(WorkGroup, gid))  # a deleted group takes its switches along
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(ExperimentGrant.id))) == 0
    assert change(client, "probe", f"g:{gid}", "blocked").status_code == 302  # only persons are kept out
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(ExperimentGrant.id))) == 0


def test_account_page_of_the_administration(app, client, two):
    _other(app)
    uid = _user(app, OTHER).id
    page = client.get(f"/admin/accounts/{uid}").get_data(as_text=True)
    assert 'id="experimentell"' in page and "Probefunktion" in page and "Einschalten" in page
    client.post(f"/admin/accounts/{uid}", data={"action": "experiment", "feature": "probe", "state": "on"})
    assert _active(app, OTHER) == ["probe"]
    assert "eingeschaltet" in client.get(f"/admin/accounts/{uid}").get_data(as_text=True)
    assert client.post(f"/admin/accounts/{uid}", data={"action": "experiment", "feature": "probe", "state": "x"}).status_code == 400
    client.post(f"/admin/accounts/{uid}", data={"action": "experiment", "feature": "probe", "state": "blocked"})
    with app.app_context():
        assert _user(app, OTHER).experiments == []  # kept out ends the person's own switch too
        assert db.session.scalar(db.select(db.func.count(AccessLog.id))) == 0  # not in the log (Ralf, 2026-10-06)


@pytest.fixture
def probe_type(monkeypatch, two):
    """The voice message as the element type of the feature "probe": since 2026-10-16 no element type is
    experimental, the mechanism stays for the next one."""
    from app.items.voice import Voice

    monkeypatch.setattr(Voice, "experimental", "probe", raising=False)


def test_a_survey_that_uses_a_feature_keeps_it(app, client, survey, probe_type):
    """Switched off: the team keeps the element in a survey that uses it; only new use needs the feature."""
    from tests.conftest import add

    experiments_on(client, "probe")
    assert add(client, "voice").status_code == 302
    experiments_on(client)
    assert client.get("/surveys/1/editor/items/2").status_code == 200
    assert add(client, "voice").status_code == 404


def test_a_person_kept_out_does_not_get_it_through_a_survey(app, client, survey, probe_type):
    """Abgleich 2026-10-15 AU-15: "ausgeschlossen" wins over a survey that already uses the feature."""
    from tests.conftest import add

    experiments_on(client, "probe")
    assert add(client, "voice").status_code == 302
    me = _user(app)
    change(client, "probe", f"u:{me.id}", "blocked")
    assert client.get("/surveys/1/editor/items/2").status_code == 404
    with app.test_request_context():
        from app.models import Survey

        user = db.session.get(User, me.id)
        assert not experimental.enabled("probe", user=user, survey=db.session.get(Survey, 1))
        change(client, "probe", f"u:{me.id}", "off")
        experimental.forget()
        assert experimental.enabled("probe", user=db.session.get(User, me.id), survey=db.session.get(Survey, 1))


def test_four_features_are_regular_since_2026_10_16(app, client, survey):
    """Ralf, 2026-10-16: voice message, video / audio with a playback log, the sound of the video conference and
    "Schneller blättern" are for everyone: no key any more, the element types without one either."""
    from app import items
    from tests.conftest import add

    assert not {"voice", "media", "conference", "blockload"} & set(experimental.KEYS)
    assert not any(getattr(k, "experimental", None) for k in (items.get("voice"), items.get("media")))
    experiments_on(client)
    assert add(client, "voice").status_code == 302 and add(client, "media").status_code == 302


def test_the_old_single_tick_becomes_all_current_features(app):
    """A database from before one tick per feature: the column user.experiments is missing, user.experimental is
    set. At the start the column comes back (add_missing_columns, SQLite and PostgreSQL) and the account gets
    every feature that is experimental now."""
    from sqlalchemy import inspect, text

    from app.extensions import add_missing_columns

    with app.app_context():
        db.session.add(User(orcid="0000-0002-0000-0001", name="Alt", experimental_legacy=True))
        db.session.add(User(orcid="0000-0002-0000-0002", name="Ohne"))
        db.session.commit()
        db.session.close()
        with db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE "user" DROP COLUMN experiments'))
        add_missing_columns(app)
        assert "experiments" in {c["name"] for c in inspect(db.engine).get_columns("user")}
        assert experimental.upgrade_legacy() == 1
        old = db.session.scalar(db.select(User).filter_by(orcid="0000-0002-0000-0001"))
        assert sorted(old.experiments) == sorted(experimental.KEYS) and old.experimental_legacy is False
        assert not db.session.scalar(db.select(User).filter_by(orcid="0000-0002-0000-0002")).experiments
        assert experimental.upgrade_legacy() == 0  # only once: switching off later stays off


def test_organisations_switch_a_feature_on_for_everyone_in_them(app, client, two):
    """On for a whole organisation (Ralf 2026-10-20): live for everyone in it as app/orgs.py counts them, never read as
    "für alle", a person kept out stays out, and a deleted organisation takes its switches along."""
    from app import orgs

    _other(app)
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        other = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        institute = orgs.make("Institut für Probe", me)
        orgs.add(institute, other, home=True)
        db.session.commit()
        oid, uid, main, guest = institute.id, other.id, orgs.default_id(), orgs.guest_id()
    page = client.get("/admin/experimental").get_data(as_text=True)
    assert f'value="o:{oid}"' in page and "Organisation „Institut für Probe“" in page  # in the choice, with a search
    assert 'data-filter-select="exp-target-probe"' in page and f'value="o:{guest}"' not in page
    change(client, "probe", f"o:{oid}", "on")
    assert _active(app, OTHER) == ["probe"] and _active(app) == []  # its people only, not "für alle"
    probe = client.get("/admin/experimental").get_data(as_text=True)
    probe = probe[probe.index('id="exp-probe"'):].split('<section', 1)[0]
    assert "eingeschaltet für die Organisation" in probe and "1 Organisation" in probe and "Person ausschließen" in probe
    assert "über die Organisation" in client.get(f"/admin/accounts/{uid}").get_data(as_text=True)
    change(client, "probe", f"u:{uid}", "blocked")
    assert _active(app, OTHER) == []  # kept out wins over the organisation
    change(client, "probe", f"u:{uid}", "off")
    change(client, "probe", f"o:{main}", "on")
    assert _active(app) == ["probe"]  # home "main" counts as being in it
    change(client, "probe", f"o:{main}", "off")
    assert _active(app) == [] and _active(app, OTHER) == ["probe"]
    assert change(client, "probe", f"o:{oid}", "blocked").status_code == 302  # only persons are kept out
    assert change(client, "probe", f"o:{guest}", "on").status_code == 404
    change(client, "probe", f"o:{oid}", "off")
    assert _active(app, OTHER) == []
    change(client, "probe", f"o:{oid}", "on")
    with app.app_context():
        db.session.delete(orgs.row_of(oid, uid))
        db.session.flush()
        orgs.forget()
        assert orgs.delete(db.session.get(orgs.Organisation, oid)) is None  # its switches go along
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(ExperimentGrant.id))) == 0
