"""The editor of a design: the page "Erscheinungsbild" without a survey. Same form, live preview, logo and partner
logos; what is saved changes the design, never a survey that already uses it."""
import io
import os

from PIL import Image

from app import theme
from app.extensions import db
from app.models import Design, Survey
from tests.conftest import allow, may_sign_in

OTHER = "0000-0002-1825-0097"
WINE = {"accent": "#8a1c3b", "background": "#fffaf5", "text": "#221a1a", "surface": "#ffffff"}
FORM = {"font": "serif", "font_scale": "112", "radius": "round"}


def png(width=200, height=200):
    out = io.BytesIO()
    Image.new("RGBA", (width, height), (138, 28, 59, 255)).save(out, format="PNG")
    return out.getvalue()


def new_design(client, name="Lehrstuhl"):
    r = client.post("/designs/new", data={"name": name})
    assert r.status_code == 302 and "/designs/" in r.headers["Location"]
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def data_of(client, did):
    with client.application.app_context():
        return db.session.get(Design, did).data


def test_a_design_is_made_and_shaped_without_a_survey(client):
    did = new_design(client)
    assert data_of(client, did) == theme.standard()
    page = client.get(f"/designs/{did}").data.decode()
    assert "Design „Lehrstuhl“" in page and "data-design-form" in page and f'action="/designs/{did}"' in page
    assert f'data-sample="/designs/{did}/sample"' in page and 'data-preset="askurious-dark"' in page
    assert "survey-tabs" not in page and "Als Design speichern" not in page          # nothing of a survey on this page
    r = client.post(f"/designs/{did}", data={**WINE, **FORM, "scheme": "auto"}, follow_redirects=True).data.decode()
    assert "Design gespeichert" in r
    kept = data_of(client, did)
    assert kept["colors"] == WINE and (kept["font"], kept["font_scale"], kept["radius"], kept["scheme"]) == ("serif", 112, "round", "auto")
    # live preview and contrast for the design
    sample = client.get(f"/designs/{did}/sample?accent=%2300549f&background=%23000000&text=%23ffffff&surface=%23000000").data.decode()
    assert "--bg: #000000" in sample and "Beispielseite" in sample
    saved = client.get(f"/designs/{did}/sample").data.decode()
    assert "--accent: #8a1c3b" in saved and "@media (prefers-color-scheme: dark)" in saved
    check = client.get(f"/designs/{did}/check?accent=%23ffed00&background=%23ffffff&text=%231d1d1b&surface=%23ffffff").get_json()
    assert any(not c["ok"] for c in check["checks"])
    # back to the standard colours
    client.post(f"/designs/{did}", data={"reset": "1"})
    assert data_of(client, did) == theme.standard()
    assert "Bearbeiten" in client.get("/designs/").data.decode()


def test_logo_and_partner_logos_of_a_design(client, survey):
    did = new_design(client)
    folder = os.path.join(client.application.config["UPLOAD_FOLDER"], f"_design_{did}")
    r = client.post(f"/designs/{did}/logo", data={"file": (io.BytesIO(png()), "l.png"), "variant": "light", "rights": "1"},
                    content_type="multipart/form-data", follow_redirects=True).data.decode()
    assert "Logo hochgeladen" in r
    logo = data_of(client, did)["logo"]
    assert os.path.isfile(os.path.join(folder, logo["file"])) and os.path.isfile(os.path.join(folder, logo["icon"]))
    assert f"/media/_design_{did}/{logo['file']}" in client.get(f"/designs/{did}").data.decode()
    assert client.get(f"/designs/{did}/logo/colours").get_json()["colours"][0]["colour"] == "#8a1c3b"
    client.post(f"/designs/{did}", data={**theme.STANDARD, "font": "system", "font_scale": "100", "radius": "soft",
                                         "logo_fields": "1", "logo_alt_de": "Lehrstuhl", "logo_alt_en": "Chair",
                                         "logo_position": "center", "logo_size": "l"})
    logo = data_of(client, did)["logo"]
    assert logo["alt"] == {"de": "Lehrstuhl", "en": "Chair"} and (logo["position"], logo["size"]) == ("center", "l")
    r = client.post(f"/designs/{did}/partner", data={"file": (io.BytesIO(png(320, 80)), "p.png"), "alt": "DFG", "rights": "1"},
                    content_type="multipart/form-data", follow_redirects=True).data.decode()
    assert "Partnerlogo hinzugefügt" in r and data_of(client, did)["partners"][0]["alt"] == "DFG"
    # a survey that takes the design gets copies; changing the design afterwards does not change the survey
    client.post("/surveys/1/design/choose", data={"preset": f"design:{did}"})
    with client.application.app_context():
        taken = theme.clean(db.session.get(Survey, 1).theme)
    assert taken["logo"]["alt"]["de"] == "Lehrstuhl" and taken["partners"][0]["alt"] == "DFG"
    client.post(f"/designs/{did}/logo/remove", data={"variant": "light"})
    client.post(f"/designs/{did}/partner", data={"remove": "0"})
    client.post(f"/designs/{did}", data={**WINE, **FORM})
    kept = data_of(client, did)
    assert "logo" not in kept and "partners" not in kept and kept["colors"] == WINE
    assert not os.path.exists(os.path.join(folder, logo["file"])) and not os.path.exists(os.path.join(folder, logo["icon"]))
    with client.application.app_context():
        s = db.session.get(Survey, 1)
        assert theme.clean(s.theme) == taken
        assert os.path.isfile(os.path.join(client.application.config["UPLOAD_FOLDER"], s.uid, taken["logo"]["file"]))


