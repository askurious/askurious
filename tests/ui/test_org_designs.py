"""Organisation designs instead of the RWTH tick (app/org_designs.py, konzept-org-designs-2026-10-19.md, Ralf
2026-10-19: A1, B1, C1, D1, E1). An organisation has at most one light and one dark organisation design, made by its
admins (a design file, or taken over from a template or a design); it is the standard for new surveys and every member
chooses it, a copy each time. Site admins may upload a template as a starting point. The RWTH templates are offered to
nobody any more; what has them keeps its look (migration 0091 turns it into the same organisation design)."""
import importlib.util
import io
import json
import pathlib

from tests.conftest import add
from tests.helpers import rwth_design

from app import org_designs, orgs, theme
from app.extensions import db
from app.models import Survey, User

OTHER = "0000-0002-1825-0097"
RWTH = rwth_design.COLOURS
RWTH_DARK = rwth_design.DARK_COLOURS


def _file(key, name=None):
    """The design file of a built-in template as it would be downloaded; "rwth"/"rwth-dark": the RWTH design files."""
    if key in ("rwth", "rwth-dark"):
        return rwth_design.file("light" if key == "rwth" else "dark")
    t = theme.BUILTIN[key]
    return theme.design_file(theme.as_org({**t.get("look", {}), "preset": key, "colors": t["colors"]}, name or t["name"]))


def _upload(client, url, data, mode="light", action="design_upload", name="design.json"):
    raw = json.dumps(data).encode() if not isinstance(data, bytes) else data
    form = {"mode": mode, "file": (io.BytesIO(raw), name)}
    if action:
        form["action"] = action
    return client.post(url, data=form, content_type="multipart/form-data", follow_redirects=True).data.decode()


def _main(client):
    with client.application.app_context():
        return orgs.default_id()


def _org(client):
    with client.application.app_context():
        org = orgs.get(orgs.default_id())
        return {m: org_designs.get(org, m) for m in org_designs.MODES} | {
            f"t_{m}": org_designs.get(org, m, template=True) for m in org_designs.MODES}


def _member(client):
    """The same browser signed in as a second researcher of the starting organisation, editor of survey 1."""
    client.get(f"/auth/dev-login?orcid={OTHER}&name=Other")
    client.post("/terms/", data={"accept": "1"})
    with client.application.app_context():
        from app.models import SurveyMember

        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        survey = db.session.get(Survey, 1)
        if not survey.member_of(user):
            survey.members.append(SurveyMember(orcid=OTHER, name="Other", user_id=user.id, role="editor"))
        db.session.commit()


def _theme(client, survey_id=1):
    with client.application.app_context():
        return theme.of(db.session.get(Survey, survey_id))


def test_a_design_file_keeps_known_colour_tokens_only():
    rwth = theme.read_design_file(_file("rwth"))
    assert rwth["preset"] == "org" and rwth["name"] == "RWTH Aachen" and theme.tokens(rwth)["--muted"] == "#646567"
    assert rwth["fixed"] == {"font": "grotesk", "radius": "square"} and rwth["logo_position"] == "right"
    assert theme.read_design_file(theme.design_file(rwth)) == rwth                   # download and upload again
    evil = {**_file("papier"), "tokens": {"--accent": "red; } body { display: none", "--bg": "url(x)", "--nope": "#ffffff",
                                          "--fg": "#123456", "--font": "#000000", "x": "#000000"},
            "swatches": ["#00549F", "javascript:x"], "fixed": {"font": "comic"}, "logo_position": "top"}
    t = theme.read_design_file(evil)
    assert t["tokens"] == {"--fg": "#123456"} and t["swatches"] == ["#00549f"] and "fixed" not in t and "logo_position" not in t
    assert ";" not in theme.css(t).split("{", 1)[1].replace("; ", "|").replace(";", "")  # every value a colour only
    assert theme.read_design_file({"format": "other", "colors": {}}) is None
    assert theme.read_design_file({**_file("papier"), "name": ""}) is None
    assert theme.read_design_file({**_file("papier"), "name": ""}, fallback_name="Datei") is not None


