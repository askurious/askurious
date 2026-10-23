"""Designs: a survey's theme kept under a name, used again in other surveys and shared with others. A survey always
gets its own copy, so changing, unsharing or deleting a design never changes a survey (app/designs.py)."""
import io
import os

from PIL import Image

from app import theme
from app.extensions import db
from app.models import Design, Survey, SurveyMember, User
from tests.conftest import allow, may_sign_in
from tests.helpers import rwth_design

OTHER = "0000-0002-1825-0097"
ME = "0000-0000-0000-001X"
WINE = {"accent": "#8a1c3b", "background": "#fffaf5", "text": "#221a1a", "surface": "#ffffff"}


def _png():
    out = io.BytesIO()
    Image.new("RGBA", (300, 80), (138, 28, 59, 255)).save(out, format="PNG")
    return out.getvalue()


def _style(client, survey_id=1, **values):
    data = {**theme.STANDARD, "font": "system", "font_scale": "100", "radius": "soft", **values}
    return client.post(f"/surveys/{survey_id}/design", data=data)


def _theme(client, survey_id=1):
    with client.application.app_context():
        return theme.clean(db.session.get(Survey, survey_id).theme)


def _file(client, uid, name):
    return os.path.join(client.application.config["UPLOAD_FOLDER"], uid, name)


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _keep(client, name="Lehrstuhl"):
    """Survey 1 with own colours, a serif font and a logo, kept as a design; returns the design's id."""
    _style(client, **WINE, font="serif", radius="round")
    client.post("/surveys/1/design/logo", data={"file": (io.BytesIO(_png()), "l.png"), "variant": "light", "rights": "1"},
                content_type="multipart/form-data")
    r = client.post("/surveys/1/design/save", data={"name": name}, follow_redirects=True).data.decode()
    assert f"Als Design „{name}“ gespeichert" in r
    with client.application.app_context():
        return db.session.scalar(db.select(Design).order_by(Design.id.desc())).id


def test_keep_a_theme_as_design_and_use_it_in_another_survey(client, survey):
    did = _keep(client)
    with client.application.app_context():
        design = db.session.get(Design, did)
        data, uid1 = design.data, db.session.get(Survey, 1).uid
        assert design.name == "Lehrstuhl" and data["colors"] == WINE and data["font"] == "serif"
    logo1 = _theme(client)["logo"]["file"]
    assert data["logo"]["file"] != logo1 and os.path.isfile(_file(client, f"_design_{did}", data["logo"]["file"]))
    page = client.get("/designs/").data.decode()
    assert "Lehrstuhl" in page and "Meine Designs" in page and "mit Logo" in page
    # a second survey takes it: colours, font and its own copy of the logo
    client.post("/surveys/new", data={"title": "Zwei", "languages": ["de"], "default_language": "de"})
    assert f'value="{did}"' in client.get("/surveys/2/design").data.decode()
    r = client.post("/surveys/2/design/apply", data={"design": did}, follow_redirects=True).data.decode()
    assert "Design „Lehrstuhl“ übernommen" in r
    two = _theme(client, 2)
    with client.application.app_context():
        uid2 = db.session.get(Survey, 2).uid
    assert two["colors"] == WINE and (two["font"], two["radius"]) == ("serif", "round")
    assert two["logo"]["file"] not in (logo1, data["logo"]["file"]) and os.path.isfile(_file(client, uid2, two["logo"]["file"]))
    assert "--accent: #8a1c3b" in client.get("/surveys/2/editor/preview").data.decode()
    # deleting the design changes neither survey
    client.post(f"/designs/{did}/delete")
    with client.application.app_context():
        assert db.session.get(Design, did) is None
    assert not os.path.exists(_file(client, f"_design_{did}", data["logo"]["file"]))
    assert _theme(client, 2) == two and os.path.isfile(_file(client, uid2, two["logo"]["file"]))
    assert os.path.isfile(_file(client, uid1, logo1))


def test_rename_duplicate_and_limits(client, survey):
    did = _keep(client)
    client.post(f"/designs/{did}/rename", data={"name": "  Institut   für  Beispiele " + "x" * 200})
    with client.application.app_context():
        name = db.session.get(Design, did).name
        assert name.startswith("Institut für Beispiele x") and len(name) == 80
    client.post(f"/designs/{did}/duplicate")
    with client.application.app_context():
        copy = db.session.scalar(db.select(Design).order_by(Design.id.desc()))
        assert copy.id != did and copy.name.endswith("(Kopie)") and copy.data["colors"] == WINE
        assert copy.data["logo"]["file"] != db.session.get(Design, did).data["logo"]["file"]
        assert os.path.isfile(_file(client, f"_design_{copy.id}", copy.data["logo"]["file"]))
    # the standard look can be kept as well (a survey without a theme)
    client.post("/surveys/1/design", data={"reset": "1"})
    client.post("/surveys/1/design/logo/remove", data={"variant": "light"})
    client.post("/surveys/1/design/save", data={"name": "Schlicht"})
    with client.application.app_context():
        plain = db.session.scalar(db.select(Design).filter_by(name="Schlicht"))
        assert plain.data["preset"] == "askurious" and "logo" not in plain.data