def test_only_the_owner_edits_and_reserved_templates_stay_reserved(client):
    did = new_design(client)
    rwth = {"accent": "#00549f", "background": "#ffffff", "text": "#000000", "surface": "#ffffff"}
    may_sign_in(client.application, OTHER)  # sharing needs someone who can sign in (H4)
    client.post(f"/designs/{did}/share", data={"orcid": OTHER})
    client.get(f"/auth/dev-login?orcid={OTHER}&name=Other")
    client.post("/terms/", data={"accept": "1"})
    for path in (f"/designs/{did}", f"/designs/{did}/sample", f"/designs/{did}/check", f"/designs/{did}/logo/colours"):
        assert client.get(path).status_code == 404, path
    assert client.post(f"/designs/{did}", data={**WINE, **FORM}).status_code == 404
    assert client.post(f"/designs/{did}/logo/remove", data={"variant": "light"}).status_code == 404
    assert data_of(client, did) == theme.standard()
    # an own design of someone who may create surveys, and so designs: the RWTH template is offered to nobody
    allow(client.application, OTHER)
    mine = new_design(client, "Meins")
    page = client.get(f"/designs/{mine}").data.decode()
    assert 'data-preset="rwth"' not in page
    r = client.post(f"/designs/{mine}", data={**rwth, "preset": "rwth", "font_scale": "100"}, follow_redirects=True).data.decode()
    assert data_of(client, mine)["preset"] == "custom"  # no template "rwth" any more: its colours are an own design
    r = client.post(f"/designs/{mine}", data={**rwth, "font": "atkinson", "font_scale": "100", "radius": "soft"}, follow_redirects=True).data.decode()
    assert "Design gespeichert" in r and data_of(client, mine)["preset"] == "custom"   # the colours alone are an own design


def test_the_tile_of_a_design_shows_the_sample_page_in_its_look(client):
    did = new_design(client)
    client.post(f"/designs/{did}", data={**WINE, "font": "atkinson", "font_scale": "100", "radius": "square"})
    may_sign_in(client.application, OTHER)  # sharing needs someone who can sign in (H4)
    client.post(f"/designs/{did}/share", data={"orcid": OTHER})
    page = client.get("/designs/").data.decode()
    tile = page[page.index(f'id="design-{did}"'):]
    tile = tile[:tile.index("</article>")]
    assert ">Aa<" not in tile and f'<iframe src="/designs/{did}/look"' in tile and 'tabindex="-1"' in tile
    look = client.get(f"/designs/{did}/look?accent=%23000000&background=%23000000&text=%23ffffff&surface=%23000000").data.decode()
    assert "Beispielseite" in look and "--accent: #8a1c3b" in look and "Atkinson Hyperlegible Next" in look   # stored look, no query
    assert "--radius: 2px" in look and "has-error" not in look   # nothing the page would scroll to
    assert "has-error" in client.get(f"/designs/{did}/sample").data.decode()
    assert client.get("/designs/99999/look").status_code == 404
    # someone the design is shared with sees the same view; a stranger does not
    client.get(f"/auth/dev-login?orcid={OTHER}&name=Other")
    client.post("/terms/", data={"accept": "1"})
    assert f'<iframe src="/designs/{did}/look"' in client.get("/designs/").data.decode()
    assert "--accent: #8a1c3b" in client.get(f"/designs/{did}/look").data.decode()
    client.get("/auth/dev-login?orcid=0000-0000-0000-0124&name=Stranger")
    client.post("/terms/", data={"accept": "1"})
    assert client.get(f"/designs/{did}/look").status_code == 404