def test_organisation_admins_make_the_design_and_new_surveys_start_with_it(client, survey):
    org_id = _main(client)
    url = f"/org/{org_id}/design"
    page = client.get(url).data.decode()
    assert "Organisationsdesign" in page and "Standarddesign" not in page and "Noch keins." in page
    # a dark file for the light side, an unreadable one, something else
    assert "braucht einen hellen Hintergrund" in _upload(client, url, _file("rwth-dark"))
    weak = {**_file("papier"), "colors": {"accent": "#a4442a", "background": "#ffffff", "text": "#dddddd", "surface": "#ffffff"}}
    assert "Zu wenig Kontrast" in _upload(client, url, weak)
    assert "keine Designdatei" in _upload(client, url, b"<svg/>")
    assert _org(client)["light"] is None
    assert "gespeichert" in _upload(client, url, _file("rwth"))
    assert "gespeichert" in _upload(client, url, _file("rwth-dark"), mode="dark")
    got = _org(client)
    assert got["light"]["name"] == "RWTH Aachen" and got["dark"]["name"] == "RWTH Aachen dunkel"
    download = client.get(f"{url}/light.json")
    assert download.status_code == 200 and "attachment" in download.headers["Content-Disposition"]
    assert theme.read_design_file(json.loads(download.data)) == theme.read_design_file(_file("rwth"))
    # a new survey of the organisation starts with its light side, which stays light (Ralf, 2026-10-23)
    client.post("/surveys/new", data={"title": "Neu", "languages": ["de"], "default_language": "de"})
    with client.application.app_context():
        new = db.session.scalar(db.select(Survey).filter_by(title="Neu"))
        t = theme.of(new)
    assert t["preset"] == "org" and t["scheme"] == "fixed" and not theme.is_dark(t) and theme.css(t) == theme.css(rwth_design.pair())
    assert "prefers-color-scheme" not in theme.css(t)
    # removing it changes no survey
    before = theme.css(t)
    client.post(url, data={"action": "design_remove", "mode": "dark"})
    assert _org(client)["dark"] is None and theme.css(_theme(client, new.id)) == before


def test_taken_over_from_a_template_or_a_design(client, survey):
    url = f"/org/{_main(client)}/design"
    page = client.get(url).data.decode()
    assert 'value="builtin:papier"' in page and 'value="builtin:rwth"' not in page   # the RWTH templates: nobody
    assert "Bitte ein Farbschema" in client.post(url, data={"action": "design_take", "mode": "light", "source": "builtin:rwth"},
                                                 follow_redirects=True).data.decode()
    client.post(url, data={"action": "design_take", "mode": "light", "source": "builtin:salbei"})
    got = _org(client)["light"]
    assert got["name"] == "Salbei" and got["font"] == "rounded" and got["radius"] == "round"
    # a design of one's own, made in the design editor
    client.post("/surveys/1/design", data={**theme.STANDARD, "accent": "#8a1c3b", "font": "serif", "font_scale": "100",
                                           "radius": "square"})
    client.post("/surveys/1/design/save", data={"name": "Haus"})
    page = client.get(url).data.decode()
    key = page.split('value="design:')[1].split('"')[0]
    client.post(url, data={"action": "design_take", "mode": "light", "source": f"design:{key}"})
    got = _org(client)["light"]
    assert got["name"] == "Haus" and got["colors"]["accent"] == "#8a1c3b" and got["font"] == "serif" and "logo" not in got


def test_members_choose_it_but_change_only_their_copy(client, survey):
    org_id = _main(client)
    _upload(client, f"/org/{org_id}/design", _file("rwth"))
    _upload(client, f"/org/{org_id}/design", _file("rwth-dark"), mode="dark")
    _member(client)
    assert client.get(f"/org/{org_id}/design").status_code == 404                  # members do not manage it
    assert client.post(f"/org/{org_id}/design", data={"action": "design_remove", "mode": "light"}).status_code == 404
    page = client.get("/surveys/1/design").data.decode()
    assert f'value="org:{org_id}"' in page and "Organisationsdesign" in page and 'data-preset="rwth"' not in page
    assert f'value="org:{org_id}"' in client.get("/surveys/1").data.decode()       # in the settings too
    r = client.post("/surveys/1/design/choose", data={"preset": f"org:{org_id}", "back": "design"}, follow_redirects=True)
    assert "Organisationsdesign „RWTH Aachen“ übernommen" in r.data.decode()
    t = _theme(client)
    assert t["preset"] == "org" and t["scheme"] == "fixed" and t["org"] == org_id and not theme.is_dark(t)
    page = client.get("/surveys/1/editor/preview").data.decode()
    assert "--accent: #00549f" in page and "--radius: 2px" in page and "prefers-color-scheme: dark" not in page  # no dark side
    # the copy stays one while its colours stay (a font size changed), and becomes an own design with other colours
    client.post("/surveys/1/design", data={"preset": "org", **RWTH, **{f"dark_{k}": v for k, v in RWTH_DARK.items()},
                                           "scheme": "auto", "font_scale": "112"})
    t = _theme(client)
    assert t["preset"] == "org" and t["font_scale"] == 112 and "tokens_dark" not in t and t["font"] == "grotesk"
    client.post("/surveys/1/design", data={"preset": "org", **{**RWTH, "accent": "#cc071e"}, "font_scale": "100",
                                           "font": "serif", "radius": "soft"})
    t = _theme(client)
    assert t["preset"] == "custom" and "tokens" not in t and t["font"] == "serif"
    # the organisation design changes: the survey keeps its copy; outsiders cannot choose it
    with client.application.app_context():
        org_designs.put(orgs.get(org_id), "light", theme.read_design_file(_file("papier")))
        db.session.commit()
    assert _theme(client)["colors"]["accent"] == "#cc071e"
    with client.application.app_context():
        assert org_designs.chosen(db.session.scalar(db.select(User).filter_by(orcid=OTHER)), "org:999") is None