def test_share_use_and_unshare(client, survey):
    did = _keep(client)
    assert "ORCID iD angeben" in client.post(f"/designs/{did}/share", data={"orcid": "nobody"}, follow_redirects=True).data.decode()
    assert "gehört Ihnen bereits" in client.post(f"/designs/{did}/share", data={"orcid": ME}, follow_redirects=True).data.decode()
    assert "kein Anmelderecht" in client.post(f"/designs/{did}/share", data={"orcid": OTHER}, follow_redirects=True).data.decode()
    may_sign_in(client.application, OTHER)  # sharing needs someone who can sign in (H4)
    assert "Design geteilt" in client.post(f"/designs/{did}/share", data={"orcid": OTHER}, follow_redirects=True).data.decode()
    assert "schon geteilt" in client.post(f"/designs/{did}/share", data={"orcid": OTHER}, follow_redirects=True).data.decode()
    # the other person (signs in later; the share is by ORCID iD) with a survey of their own: may create surveys,
    # and so designs (tests/accounts/test_security_roles.py: an account that was only invited makes none)
    allow(client.application, OTHER)
    _as(client, OTHER, "Other")
    with client.application.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        mine = Survey(title="Drei", created_by_id=user.id, languages=["de"], default_language="de")
        mine.members.append(SurveyMember(orcid=OTHER, name="Other", user_id=user.id, role="admin"))
        db.session.add(mine)
        db.session.commit()
        sid, uid = mine.id, user.id
    page = client.get("/designs/").data.decode()
    assert "Mit mir geteilt" in page and "Lehrstuhl" in page and "von Philipp" in page
    # use and duplicate, but not change
    assert client.post(f"/designs/{did}/rename", data={"name": "Meins"}).status_code == 404
    assert client.post(f"/designs/{did}/delete").status_code == 404
    assert client.post(f"/designs/{did}/share", data={"orcid": "0000-0000-0000-0124"}).status_code == 404
    settings = client.get(f"/surveys/{sid}").data.decode()
    assert f'value="design:{did}"' in settings and "Design von Philipp" in settings
    client.post(f"/surveys/{sid}/design/choose", data={"preset": f"design:{did}"})
    assert _theme(client, sid)["colors"] == WINE and _theme(client, sid)["logo"]
    client.post(f"/designs/{did}/duplicate")
    with client.application.app_context():
        own = db.session.scalar(db.select(Design).filter_by(owner_id=uid))
        assert own is not None and own.data["colors"] == WINE
    # unshared: no longer usable, the survey keeps its look
    _as(client, ME, "Philipp")
    client.post(f"/designs/{did}/unshare", data={"orcid": OTHER})
    _as(client, OTHER, "Other")
    assert f'value="design:{did}"' not in client.get(f"/surveys/{sid}").data.decode()
    client.post(f"/surveys/{sid}/design", data={"reset": "1"})
    r = client.post(f"/surveys/{sid}/design/apply", data={"design": did}, follow_redirects=True).data.decode()
    assert "steht Ihnen nicht zur Verfügung" in r and _theme(client, sid)["colors"] == theme.STANDARD
    assert client.post(f"/designs/{did}/duplicate").status_code == 404


def test_a_design_saved_from_an_organisation_design_keeps_all_of_it(client, survey):
    """Ralf 2026-10-19: no reserved templates any more; a design saved from a survey with an organisation design (here
    the former RWTH template as one) keeps its tokens, and whoever it is shared with applies the same look."""
    with client.application.app_context():
        s = db.session.get(Survey, 1)
        s.theme = rwth_design.look()
        db.session.commit()
    client.post("/surveys/1/design/save", data={"name": "RWTH mit Logo"})
    with client.application.app_context():
        did = db.session.scalar(db.select(Design)).id
        assert db.session.get(Design, did).data["preset"] == "org"
    may_sign_in(client.application, OTHER)  # sharing needs someone who can sign in (H4)
    client.post(f"/designs/{did}/share", data={"orcid": OTHER})
    _as(client, OTHER, "Other")
    with client.application.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        one = db.session.get(Survey, 1)
        one.members.append(SurveyMember(orcid=OTHER, name="Other", user_id=user.id, role="editor"))
        db.session.commit()
    assert "RWTH mit Logo" in client.get("/designs/").data.decode()
    client.post("/surveys/1/design", data={"reset": "1"})
    client.post("/surveys/1/design/apply", data={"design": did})
    assert _theme(client)["preset"] == "org" and theme.tokens(_theme(client)) == theme.tokens(rwth_design.look())


def test_applying_is_for_the_draft_keeping_also_later(client, survey):
    did = _keep(client)
    client.post("/surveys/1/design", data={"reset": "1"})
    with client.application.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    before = _theme(client)
    assert "nur im Entwurf änderbar" in client.post("/surveys/1/design/apply", data={"design": did}, follow_redirects=True).data.decode()
    assert _theme(client) == before
    client.post("/surveys/1/design/save", data={"name": "Stand der Feldphase"})     # keeping changes nothing in the survey
    with client.application.app_context():
        assert db.session.scalar(db.select(Design).filter_by(name="Stand der Feldphase")) is not None
