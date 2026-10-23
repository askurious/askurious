"""The news page /blog (app/blog.py; blog-konzept.md, Ralf 2026-10-10: "wie empfohlen")."""
import io
import os
import xml.etree.ElementTree as ET

from app.extensions import db
from app.models import BlogImage, BlogPost, BlogSlug, User


def _png(size=(40, 30), color=(200, 30, 30)):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    out.seek(0)
    return out


def _new(client, **fields):
    """A new entry with these fields saved (as the edit page sends them); returns its id."""
    r = client.post("/admin/blog/new")
    assert r.status_code == 302
    post_id = int(r.headers["Location"].rstrip("/").rsplit("/", 1)[1])
    data = {"title_de": "", "title_en": "", "summary_de": "", "summary_en": "", "body_de": "", "body_en": "",
            "category": "feature", **fields}
    assert client.post(f"/admin/blog/{post_id}", data=data).status_code == 302
    return post_id


def _save(client, post_id, **fields):
    with client.application.app_context():
        p = db.session.get(BlogPost, post_id)
        data = {"title_de": p.title_de, "title_en": p.title_en, "summary_de": p.summary_de, "summary_en": p.summary_en,
                "body_de": p.body_de, "body_en": p.body_en, "category": p.category}
    data.update(fields)
    return client.post(f"/admin/blog/{post_id}", data=data)


def _guest(app):
    return app.test_client()


def _researcher(app, orcid="0000-0001-5555-5555"):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={orcid}")
    c.post("/terms/", data={"accept": "1"})
    return c


def test_admin_writes_publishes_and_everyone_reads(app, client):
    guest = _guest(app)
    assert "Neuigkeiten" not in guest.get("/").data.decode()       # nothing published: no link, no section
    pid = _new(client, title_de="Neue Karte für Marker", summary_de="Marker setzen auf der Karte.",
               body_de="**Was ist neu**\n\nMan kann *Marker* setzen.\n\n- eins\n- zwei")
    with app.app_context():
        assert db.session.get(BlogPost, pid).slug == "neue-karte-fuer-marker"   # follows the German title
    assert guest.get("/blog/neue-karte-fuer-marker").status_code == 404          # a draft is not found
    assert _researcher(app).get("/blog/neue-karte-fuer-marker").status_code == 404
    preview = client.get("/blog/neue-karte-fuer-marker").data.decode()           # admins see the draft
    assert "Entwurf: nur für Admins sichtbar." in preview and 'name="robots" content="noindex"' in preview
    assert client.post(f"/admin/blog/{pid}/publish").status_code == 302
    page = guest.get("/blog/neue-karte-fuer-marker").data.decode()
    assert "<h1>Neue Karte für Marker</h1>" in page and "<h3>Was ist neu</h3>" in page and "<em>Marker</em>" in page
    assert "<ul><li>eins</li><li>zwei</li></ul>" in page and "Entwurf" not in page and "Bearbeiten" not in page
    assert 'property="og:title" content="Neue Karte für Marker"' in page
    listing = guest.get("/blog/").data.decode()
    assert "Neue Karte für Marker" in listing and "Marker setzen auf der Karte." in listing
    home = guest.get("/").data.decode()
    # the start page's section links the overview (the footer has no news link since 2026-10-15, 2d21b6d1)
    assert 'href="/blog/">Alle Neuigkeiten</a>' in home and "Neu in Askurious" in home and "Neue Karte für Marker" in home
    # withdrawn: a draft again, gone for everyone else
    client.post(f"/admin/blog/{pid}/withdraw")
    assert guest.get("/blog/neue-karte-fuer-marker").status_code == 404
    home = guest.get("/").data.decode()
    assert "Neuigkeiten</a>" not in home and "Neue Karte für Marker" not in home