def test_the_site_admins_template_is_a_starting_point_only(client, survey):
    org_id = _main(client)
    url = f"/admin/orgs/{org_id}/template"
    page = client.get(f"/admin/orgs/{org_id}").data.decode()
    assert "Design der Einrichtung" in page and 'name="rwth"' not in page and "Keine Vorlage." in page
    assert 'name="rwth"' not in client.get("/admin/orgs").data.decode()              # no tick "RWTH" any more
    # without an organisation design the template becomes it
    assert "als Organisationsdesign übernommen" in _upload(client, url, _file("rwth"), action=None)
    got = _org(client)
    assert got["t_light"]["name"] == "RWTH Aachen" and got["light"]["name"] == "RWTH Aachen"
    # the admins of the organisation change it; a new template does not replace their work
    client.post(f"/org/{org_id}/design", data={"action": "design_take", "mode": "light", "source": "builtin:papier"})
    assert "Die Organisation hat schon ein eigenes Design" in _upload(client, url, _file("salbei"), action=None)
    got = _org(client)
    assert got["light"]["name"] == "Papier" and got["t_light"]["name"] == "Salbei"
    page = client.get(f"/org/{org_id}/design").data.decode()
    assert "Vorlage übernehmen" in page
    client.post(f"/org/{org_id}/design", data={"action": "design_template"})
    assert _org(client)["light"]["name"] == "Salbei"
    assert "Vorlage übernehmen" not in client.get(f"/org/{org_id}/design").data.decode()
    assert client.get(f"/admin/orgs/{org_id}/template/light.json").status_code == 200
    client.post(url, data={"action": "remove", "mode": "light"})
    assert _org(client)["t_light"] is None and _org(client)["light"]["name"] == "Salbei"
    # only for site admins
    _member(client)
    assert client.get(f"/admin/orgs/{org_id}").status_code == 403
    assert _upload(client, url, _file("rwth"), action=None) and _org(client)["t_light"] is None


def test_colour_fields_offer_the_colours_of_the_design(client, survey):
    add(client, "map_marker")
    assert "theme-colours" not in client.get("/surveys/1/editor/items/2?lang=de").data.decode()
    org_id = _main(client)
    _upload(client, f"/org/{org_id}/design", _file("rwth"))
    client.post("/surveys/1/design/choose", data={"preset": f"org:{org_id}"})
    editor = client.get("/surveys/1/editor/items/2?lang=de").data.decode()
    assert 'list="theme-colours"' in editor and '<option value="#cc071e">' in editor and editor.count('<option value="#') >= 13


def test_a_fixed_font_is_shown_as_fixed(client, survey):
    org_id = _main(client)
    _upload(client, f"/org/{org_id}/design", _file("rwth"))
    client.post("/surveys/1/design/choose", data={"preset": f"org:{org_id}"})
    page = client.get("/surveys/1/design").data.decode()
    assert '<select name="font" data-design-field data-fixable disabled>' in page and "lassen sich nicht ändern" in page
    assert 'data-preset="org"' in page and 'data-logo-position="right"' in page
    r = client.post("/surveys/1/design", data={"preset": "org", **RWTH, "font_scale": "100", "font": "atkinson"},
                    follow_redirects=True).data.decode()
    assert "Nicht übernommen: Schrift" in r and _theme(client)["font"] == "grotesk"


