"""UI/UX audit of 2026-10-07, proposal S10 (first part): the administration's tab "Personen". Researchers, the
allowed ORCID iDs, the groups and the institution design are panels of one page; "Anmeldung freischalten" and "Konto
anlegen" are one button "Person hinzufügen …" with a dialog of two ways."""
import re
from pathlib import Path

from app.extensions import db
from app.models import User, WorkGroup, WorkGroupMember

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")


def _group(app, name="AG Test"):
    with app.app_context():
        group = WorkGroup(name=name)
        db.session.add(group)
        db.session.flush()
        db.session.add(WorkGroupMember(group_id=group.id, orcid="0000-0002-1825-0097", role="lead", name="Leitung"))
        db.session.commit()
        return group.id


def test_one_tab_people_gathers_the_panels_in_order(client):
    page = client.get("/admin/people").data.decode()
    tabs = page[page.index('class="admin-tabs"'):].split("</nav>", 1)[0]
    assert re.search(r'aria-current="page"[^>]*>Personen</a>', tabs)
    assert "/admin/templates" not in tabs and ">Einrichtungen<" not in tabs
    for other in ("Gruppen", "Befragungen", "Rückmeldungen", "Konstrukte", "Experimentell", "KI-Nutzung", "Protokoll", "Server"):
        assert f">{other}" in tabs  # the other tabs stay as they are
    assert "<title>Personen · Administration" in page
    order = [page.index(f'id="{anchor}"') for anchor in
             ("forschende", "freischalten", "person-hinzufuegen")]
    assert order == sorted(order) and 'id="gruppen"' not in page   # the groups have their own tab (2026-10-10)
    groups = client.get("/admin/groups").data.decode()
    assert re.search(r'aria-current="page"[^>]*>Gruppen</a>', groups) and 'id="gruppen"' in groups
    assert "<title>Gruppen · Administration" in groups and 'id="forschende"' not in groups
    assert ">Freigeschaltete ORCID iDs <" in page and 'id="zugang"' not in page
    # the former institution design: since 2026-10-19 the template of an organisation's design on its page
    assert client.get("/admin/designs").headers["Location"].endswith("/admin/orgs")
    design = client.get("/admin/orgs").data.decode()
    assert re.search(r'aria-current="page"[^>]*>Organisationen</a>', design) and "admin/designs" not in design
    assert 'class="org-design"' not in design and 'id="design"' not in design
    assert "Einzelne Personen" not in design and 'id="design-gruppen"' not in design


def test_the_button_opens_the_dialog_and_leads_to_the_panel_without_scripts(client):
    page = client.get("/admin/people").data.decode()
    head = page[page.index('class="admin-listhead"'):].split("</div>", 1)[0]
    assert 'class="list-search"' in head
    assert '<a class="button" href="#person-hinzufuegen" data-person-add aria-haspopup="dialog">' in head
    assert "Person hinzufügen …" in head
    panel = page[page.index('id="person-hinzufuegen"'):]
    assert panel.startswith('id="person-hinzufuegen" aria-labelledby="person-add-title" data-person-add-panel>')
    assert 'data-person-add-close hidden aria-label="Schließen"' in panel
    assert ">Mit ORCID iD<" in panel and ">Ohne ORCID iD<" in panel
    assert panel.index('action="/admin/admit"') < panel.index('action="/admin/accounts/new"')
    assert "Anmeldung freischalten</button>" in panel and "Konto anlegen</button>" in panel
    # the list of allowed iDs has no form of its own any more, only the switch "every ORCID holder"
    allowed = page[page.index('id="freischalten"'):].split("</section>", 1)[0]
    assert 'name="orcid" maxlength' not in allowed and 'name="mode" value="all"' in allowed
    # the dialog: Esc and the focus are the browser's and app.js's (S10)
    block = JS[JS.index("[data-person-add-panel]") - 200:]
    block = block[:block.index("})();")]
    assert 'document.createElement("dialog")' in block and "showModal()" in block
    assert 'addEventListener("close", function () { open.focus(); })' in block
    assert 'input:not([type=hidden])' in block and "[aria-invalid=true]" in block and '"#" + dialog.id' in block
    assert ".person-ways { display: grid; grid-template-columns: 1fr 1fr;" in CSS
    assert "@media (max-width: 720px) { .person-ways { grid-template-columns: 1fr; }" in CSS


def test_a_refused_account_comes_back_in_the_dialog_with_the_field_marked(client):
    assert client.post("/admin/accounts/new", data={"name": "Gast", "email": "gast@example.org"}).status_code == 302
    r = client.post("/admin/accounts/new", data={"name": "Zweiter", "email": "gast@example.org", "affiliation": "Uni"})
    page = r.data.decode()
    assert r.status_code == 422
    panel = page[page.index('id="person-hinzufuegen"'):].split('id="gruppen"', 1)[0]
    assert '<div id="new-account-error">' in panel and "Mit dieser E-Mail-Adresse gibt es schon ein Konto." in panel
    assert 'value="Zweiter"' in panel
    assert 'value="gast@example.org" aria-invalid="true" aria-describedby="new-account-error"' in panel
    assert panel.count("aria-invalid") == 1
    r = client.post("/admin/accounts/new", data={"name": "", "email": "x@example.org"})
    assert 'required value="" aria-invalid="true" aria-describedby="new-account-error"' in r.data.decode()


def test_a_created_account_still_shows_its_one_time_link_once(client):
    r = client.post("/admin/accounts/new", data={"name": "Gast", "email": "gast@example.org"})
    assert r.status_code == 302 and "/admin/accounts/" in r.headers["Location"]
    page = client.get(r.headers["Location"]).data.decode()
    assert re.search(r"http://[^\"<]+/password/[A-Za-z0-9_-]{20,}", page)
    assert "Kopieren" in page
    # UI audit 2026-10-07, W103: the address button beside its field, the link a paragraph of its own
    form = page[page.index('class="account-form"'):].split("</form>", 1)[0]
    assert 'class="row field-with-button"' in form and "Adresse ändern" in form and "Einmal-Link erzeugen" not in form
    assert '<h3 class="account-link-head">Einmal-Link</h3>' in page


def test_old_addresses_and_actions_land_on_the_panels_of_people(app, client):
    assert client.get("/admin/templates").headers["Location"].endswith("/admin/orgs")
    gid = _group(app)
    r = client.post(f"/admin/groups/{gid}", data={"name": "AG Neu"})
    assert r.headers["Location"].endswith("/admin/groups#gruppen")
    page = client.get("/admin/groups").data.decode()
    assert 'value="AG Neu"' in page and "Design der Einrichtung" in client.get("/admin/orgs/1").data.decode()


def test_people_tab_in_english(client):
    client.post("/account/language", data={"lang": "en"})
    page = client.get("/admin/people").data.decode()
    assert "<title>People · Administration" in page and ">Allowed ORCID iDs <" in page
    assert ">With an ORCID iD<" in page and ">Without an ORCID iD<" in page
    assert "Institution design" in client.get("/admin/orgs/1").data.decode()
    groups = client.get("/admin/groups").data.decode()
    assert "<title>Groups · Administration" in groups and ">Groups</a>" in groups
    assert "Gruppen" not in groups.split('id="gruppen"', 1)[1].split("</section>", 1)[0]