def test_only_admins_reach_the_administration(app, client):
    other = _researcher(app)
    pid = _new(client, title_de="X", body_de="Y")
    for url in ("/admin/blog", f"/admin/blog/{pid}"):
        assert other.get(url).status_code == 403
    for url in ("/admin/blog/new", f"/admin/blog/{pid}", f"/admin/blog/{pid}/publish", f"/admin/blog/{pid}/delete"):
        assert other.post(url, data={"title_de": "gekapert"}).status_code == 403
    assert _guest(app).get("/admin/blog").status_code in (302, 401)
    admin_page = client.get("/admin/blog").data.decode()
    assert 'href="/admin/blog"' in admin_page and "Neuer Eintrag" in admin_page and "Entwürfe" in admin_page


def test_publish_needs_german_text_and_alternative_texts(app, client):
    pid = _new(client, title_en="Only English", body_en="Text")
    r = client.post(f"/admin/blog/{pid}/publish", follow_redirects=True).data.decode()
    assert "Noch nicht veröffentlicht" in r and "ein deutscher Titel" in r and "ein deutscher Text" in r
    _save(client, pid, title_de="Bilder", body_de="Vorher\n\n{{bild1}}")
    client.post(f"/admin/blog/{pid}/images", data={"image": (_png(), "a.png")}, content_type="multipart/form-data")
    with app.app_context():
        img = db.session.scalars(db.select(BlogImage)).one()
        assert img.filename.endswith(".webp") and (img.width, img.height) == (40, 30)
    _save(client, pid, body_de=f"Vorher\n\n{{{{bild{img.id}}}}}")
    r = client.post(f"/admin/blog/{pid}/publish", follow_redirects=True).data.decode()
    assert "ein Alternativtext für jedes gezeigte Bild" in r
    _save(client, pid, **{f"alt_de_{img.id}": "Rotes Rechteck", f"alt_en_{img.id}": "Red rectangle"})
    client.post(f"/admin/blog/{pid}/publish")
    with app.app_context():
        assert db.session.get(BlogPost, pid).status == "published"


def test_images_are_webp_without_metadata_and_served_only_when_public(app, client):
    from PIL import Image

    pid = _new(client, title_de="Bild", body_de="Text")
    r = client.post(f"/admin/blog/{pid}/images", data={"image": (_png((3000, 1500)), "big.png"), "alt_de": "Groß",
                                                     "as_cover": "1"}, content_type="multipart/form-data")
    assert r.status_code == 302
    with app.app_context():
        img = db.session.scalars(db.select(BlogImage)).one()
        post = db.session.get(BlogPost, pid)
        assert post.cover_image_id == img.id and img.alt_de == "Groß" and img.width == 2400
        name = img.filename
    url = f"/blog/media/{pid}/{name}"
    assert _guest(app).get(url).status_code == 404                              # the entry is a draft
    data = client.get(url).data
    shown = Image.open(io.BytesIO(data))
    assert shown.format == "WEBP" and shown.size == (2400, 1200) and not shown.info.get("exif")
    client.post(f"/admin/blog/{pid}/publish")
    assert _guest(app).get(url).status_code == 200
    assert _guest(app).get(f"/blog/media/{pid}/../../x.webp").status_code == 404
    # not an image
    r = client.post(f"/admin/blog/{pid}/images", data={"image": (io.BytesIO(b"<svg></svg>"), "x.svg")},
                    content_type="multipart/form-data", follow_redirects=True).data.decode()
    assert "Das Bild konnte nicht gelesen werden." in r
    # removing the image
    with app.app_context():
        img_id = db.session.scalars(db.select(BlogImage.id)).one()
    client.post(f"/admin/blog/{pid}/images/{img_id}/delete")
    with app.app_context():
        assert db.session.get(BlogPost, pid).cover_image_id is None and not db.session.scalars(db.select(BlogImage)).all()
    assert _guest(app).get(url).status_code == 404