def test_migration_0090_fills_the_organisation_designs_with_the_same_values():
    path = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0090_org_designs.py"
    spec = importlib.util.spec_from_file_location("m0090", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.ROLLING is True
    for key in theme.BUILTIN:
        t = theme.BUILTIN[key]
        assert theme.clean(m.as_org(key)) == theme.as_org({**t.get("look", {}), "preset": key, "colors": t["colors"]}, t["name"]), key
    stored = {"preset": "rwth", "colors": RWTH, "scheme": "auto", "colors_dark": RWTH_DARK,
              "logo": {"file": "abcdefgh12345678.png", "w": 3, "h": 1}}
    new = theme.clean(m.convert_rwth(json.dumps(stored)))
    assert new["preset"] == "org" and theme.css(new) == theme.css(rwth_design.pair()) and new["logo"]["position"] == "right"
    assert m.convert_rwth({"preset": "papier"}) is None
    design = m.from_design({"preset": "custom", "colors": {**theme.STANDARD, "accent": "#8a1c3b"}, "font": "serif",
                            "logo": {"file": "x"}})
    assert design["preset"] == "org" and "logo" not in design and design["font"] == "serif"


def test_the_standard_tokens_are_read_from_the_minified_style_sheet_too():
    """The Docker image serves participant.css minified (tools/build_static.py); reading its token block failed there
    and every page that checks an organisation design answered 500 (deploy of 2026-10-19)."""
    import re

    path = pathlib.Path(theme.__file__).parent / "static" / "participant.css"
    source = path.read_text(encoding="utf-8")
    minified = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    minified = re.sub(r"\s*([{};:,])\s*", r"\1", minified).replace(";}", "}")
    assert "body.participant,.participant-frame{" in minified
    plain = {k: re.sub(r"\s*([,:])\s*", r"\1", v) for k, v in theme.tokens_of_css(source).items()}
    assert theme.tokens_of_css(minified) == plain and theme.tokens_of_css(source) == theme.standard_tokens()
    assert len(theme.standard_tokens()) > 30


def test_organisation_admins_edit_the_design_in_the_design_editor(client, survey):
    """Ralf 2026-10-19: "Kann der Organisationsadmin das Design auch editieren?" The editor of designs opens on one side
    of the organisation design; saving keeps its name, refuses a side of the wrong brightness and changes no survey."""
    org_id = _main(client)
    page = client.get(f"/org/{org_id}/design").data.decode()
    assert f'href="/org/{org_id}/design/light"' in page and "Im Designeditor erstellen" in page
    editor = client.get(f"/org/{org_id}/design/light").data.decode()
    assert "Organisationsdesign (hell)" in editor and 'id="logo"' not in editor and 'name="scheme" value="fixed"' in editor
    assert client.get(f"/org/{org_id}/design/light/sample?accent=%23a4442a&background=%23ffffff&text=%23111111"
                      "&surface=%23ffffff").status_code == 200
    _upload(client, f"/org/{org_id}/design", _file("rwth"))
    client.post("/surveys/1/design/choose", data={"preset": f"org:{org_id}"})
    form = {**RWTH, "accent": "#8a1c3b", "font_scale": "112", "font": "serif", "radius": "soft", "preset": "custom"}
    r = client.post(f"/org/{org_id}/design/light", data=form, follow_redirects=True).data.decode()
    assert "Organisationsdesign (hell) gespeichert" in r
    got = _org(client)["light"]
    assert got["name"] == "RWTH Aachen" and got["colors"]["accent"] == "#8a1c3b" and got["font_scale"] == 112
    assert _theme(client)["colors"]["accent"] == "#00549f"                         # the survey keeps its copy
    dark = {**RWTH_DARK, "font_scale": "100", "preset": "custom"}
    assert "braucht einen hellen Hintergrund" in client.post(f"/org/{org_id}/design/light", data=dark,
                                                             follow_redirects=True).data.decode()
    _member(client)
    assert client.get(f"/org/{org_id}/design/light").status_code == 404
    assert client.post(f"/org/{org_id}/design/light", data=form).status_code == 404


def test_the_pages_of_an_organisation_and_of_the_experiments_come_in_blocks(client, survey):
    """Ralf 2026-10-19: the page of an organisation in blocks, "Design der Einrichtung" last; one block per experimental
    feature; the buttons of the design next to their fields."""
    org_id = _main(client)
    page = client.get(f"/admin/orgs/{org_id}").data.decode()
    order = [page.index(f'id="{key}"') for key in ("admins", "grenzen", "design")]
    assert order == sorted(order) and page.count('class="panel org-page-part"') == 3
    assert 'class="row field-with-button"><input type="file" id="template-file-light"' in page
    design = client.get(f"/org/{org_id}/design").data.decode()
    assert 'class="row field-with-button"><select id="org-design-source-light"' in design
    experiments = client.get("/admin/experimental").data.decode()
    assert experiments.count('<section class="panel exp-feature"') >= 2


def _migration(name):
    path = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(name[:4], path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_migration_0091_turns_every_stored_rwth_theme_into_the_same_organisation_design():
    """Surveys, designs and the states of "Rückgängig" that named "rwth"/"rwth-dark" keep their look exactly: the CSS
    of the converted theme is that of the RWTH design files. Then the tick and the old grants go (DROPS)."""
    m = _migration("0091_drop_rwth_designs.py")
    assert m.ROLLING is False and m.DROPS_UNUSED_SINCE == "0090"
    assert set(m.DROPS) == {"organisation.themes", "organisation.theme_light", "organisation.theme_dark", "theme_grant",
                            "theme_group_grant"}
    auto = {"preset": "rwth", "colors": RWTH, "scheme": "auto", "colors_dark": RWTH_DARK, "font_scale": 112,
            "logo": {"file": "abcdefgh12345678.png", "w": 3, "h": 1, "position": "left"}}
    step = {"parts": {"settings:theme": [{"theme": auto}, {"theme": {"preset": "rwth-dark", "colors": RWTH_DARK}}]}}
    doc, changed = m.convert(json.loads(json.dumps(step)))
    assert changed
    before, after = (theme.clean(p["theme"]) for p in doc["parts"]["settings:theme"])
    assert before["preset"] == "org" and before["font_scale"] == 112 and before["logo"]["position"] == "right"
    assert theme.css({**before, "logo": None}) == theme.css(theme.clean({**rwth_design.pair(), "font_scale": 112}))
    assert theme.css(after) == theme.css(rwth_design.look("dark"))
    assert m.convert({"preset": "papier", "colors": {}}) == ({"preset": "papier", "colors": {}}, False)


def test_the_light_and_the_dark_organisation_design_are_both_offered(client, survey):
    """Ralf, 2026-10-23: only one of the two organisation designs showed, and on a device set to dark it looked dark
    without saying so. Both are offered, each named; light stays light, dark stays dark, nothing follows the device."""
    org_id = _main(client)
    _upload(client, f"/org/{org_id}/design", _file("rwth"))
    _upload(client, f"/org/{org_id}/design", _file("rwth-dark"), mode="dark")
    _member(client)
    page = client.get("/surveys/1").data.decode()
    assert f'value="org:{org_id}"' in page and f'value="org:{org_id}:dark"' in page and f'value="org:{org_id}:light"' not in page
    assert "nach der Einstellung des Geräts" not in page and "automatisch" not in page.split('class="theme-choice-list"')[1].split("</fieldset>")[0]
    client.post("/surveys/1/design/choose", data={"preset": f"org:{org_id}:dark"})
    t = _theme(client)
    assert t["preset"] == "org" and t["org"] == org_id and t["scheme"] == "fixed" and theme.is_dark(t)
    page = client.get("/surveys/1").data.decode()
    assert f'value="org:{org_id}:dark" checked' in page and f'value="org:{org_id}" checked' not in page
    # the light one stays light on a device set to dark too: fixed, no dark side in the CSS
    client.post("/surveys/1/design/choose", data={"preset": f"org:{org_id}"})
    t = _theme(client)
    assert t["scheme"] == "fixed" and not theme.is_dark(t) and "prefers-color-scheme" not in theme.css(t)
    page = client.get("/surveys/1").data.decode()
    assert f'value="org:{org_id}" checked' in page and f'value="org:{org_id}:dark" checked' not in page
    # a survey that still carries the former pair (light with dark for devices set to dark) shows light
    old = theme.clean({**t, "scheme": "auto", "colors_dark": theme.clean(rwth_design.look("dark"))["colors"]})
    assert old["scheme"] == "fixed" and "prefers-color-scheme" not in theme.css(old)
    # the summary line of "Erscheinungsbild" names the colour scheme only, no image style
    from app import settings_page

    with client.application.test_request_context():
        got = settings_page.basic_summaries(db.session.get(Survey, 1))["gestaltung"]
        assert [text for text, _strong in got] == ["RWTH Aachen"]