def test_text_links_images_and_nothing_unsafe(app, client):
    from app import blog

    pid = _new(client, title_de="Links", body_de="x")
    with app.test_request_context("/"):
        post = db.session.get(BlogPost, pid)
        post.images.append(BlogImage(filename="abcdefghijklmnop.webp", width=10, height=5, alt_de="Ein <Bild>"))
        db.session.commit()
        img_id = post.images[0].id
        text = (f"Siehe [die *Doku*](https://example.org/a~b~c) und https://askurious.de/blog/x.\n\n"
                f"{{{{bild{img_id}}}}}\n\nInline {{{{bild{img_id}}}}} hier. <script>alert(1)</script> "
                f"[böse](javascript:alert(1)) [lokal](/pricing) `code` {{marke}}")
        html = str(blog.render(text, post, "de"))
    assert '<a href="https://example.org/a~b~c" target="_blank" rel="noopener">die <em>Doku</em></a>' in html
    assert '<a href="https://askurious.de/blog/x" target="_blank" rel="noopener">https://askurious.de/blog/x</a>.' in html
    assert f'<figure class="blog-figure"><img src="/blog/media/{pid}/abcdefghijklmnop.webp" alt="Ein &lt;Bild&gt;"' in html
    assert html.count("<img") == 2 and "<p>Inline <img" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'href="javascript' not in html and '<a href="/pricing">lokal</a>' in html
    assert "<code>code</code>" in html and "Askurious" in html and "{marke}" not in html


def test_images_at_the_left_or_right_edge(app, client):
    """{{bild12|links}} and {{bild12|rechts}}: the image at the edge, the text flowing round it (Ralf, 2026-10-10)."""
    from app import blog

    pid = _new(client, title_de="Seiten", body_de="x")
    with app.test_request_context("/"):
        post = db.session.get(BlogPost, pid)
        post.images.append(BlogImage(filename="abcdefghijklmnop.webp", width=10, height=5, alt_de="Bild"))
        db.session.commit()
        i = post.images[0].id
        html = str(blog.render(f"{{{{bild{i}|links}}}}\n\nText\n\n{{{{bild{i}| Rechts }}}}\n\n{{{{bild{i}|irgendwas}}}}\n\n"
                               f"Mitten {{{{bild{i}|right}}}} drin", post, "de"))
    assert html.count('<figure class="blog-figure is-left">') == 1 and html.count('<figure class="blog-figure is-right">') == 1
    assert html.count('<figure class="blog-figure">') == 1
    assert 'class="blog-float is-right">' in html and "<p>Mitten <img" in html


def test_english_readers_get_english_or_the_german_text_with_a_note(app, client):
    pid = _new(client, title_de="Nur deutsch", body_de="Deutscher Text")
    client.post(f"/admin/blog/{pid}/publish")
    guest = _guest(app)
    guest.post("/language", data={"lang": "en", "next": "/"})
    page = guest.get("/blog/nur-deutsch").data.decode()
    assert "This entry is only available in German." in page and 'lang="de"' in page and "Deutscher Text" in page
    _save(client, pid, title_en="English too", body_en="English text")
    page = guest.get("/blog/nur-deutsch").data.decode()
    assert "<h1>English too</h1>" in page and "English text" in page and "only available in German" not in page
    assert "Deutscher Text" in _guest(app).get("/blog/nur-deutsch").data.decode()   # German readers: German


def test_brands_and_the_brand_name(app):
    app.config.update(PUBLIC_BASE_URL="https://askurious.de", BRANDS="polltergeist.de=POLLtergeist", SECRET_KEY="x" * 32)
    client = _researcher(app, "0000-0000-0000-001X")  # signed in after the key changed: the admin of the tests
    pid = _new(client, title_de="Nur Geist", body_de="Willkommen bei {marke}.", brands_sent="1", brands=["polltergeist"])
    client.post(f"/admin/blog/{pid}/publish")
    both = _new(client, title_de="Für alle", body_de="Hallo {marke}.", brands_sent="1",
                brands=["askurious", "polltergeist"])
    client.post(f"/admin/blog/{both}/publish")
    with app.app_context():
        assert db.session.get(BlogPost, both).brands is None                   # all ticked = all
    guest = _guest(app)
    main = guest.get("/blog/", headers={"Host": "askurious.de"}).data.decode()
    ghost = guest.get("/blog/", headers={"Host": "polltergeist.de"}).data.decode()
    assert "Für alle" in main and "Nur Geist" not in main
    assert "Für alle" in ghost and "Nur Geist" in ghost
    assert guest.get("/blog/nur-geist", headers={"Host": "askurious.de"}).status_code == 404
    assert "Willkommen bei POLLtergeist." in guest.get("/blog/nur-geist", headers={"Host": "polltergeist.de"}).data.decode()
    assert "Hallo Askurious." in guest.get("/blog/fuer-alle", headers={"Host": "askurious.de"}).data.decode()


def test_address_follows_the_title_until_published_then_old_ones_redirect(app, client):
    pid = _new(client, title_de="Erster Titel", body_de="Text")
    _save(client, pid, title_de="Zweiter Titel")
    with app.app_context():
        assert db.session.get(BlogPost, pid).slug == "zweiter-titel"
        assert not db.session.scalars(db.select(BlogSlug)).all()
    client.post(f"/admin/blog/{pid}/publish")
    _save(client, pid, title_de="Dritter Titel")
    with app.app_context():
        assert db.session.get(BlogPost, pid).slug == "zweiter-titel"            # published: stays
    _save(client, pid, slug="neue-adresse")
    r = _guest(app).get("/blog/zweiter-titel")
    assert r.status_code == 301 and r.headers["Location"].endswith("/blog/neue-adresse")
    bad = _save(client, pid, slug="Nicht gut!")
    with app.app_context():
        assert db.session.get(BlogPost, pid).slug == "neue-adresse"
    other = _new(client, title_de="Neue Adresse", body_de="Text")                # the same title: a free address
    with app.app_context():
        assert db.session.get(BlogPost, other).slug == "neue-adresse-2"
    assert bad.status_code == 302


def test_changes_after_publication_say_updated(app, client):
    from datetime import timedelta

    pid = _new(client, title_de="Datum", body_de="Text")
    client.post(f"/admin/blog/{pid}/publish")
    with app.app_context():
        p = db.session.get(BlogPost, pid)
        p.published_at -= timedelta(days=3)
        db.session.commit()
    assert "aktualisiert am" not in _guest(app).get("/blog/datum").data.decode()
    _save(client, pid, body_de="Neuer Text")
    assert "aktualisiert am" in _guest(app).get("/blog/datum").data.decode()
    # a later day: public only then
    _save(client, pid, published_on="2099-01-01")
    assert _guest(app).get("/blog/datum").status_code == 404
    assert "erscheint später" in client.get("/admin/blog").data.decode()


def test_delete_removes_entry_and_images(app, client):
    pid = _new(client, title_de="Weg", body_de="Text")
    client.post(f"/admin/blog/{pid}/images", data={"image": (_png(), "a.png")}, content_type="multipart/form-data")
    folder = os.path.join(os.path.dirname(app.config["UPLOAD_FOLDER"]), "blog", str(pid))
    assert os.listdir(folder)
    assert client.post(f"/admin/blog/{pid}/delete").status_code == 302
    with app.app_context():
        assert db.session.get(BlogPost, pid) is None and not db.session.scalars(db.select(BlogImage)).all()
    assert not os.path.exists(folder)


def test_feed_sitemap_and_robots(app, client):
    pid = _new(client, title_de="Feed & mehr", summary_de="Kurz <b>fett</b>", body_de="Text", title_en="Feed and more",
               body_en="Text")
    client.post(f"/admin/blog/{pid}/publish")
    _new(client, title_de="Noch Entwurf", body_de="Text")
    guest = _guest(app)
    r = guest.get("/blog/feed.xml")
    assert r.mimetype == "application/atom+xml"
    ns = {"a": "http://www.w3.org/2005/Atom"}
    feed = ET.fromstring(r.data)
    titles = [e.findtext("a:title", namespaces=ns) for e in feed.findall("a:entry", ns)]
    assert titles == ["Feed & mehr"] and feed.findall("a:entry", ns)[0].findtext("a:summary", namespaces=ns) == "Kurz <b>fett</b>"
    en = ET.fromstring(guest.get("/blog/feed.xml?lang=en").data)
    assert [e.findtext("a:title", namespaces=ns) for e in en.findall("a:entry", ns)] == ["Feed and more"]
    sitemap = ET.fromstring(guest.get("/sitemap.xml").data)
    locs = [u.findtext("{http://www.sitemaps.org/schemas/sitemap/0.9}loc") for u in sitemap]
    assert any(l.endswith("/blog/feed-mehr") for l in locs) and any(l.endswith("/blog/") for l in locs)
    assert not any("noch-entwurf" in l for l in locs)
    assert "Sitemap: " in guest.get("/robots.txt").data.decode()


def test_dashboard_names_no_new_entries(app, client):
    """No line "Neu in den Neuigkeiten" on the dashboard (Ralf, 2026-10-10): the news are in the footer and on the
    landing page."""
    other = _researcher(app)
    pid = _new(client, title_de="Frisch", body_de="Text")
    client.post(f"/admin/blog/{pid}/publish")
    assert "Neu in den Neuigkeiten" not in other.get("/dashboard/").data.decode()
    assert other.post("/blog/seen").status_code in (404, 405)


def test_deleting_an_account_keeps_its_entries(app, client):
    from app import accounts

    _researcher(app, "0000-0002-1111-2222")
    pid = _new(client, title_de="Von jemandem", body_de="Text")
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(orcid="0000-0002-1111-2222"))
        post = db.session.get(BlogPost, pid)
        post.created_by_id = post.updated_by_id = uid  # written by an admin whose account goes
        db.session.commit()
    with app.test_request_context("/"):
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        accounts.delete(db.session.get(User, uid), "admin", {}, admin=me)
    with app.app_context():
        post = db.session.get(BlogPost, pid)
        assert db.session.get(User, uid) is None
        assert post is not None and post.created_by_id is None and post.updated_by_id is None


# --- stage 2: help from the AI (E16, E3) ------------------------------------------------------------------------

def test_log_since_takes_the_days_from_a_date(app, tmp_path, monkeypatch):
    from datetime import date

    from app import blog

    log = tmp_path / "log.md"
    # the forms log.md uses: a bare date, "date: title", "title (date)", and a heading without a date that keeps the
    # day before it
    log.write_text("# Development Log\n\n## 2026-09-30\n\n- alt\n\n## Ohne Datum\n\n- auch alt\n\n"
                   "## 2026-10-02: Karte\n\n- Karte\n\n### Einzelheiten\n\n- mehr\n\n## Ohne Datum\n\n- Liste\n\n"
                   "## Quoten (2026-10-03)\n\n- Quoten\n", encoding="utf-8")
    monkeypatch.setattr(blog, "log_path", lambda: str(log))
    with app.app_context():
        text = blog.log_since(date(2026, 10, 2))
        assert blog.log_since(date(2026, 10, 3)) == "## Quoten (2026-10-03)\n\n- Quoten"
    assert text == ("## 2026-10-02: Karte\n\n- Karte\n\n### Einzelheiten\n\n- mehr\n\n## Ohne Datum\n\n- Liste\n\n"
                    "## Quoten (2026-10-03)\n\n- Quoten") and "alt" not in text
    monkeypatch.setattr(blog, "log_path", lambda: str(tmp_path / "missing.md"))
    with app.app_context():
        assert blog.log_since(date(2026, 1, 1)) == ""


def test_draft_from_what_is_new_and_english_by_ai(app, client, monkeypatch):
    import json

    from app import ai

    app.config["OPENAI_API_KEY"] = "test"
    calls = []

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None):
        calls.append((purpose, prompt))
        if purpose == "blog_draft":
            # experimental features stay out of the public blog (Ralf, 2026-10-11)
            assert "Offene Antworten kategorisieren" in instructions and "Leave out" in instructions
            return json.dumps({"title_de": "Neu: Quoten-Assistent", "summary_de": "Kurz.", "body_de": "Text bei {marke}.",
                               "title_en": "New: quota assistant", "summary_en": "Short.", "body_en": "Text.",
                               "category": "improvement"})
        return json.dumps({"title": "Map with markers", "summary": "", "body": "**New**\n\n{{bild1}}"})

    monkeypatch.setattr(ai, "generate_text", fake)
    page = client.get("/admin/blog/draft").data.decode()
    assert "Entwurf schreiben" in page and 'name="source"' in page
    r = client.post("/admin/blog/draft", data={"source": "## 2026-10-03\n- Quoten-Assistent"})
    assert r.status_code == 302 and calls[0] == ("blog_draft", "## 2026-10-03\n- Quoten-Assistent")
    with app.app_context():
        post = db.session.scalars(db.select(BlogPost)).one()
        assert (post.status, post.category, post.slug, post.title_en) == ("draft", "improvement", "neu-quoten-assistent",
                                                                          "New: quota assistant")
    assert client.post("/admin/blog/draft", data={"source": " "}).status_code == 302   # nothing to write from
    pid = _new(client, title_de="Karte mit Markern", body_de="**Neu**\n\n{{bild1}}")
    page = client.get(f"/admin/blog/{pid}").data.decode()
    assert "Ins Englische übersetzen" in page and "data-confirm=" not in page.split("Ins Englische übersetzen")[0][-900:]
    client.post(f"/admin/blog/{pid}/translate")
    with app.app_context():
        post = db.session.get(BlogPost, pid)
        assert (post.title_en, post.summary_en, post.body_en) == ("Map with markers", "", "**New**\n\n{{bild1}}")
    assert calls[-1][0] == "blog_translation" and "TEXT:\n**Neu**" in calls[-1][1]
    # without a key no button and no call
    app.config["OPENAI_API_KEY"] = ""
    assert "Ins Englische übersetzen" not in client.get(f"/admin/blog/{pid}").data.decode()
    assert _researcher(app).post(f"/admin/blog/{pid}/translate").status_code == 403


def test_text_corrections_keep_the_style_and_name_each_change(app, client, monkeypatch):
    """"Textkorrekturen": a light proofreading per language on the fields as they are now; the message names every
    correction, an empty answer replaces nothing (Philipp, 2026-10-13)."""
    import json

    from app import ai

    app.config["OPENAI_API_KEY"] = "test"
    calls, answer = [], {}

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None):
        calls.append((purpose, instructions, prompt))
        return json.dumps(answer)

    monkeypatch.setattr(ai, "generate_text", fake)
    pid = _new(client, title_de="Neue Karte", body_de="Ein älterer Stand.")
    page = client.get(f"/admin/blog/{pid}").data.decode()
    assert page.count("Textkorrekturen") >= 2 and f"/admin/blog/{pid}/proofread/en" in page
    # the fields as sent, not what was stored: the typo is in the form only
    form = {"title_de": "Neue Karte", "summary_de": "", "body_de": "Das ist ein **Tetx** mit {{bild1}}.",
            "title_en": "", "summary_en": "", "body_en": "", "category": "feature"}
    answer.update(title="Neue Karte", summary="", body="Das ist ein **Text** mit {{bild1}}.",
                  changes=[{"before": "Tetx", "after": "Text"}])
    r = client.post(f"/admin/blog/{pid}/proofread/de", data=form, follow_redirects=True)
    purpose, instructions, prompt = calls[-1]
    assert purpose == "blog_proofreading" and "Keep the author's style" in instructions and "Duden" in instructions
    assert "{{bild12}}" in instructions and "{marke}" in instructions and "TEXT:\nDas ist ein **Tetx**" in prompt
    assert "„Tetx“ → „Text“" in r.data.decode()
    with app.app_context():
        assert db.session.get(BlogPost, pid).body_de == "Das ist ein **Text** mit {{bild1}}."
    # an answer without the text replaces nothing
    answer.update(body="", changes=[])
    client.post(f"/admin/blog/{pid}/proofread/de")
    with app.app_context():
        assert db.session.get(BlogPost, pid).body_de == "Das ist ein **Text** mit {{bild1}}."
    # no English text yet: nothing to correct, no call
    n = len(calls)
    client.post(f"/admin/blog/{pid}/proofread/en")
    assert len(calls) == n
    assert client.post(f"/admin/blog/{pid}/proofread/fr").status_code == 404
    assert _researcher(app).post(f"/admin/blog/{pid}/proofread/de").status_code == 403

