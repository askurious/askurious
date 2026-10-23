"""The public news page /blog, "Neuigkeiten" (blog-konzept.md, Ralf 2026-10-10: "wie empfohlen").

Site admins write the entries on the tab "Blog" of the administration: an entry is a draft until "Veröffentlichen",
"Zurückziehen" makes it a draft again, "Löschen" removes it with its images. The edit page saves by itself like the
other forms (app/autosave.py); a change to a published entry is public at once, and the entry then says "aktualisiert
am …". Everyone reads the published entries, newest first, in German or English (German is required to publish;
without an English text English readers get the German one with a note). An entry appears under all brands or only
under some (app/brand.py); `{marke}` in its texts becomes the name of the brand it is read under.

Texts use the formatting of the long texts of the app (items/base.py rich: paragraphs, "- " lists, a bold line as
heading, **bold**, *italic*), plus links as [Text](https://…) or a bare https address, and images as {{bild<id>}}
(the formatting field offers them under "Bild einfügen"; a click on one sets it across the column or at the left
or right edge, the text flowing round it: {{bild<id>|links}}, {{bild<id>|rechts}}). Images are written anew as WebP
80 without metadata; their alternative text is required before an entry with them is published (BITV).

Also here: the Atom feed /blog/feed.xml, /sitemap.xml, the line on the dashboard about entries not yet seen, and the
three newest entries for the landing page. Blog actions are not in the access log, which keeps only actions about
personal data (decision 1a of 2026-10-06); the admin list shows who changed an entry last.
"""
import os
import re
import secrets
import shutil
import unicodedata
from datetime import date
from xml.sax.saxutils import escape as xml_escape

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import current_user, login_required
from markupsafe import Markup, escape
from sqlalchemy.orm import defer, selectinload

from app import autosave, brand
from app.extensions import db
from app.lang import de_en, english
from app.models import BlogImage, BlogPost, BlogSlug, utcnow
from app.timeutil import fmt, local_to_utc, to_local

bp = Blueprint("blog", __name__)

CATEGORIES = {
    "feature": ("Neue Funktion", "New feature"),
    "improvement": ("Verbesserung", "Improvement"),
    "method": ("Methodik", "Methods"),
    "operation": ("Betrieb & Sicherheit", "Operation & security"),
}
PER_PAGE = 10
FEED_SIZE = 20
LANDING_SIZE = 3
MAX_IMAGES = 30
MAX_BYTES = 8 * 1024 * 1024
MAX_SIDE = 2400
MAIN_KEY = brand.NAME.lower()  # the key of the main brand in BlogPost.brands
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
NAME_RE = re.compile(r"[A-Za-z0-9_-]{16}\.webp\Z")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def category_label(key):
    de, en = CATEGORIES.get(key, CATEGORIES["feature"])
    return de_en(de, en)


# --- brands ----------------------------------------------------------------------------------------------------

def _key(name):
    return re.sub(r"[^a-z0-9-]", "", name.lower())


def brand_key():
    """The key of the brand of this request: "askurious" or e.g. "polltergeist"."""
    return brand.site() or MAIN_KEY


def brand_choices():
    """[(key, name)] of every brand this installation answers as: the main name, BRANDS and SITE_BRAND."""
    out = [(MAIN_KEY, brand.NAME)]
    for value in (current_app.config.get("BRANDS"), current_app.config.get("SITE_BRAND")):
        for _domain, name in brand.parse(value or ""):
            if _key(name) not in {k for k, _ in out}:
                out.append((_key(name), name))
    return out


def shown_here(post):
    return not post.brands or brand_key() in post.brands


def _branded(text):
    """`{marke}` (also `{brand}`) as the name of the brand the page is read under."""
    name = brand.name()
    return (text or "").replace("{marke}", name).replace("{brand}", name)


# --- what is public ------------------------------------------------------------------------------------------

def is_admin():
    """A site admin who may see drafts on the public pages: with ADMIN_TOTP on only after the code in this browser,
    as for the administration (audit 2026-10-10, A8/R3)."""
    from app import twofactor

    return current_user.is_authenticated and twofactor.admin_rights(current_user)


def public(post, now=None):
    """Published, its date reached, and meant for this brand."""
    return (post.status == BlogPost.PUBLISHED and post.published_at is not None
            and post.published_at <= (now or utcnow()) and shown_here(post))


def visible_posts(limit=None, images=False):
    """The published entries of this brand, newest first (the brand is filtered here, not in SQL). The texts are
    deferred: lists, feed and sitemap need them only for the teaser of an entry without summary, and then only for
    the entries shown (audit 2026-10-10, A7/P6). limit: stop after so many; images: their images in one query."""
    now = utcnow()
    query = (db.select(BlogPost).options(defer(BlogPost.body_de), defer(BlogPost.body_en))
             .filter(BlogPost.status == BlogPost.PUBLISHED, BlogPost.published_at <= now)
             .order_by(BlogPost.published_at.desc(), BlogPost.id.desc()))
    if images:
        query = query.options(selectinload(BlogPost.images))
    out = []
    result = db.session.scalars(query.execution_options(yield_per=50))
    try:
        for p in result:
            if shown_here(p):
                out.append(p)
                if limit is not None and len(out) >= limit:
                    break
    finally:
        result.close()
    return out


def latest(n=LANDING_SIZE):
    """The newest entries of this brand as views, for the landing page."""
    return [view(p) for p in visible_posts(limit=n, images=True)]


def any_public():
    """Whether there is anything to read: the footer and the landing page link the blog only then. Once per
    request, and only the brands column (the footer of every page asks)."""
    from flask import g

    from sqlalchemy.exc import SQLAlchemyError

    if "blog_any" not in g:
        try:
            rows = db.session.scalars(db.select(BlogPost.brands).filter(BlogPost.status == BlogPost.PUBLISHED,
                                                                        BlogPost.published_at <= utcnow()))
            key = brand_key()
            g.blog_any = any(not brands or key in brands for brands in rows)
        except SQLAlchemyError:  # the error page of a failing database still shows its footer
            db.session.rollback()
            g.blog_any = False
    return g.blog_any


# --- language and text -----------------------------------------------------------------------------------------

def has_english(post):
    return bool(post.title_en.strip() and post.body_en.strip())


def lang_of(post, en=None):
    en = english() if en is None else en
    return "en" if en and has_english(post) else "de"


_IMAGE = re.compile(r"\{\{\s*bild(\d+)\s*(?:\|([^{}]*))?\}\}")
_LINK = re.compile(r"\[([^\]\n]{1,300})\]\(((?:https?://|mailto:|/)[^\s()<>\"']{1,1000})\)")
_URL = re.compile(r"(?<![\w/@.])(https?://[^\s<>\"'\x02\x03]{2,1000}?)(?=[.,;:!?)\]]*(?:\s|$|<))")
_TOKEN = re.compile("\x03(\\d+)\x03")
_IMAGE_TOKEN = re.compile("\x02(\\d+)([lr]?)\x02")
# where an image stands: {{bild12|links}} at the left edge and {{bild12|rechts}} at the right, the text flowing round
# it; without (or with anything else) across the reading column (Ralf, 2026-10-10)
_SIDES = {"links": "l", "left": "l", "rechts": "r", "right": "r"}


def image_side(value):
    return _SIDES.get((value or "").strip().lower(), "")


def image_url(img, absolute=False):
    path = url_for("blog.media", post_id=img.post_id, name=img.filename)
    return brand.public_base() + path if absolute else path


def image_alt(img, lang):
    return (img.alt_en if lang == "en" and img.alt_en.strip() else img.alt_de).strip()


def used_images(post):
    """The ids of the images the German or English text puts in."""
    return {int(m.group(1)) for text in (post.body_de, post.body_en) for m in _IMAGE.finditer(text or "")}


def render(text, post, lang):
    """The text of an entry as safe HTML: rich() of the app, plus links and images. Links and images are taken out
    before the formatting (an address with ~ or * would otherwise be formatted) and put back after it."""
    from app.items.base import inline_rich, rich

    images = {img.id: img for img in post.images}
    links = []

    def keep_link(label, url):
        links.append((label, url))
        return f"\x03{len(links) - 1}\x03"

    text = (text or "").replace("\x02", "").replace("\x03", "")
    text = _IMAGE.sub(lambda m: f"\x02{m.group(1)}{image_side(m.group(2))}\x02", _branded(text))
    text = _LINK.sub(lambda m: keep_link(m.group(1), m.group(2)), text)
    text = _URL.sub(lambda m: keep_link(None, m.group(1)), text)
    html = str(rich(text))

    def link(m):
        label, url = links[int(m.group(1))]
        shown = inline_rich(label) if label else escape(url)
        # //host and /\host lead to another host in the browser: outside too (audit 2026-10-10, A9)
        outside = url.startswith(("http://", "https://")) or (url.startswith("/") and not re.match(r"/(?![/\\])", url))
        extra = ' target="_blank" rel="noopener"' if outside else ""
        return f'<a href="{escape(url)}"{extra}>{shown}</a>'

    def figure(img, inline, side):
        alt = escape(image_alt(img, lang))
        place = {"l": " is-left", "r": " is-right"}.get(side, "")
        floated = f' class="blog-float{place}"' if inline and place else ""
        tag = (f'<img src="{escape(image_url(img))}" alt="{alt}" width="{img.width}" height="{img.height}" '
               f'loading="lazy" decoding="async"{floated}>')
        return tag if inline else f'<figure class="blog-figure{place}">{tag}</figure>'

    def block_image(m):
        img = images.get(int(m.group(1)))
        return figure(img, False, m.group(2)) if img else ""

    def inline_image(m):
        img = images.get(int(m.group(1)))
        return figure(img, True, m.group(2)) if img else ""

    html = re.sub("<p>\x02(\\d+)([lr]?)\x02</p>", block_image, html)
    html = _IMAGE_TOKEN.sub(inline_image, html)
    html = _TOKEN.sub(link, html)
    return Markup(html)


def summary_text(post, lang):
    """The teaser: the summary, else the start of the text without its marks (at most 280 characters)."""
    from app.items.base import plain

    summary = (post.summary_en if lang == "en" else post.summary_de).strip()
    if summary:
        return _branded(summary)
    body = _IMAGE.sub(" ", post.body_en if lang == "en" else post.body_de)
    body = _LINK.sub(lambda m: m.group(1), body)
    body = " ".join(plain(_branded(body)).split())
    return body if len(body) <= 280 else body[:279].rsplit(" ", 1)[0] + " …"


def updated_on(post):
    """The day of the last change after publication, if it is later than the day of publication."""
    if not post.content_changed_at or not post.published_at:
        return None
    if to_local(post.content_changed_at).date() <= to_local(post.published_at).date():
        return None
    return post.content_changed_at


def view(post, en=None):
    """What a page shows of an entry, in the language of the page (or German, see lang_of)."""
    lang = lang_of(post, en)
    cover = next((img for img in post.images if img.id == post.cover_image_id), None)
    return {"post": post, "lang": lang, "fallback": lang == "de" and (english() if en is None else en),
            "title": _branded(post.title_en if lang == "en" else post.title_de) or de_en("(ohne Titel)", "(untitled)"),
            "summary": summary_text(post, lang), "category": category_label(post.category),
            "cover": cover, "cover_alt": image_alt(cover, lang) if cover else "",
            "updated": updated_on(post), "url": url_for("blog.post", slug=post.slug),
            "day": to_local(post.published_at).date().isoformat() if post.published_at else ""}


# --- addresses -------------------------------------------------------------------------------------------------

def slugify(text):
    text = unicodedata.normalize("NFKD", (text or "").translate(_UMLAUTS).lower()).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:80].rstrip("-")
    return text or "eintrag"


def _taken(slug, post_id):
    other = db.session.scalar(db.select(BlogPost.id).filter(BlogPost.slug == slug, BlogPost.id != post_id))
    old = db.session.scalar(db.select(BlogSlug.post_id).filter(BlogSlug.slug == slug, BlogSlug.post_id != post_id))
    return other is not None or old is not None


def unique_slug(base, post_id):
    slug, n = base, 2
    while _taken(slug, post_id):
        slug = f"{base[:76]}-{n}"
        n += 1
    return slug


def _set_slug(post, slug):
    """A new address; the old one of a published entry keeps leading there."""
    if slug == post.slug:
        return
    if post.published_at is not None and not db.session.get(BlogSlug, post.slug):
        db.session.add(BlogSlug(slug=post.slug, post_id=post.id))
    gone = db.session.get(BlogSlug, slug)  # an earlier address of this entry becomes its address again
    if gone is not None and gone.post_id == post.id:
        db.session.delete(gone)
    post.slug = slug


# --- images ----------------------------------------------------------------------------------------------------

def image_root():
    return os.path.join(os.path.dirname(current_app.config["UPLOAD_FOLDER"]), "blog")


def store_image(post, upload):
    """Read an uploaded image and write it anew as WebP 80 (at most MAX_SIDE a side, only rights metadata); returns the
    BlogImage. ValueError with a message for the admin."""
    import io

    from app import image_rights
    from app.comment_shots import DECODE_WAIT, MAX_PIXELS, _decoded, _decoding
    from app.media import open_image

    if len(post.images) >= MAX_IMAGES:
        raise ValueError(de_en("Ein Eintrag hat höchstens {n} Bilder.", "An entry has at most {n} images.", n=MAX_IMAGES))
    data = upload.read(MAX_BYTES + 1)
    if not data:
        raise ValueError(de_en("Das Bild ist leer.", "The image is empty."))
    if len(data) > MAX_BYTES:
        raise ValueError(de_en("Das Bild ist zu groß (höchstens 8 MB).", "The image is too large (at most 8 MB)."))
    img = open_image(data, ["PNG", "JPEG", "WEBP", "GIF"], max_pixels=MAX_PIXELS)
    if not _decoding.acquire(timeout=DECODE_WAIT):
        raise ValueError(de_en("Gerade werden viele Bilder verarbeitet. Bitte gleich noch einmal.",
                               "Many images are being processed right now. Please try again in a moment."))
    try:
        rights = image_rights.read(img)
        img = _decoded(img)
    finally:
        _decoding.release()
    out = io.BytesIO()
    img.info.clear()
    img.save(out, "WEBP", quality=80, method=4, **image_rights.save_options(rights, "WEBP"))
    folder = os.path.join(image_root(), str(post.id))
    os.makedirs(folder, exist_ok=True)
    name = f"{secrets.token_urlsafe(12)}.webp"
    tmp = os.path.join(folder, f".{name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(out.getvalue())
    os.replace(tmp, os.path.join(folder, name))
    row = BlogImage(post_id=post.id, filename=name, width=img.width, height=img.height)
    db.session.add(row)
    return row


def _remove_file(img):
    try:
        os.remove(os.path.join(image_root(), str(img.post_id), img.filename))
    except OSError:
        pass


# --- public pages ----------------------------------------------------------------------------------------------

@bp.route("/blog/")
def index():
    posts = visible_posts()
    page = max(request.args.get("p", 1, type=int), 1)
    pages = max((len(posts) + PER_PAGE - 1) // PER_PAGE, 1)
    page = min(page, pages)
    shown = posts[(page - 1) * PER_PAGE:page * PER_PAGE]
    _with_images(shown)
    return render_template("public/blog_list.html", views=[view(p) for p in shown], page=page, pages=pages,
                           drafts=_draft_count() if is_admin() else 0)


def _with_images(posts):
    """Load the images of these entries in one query, not one per entry (view() looks for the cover)."""
    if posts:
        db.session.scalars(db.select(BlogPost).options(selectinload(BlogPost.images))
                           .filter(BlogPost.id.in_([p.id for p in posts]))).all()


def _draft_count():
    return db.session.scalar(db.select(db.func.count(BlogPost.id)).filter(BlogPost.status == BlogPost.DRAFT)) or 0


@bp.route("/blog/<slug>")
def post(slug):
    entry = db.session.scalar(db.select(BlogPost).filter_by(slug=slug))
    if entry is None:
        old = db.session.get(BlogSlug, slug)
        target = db.session.get(BlogPost, old.post_id) if old is not None else None
        # to the new name only while the entry is to be seen: a withdrawn and renamed one gave away its draft's name
        # (audit 2026-10-13, TN-37)
        if target is not None and (public(target) or is_admin()):
            return redirect(url_for("blog.post", slug=target.slug), 301)
        abort(404)
    if not public(entry) and not is_admin():
        abort(404)  # not 403: a draft is not to be found
    posts = visible_posts()
    ids = [p.id for p in posts]
    newer = older = None
    if entry.id in ids:
        i = ids.index(entry.id)
        newer = view(posts[i - 1]) if i > 0 else None
        older = view(posts[i + 1]) if i + 1 < len(posts) else None
    v = view(entry)
    return render_template("public/blog_post.html", v=v, body=render(entry.body_en if v["lang"] == "en" else entry.body_de,
                                                                     entry, v["lang"]),
                           newer=newer, older=older, is_public=public(entry),
                           cover_url=image_url(v["cover"], absolute=True) if v["cover"] else "")


@bp.route("/blog/media/<int:post_id>/<name>")
def media(post_id, name):
    if not NAME_RE.match(name):
        abort(404)
    entry = db.session.get(BlogPost, post_id)
    if entry is None or not (public(entry) or is_admin()):
        abort(404)
    if not db.session.scalar(db.select(BlogImage.id).filter_by(post_id=post_id, filename=name)):
        abort(404)
    if not public(entry):  # an image of a draft: no shared cache keeps it (audit 2026-10-10, A8)
        response = send_from_directory(os.path.join(image_root(), str(post_id)), name, mimetype="image/webp")
        response.headers["Cache-Control"] = "private, no-store"
        return response
    return send_from_directory(os.path.join(image_root(), str(post_id)), name, mimetype="image/webp",
                               max_age=7 * 86400)


def _iso(dt):
    return dt.replace(microsecond=0).isoformat() + "Z"


@bp.route("/blog/feed.xml")
def feed():
    """Atom feed of the newest entries; ?lang=en: the English texts where there are any."""
    en = request.args.get("lang") == "en"
    base = brand.public_base()
    posts = visible_posts(limit=FEED_SIZE)
    title = de_en("{name}: Neuigkeiten", "{name}: News", name=brand.name()) if not en else f"{brand.name()}: News"
    self_url = base + url_for("blog.feed") + ("?lang=en" if en else "")
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom"'
           f' xml:lang="{"en" if en else "de"}">', f"<title>{xml_escape(title)}</title>",
           f"<id>{xml_escape(self_url)}</id>", f'<link rel="self" href="{xml_escape(self_url)}"/>',
           f'<link rel="alternate" type="text/html" href="{xml_escape(base + url_for("blog.index"))}"/>',
           f"<updated>{_iso(max((p.content_changed_at or p.published_at) for p in posts) if posts else utcnow())}</updated>",
           f"<author><name>{xml_escape(brand.name())}</name></author>"]
    for p in posts:
        lang = lang_of(p, en)
        url = base + url_for("blog.post", slug=p.slug)
        entry_title = _branded(p.title_en if lang == "en" else p.title_de)
        out += ["<entry>", f"<title>{xml_escape(entry_title)}</title>", f'<link rel="alternate" href="{xml_escape(url)}"/>',
                f"<id>{xml_escape(base)}/blog/{p.id}</id>", f"<published>{_iso(p.published_at)}</published>",
                f"<updated>{_iso(p.content_changed_at or p.published_at)}</updated>",
                f'<category term="{xml_escape(p.category)}"/>',
                f"<summary>{xml_escape(summary_text(p, lang))}</summary>", "</entry>"]
    out.append("</feed>")
    return Response("\n".join(out), mimetype="application/atom+xml")


@bp.route("/sitemap.xml")
def sitemap():
    base = brand.public_base()
    from app.public import donations_on

    pages = [(url_for("public.index"), None), (url_for("blog.index"), None)]
    pages += [(url_for("public.pricing"), None)] if donations_on() else []
    pages += [(url_for("public.features"), None), (url_for("public.privacy"), None)]
    pages += [(url_for("blog.post", slug=p.slug), p.content_changed_at or p.published_at) for p in visible_posts()]
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, changed in pages:
        out.append(f"<url><loc>{xml_escape(base + path)}</loc>"
                   + (f"<lastmod>{to_local(changed).date().isoformat()}</lastmod>" if changed else "") + "</url>")
    out.append("</urlset>")
    return Response("\n".join(out), mimetype="application/xml")


# --- the administration --------------------------------------------------------------------------------------

def _admin(view_func):
    from functools import wraps

    @wraps(view_func)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        from app import twofactor

        stop = twofactor.gate()  # as for every page of the administration (security audit 2026-10-04, H5)
        return stop or view_func(*args, **kwargs)

    return wrapper


def _post_or_404(post_id):
    entry = db.session.get(BlogPost, post_id)
    if entry is None:
        abort(404)
    return entry


@bp.route("/admin/blog")
@_admin
def admin_list():
    posts = db.session.scalars(db.select(BlogPost).order_by(BlogPost.status, BlogPost.published_at.desc(),
                                                            BlogPost.updated_at.desc())).all()
    drafts = [p for p in posts if p.status == BlogPost.DRAFT]
    published = [p for p in posts if p.status == BlogPost.PUBLISHED]
    return render_template("admin/blog.html", drafts=drafts, published=published, now=utcnow(),
                           brands=dict(brand_choices()))


@bp.route("/admin/blog/new", methods=["POST"])
@_admin
def admin_new():
    entry = BlogPost(slug=f"entwurf-{secrets.token_hex(4)}", status=BlogPost.DRAFT, created_by_id=current_user.id,
                     updated_by_id=current_user.id)
    db.session.add(entry)
    db.session.commit()
    return redirect(url_for("blog.admin_edit", post_id=entry.id))


def _clean(value, limit):
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")[:limit]


def _one_line(value, limit):
    return " ".join((value or "").split())[:limit]


@bp.route("/admin/blog/<int:post_id>", methods=["GET", "POST"])
@_admin
@autosave.aware
def admin_edit(post_id):
    entry = _post_or_404(post_id)
    if request.method == "GET":
        return render_template("admin/blog_edit.html", p=entry, categories=CATEGORIES, brand_choices=brand_choices(),
                               used=used_images(entry), rich_text=True, ai_ready=ai_ready(),
                               published_on=to_local(entry.published_at).date().isoformat() if entry.published_at else "",
                               image_vars=[{"title": de_en("Bilder dieses Eintrags", "Images of this entry"), "kind": "image",
                                            "items": [{"name": f"bild{img.id}",
                                                       "text": de_en("Bild: {alt}", "Image: {alt}", alt=img.alt_de or img.filename)}
                                                      for img in entry.images]}])
    _save_form(entry, request.form)
    db.session.commit()
    if not autosave.wanted():
        flash(de_en("Gespeichert.", "Saved."), "success")
    return redirect(url_for("blog.admin_edit", post_id=entry.id))


def _missing_for_publish(entry):
    """What keeps an entry from being published, as messages."""
    missing = []
    if not entry.title_de.strip():
        missing.append(de_en("ein deutscher Titel", "a German title"))
    if not entry.body_de.strip():
        missing.append(de_en("ein deutscher Text", "a German text"))
    shown = used_images(entry) | ({entry.cover_image_id} if entry.cover_image_id else set())
    if any(not img.alt_de.strip() for img in entry.images if img.id in shown):
        missing.append(de_en("ein Alternativtext für jedes gezeigte Bild", "an alternative text for every image shown"))
    return missing


@bp.route("/admin/blog/<int:post_id>/preview")
@_admin
def admin_preview(post_id):
    """The entry as everyone will see it, at its address of now (it follows the title while it is a draft)."""
    return redirect(url_for("blog.post", slug=_post_or_404(post_id).slug))


@bp.route("/admin/blog/<int:post_id>/publish", methods=["POST"])
@_admin
def admin_publish(post_id):
    entry = _post_or_404(post_id)
    missing = _missing_for_publish(entry)
    if missing:
        flash(de_en("Noch nicht veröffentlicht. Es fehlt: {what}.", "Not published yet. Missing: {what}.",
                    what=", ".join(missing)), "error")
        return redirect(url_for("blog.admin_edit", post_id=entry.id))
    if entry.published_at is None:
        entry.published_at = utcnow()
    entry.status = BlogPost.PUBLISHED
    entry.updated_at, entry.updated_by_id = utcnow(), current_user.id
    db.session.commit()
    if entry.published_at > utcnow():
        flash(de_en("Veröffentlicht; der Eintrag erscheint am {day}.", "Published; the entry appears on {day}.",
                    day=fmt(entry.published_at, False, "en" if english() else "de")), "success")
    else:
        flash(de_en("Veröffentlicht.", "Published."), "success")
    return redirect(url_for("blog.admin_edit", post_id=entry.id))


@bp.route("/admin/blog/<int:post_id>/withdraw", methods=["POST"])
@_admin
def admin_withdraw(post_id):
    entry = _post_or_404(post_id)
    entry.status = BlogPost.DRAFT
    entry.updated_at, entry.updated_by_id = utcnow(), current_user.id
    db.session.commit()
    flash(de_en("Zurückgezogen: Der Eintrag ist wieder ein Entwurf und nicht mehr öffentlich.",
                "Withdrawn: the entry is a draft again and no longer public."), "success")
    return redirect(url_for("blog.admin_edit", post_id=entry.id))


@bp.route("/admin/blog/<int:post_id>/delete", methods=["POST"])
@_admin
def admin_delete(post_id):
    entry = _post_or_404(post_id)
    title = entry.title_de or de_en("(ohne Titel)", "(untitled)")
    db.session.delete(entry)
    db.session.commit()
    shutil.rmtree(os.path.join(image_root(), str(post_id)), ignore_errors=True)
    flash(de_en("„{title}“ gelöscht.", "“{title}” deleted.", title=title), "success")
    return redirect(url_for("blog.admin_list"))


@bp.route("/admin/blog/<int:post_id>/images", methods=["POST"])
@_admin
def admin_image_add(post_id):
    entry = _post_or_404(post_id)
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        flash(de_en("Bitte ein Bild auswählen.", "Please choose an image."), "error")
        return redirect(url_for("blog.admin_edit", post_id=entry.id) + "#bilder")
    try:
        img = store_image(entry, upload)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("blog.admin_edit", post_id=entry.id) + "#bilder")
    img.alt_de = _one_line(request.form.get("alt_de"), 300)
    img.alt_en = _one_line(request.form.get("alt_en"), 300)
    if request.form.get("as_cover") == "1":
        db.session.flush()
        entry.cover_image_id = img.id
    entry.updated_at, entry.updated_by_id = utcnow(), current_user.id
    db.session.commit()
    flash(de_en("Bild hochgeladen. Mit „Bild einfügen“ im Textfeld setzen Sie es in den Text.",
                "Image uploaded. Use “Insert image” in the text field to put it into the text."), "success")
    return redirect(url_for("blog.admin_edit", post_id=entry.id) + "#bilder")


@bp.route("/admin/blog/<int:post_id>/images/<int:image_id>/delete", methods=["POST"])
@_admin
def admin_image_delete(post_id, image_id):
    entry = _post_or_404(post_id)
    img = db.session.get(BlogImage, image_id)
    if img is None or img.post_id != entry.id:
        abort(404)
    if entry.cover_image_id == img.id:
        entry.cover_image_id = None
    _remove_file(img)
    db.session.delete(img)
    entry.updated_at, entry.updated_by_id = utcnow(), current_user.id
    db.session.commit()
    flash(de_en("Bild entfernt. Wo es im Text stand, steht jetzt nichts mehr.",
                "Image removed. Its place in the text is now empty."), "success")
    return redirect(url_for("blog.admin_edit", post_id=entry.id) + "#bilder")


# --- help from the AI (stage 2 of blog-konzept.md: E16 and E3) --------------------------------------------------

_LOG_HEAD = re.compile(r"^## ", re.M)
_LOG_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
LOG_LIMIT = 40_000  # characters of log.md the draft is written from; the newest when there are more
CATEGORY_KEYS = list(CATEGORIES)

DRAFT_INSTRUCTIONS = """You write one entry for the public news page of a web platform for scientific surveys (online
questionnaires, live polls, interview guides). Readers are researchers, teachers and students, not developers.

You get excerpts of the developers' log. Choose only what readers notice: new features, visible improvements, changes to
methods and results, and what matters about privacy and operation. Leave out tests, refactoring, internal names of files,
functions, columns and settings, numbers of pull requests, audit codes, deploys and server details. Merge related items.
If nothing in the log is worth telling, say so in a short entry.

Refer to the platform as {marke} (write exactly these seven characters, they are replaced by the name); never write
"Askurious", "Queryous" or another name of it. Write the German text with "Sie" and the English text in British
English; both say the same.

Format of title and summary: plain text. Format of the text: paragraphs separated by a blank line; a list as lines that
start with "- "; a subheading as a line that is bold as a whole (**Subheading**); **bold** and *italic* inside a line.
Nothing else: no "#" headings, no tables, no links, no images. Title at most 80 characters, summary one or two
sentences, text 120 to 400 words. Category: "feature" for new functions, "improvement" for better existing ones, "method"
for statistics and methodology, "operation" for operation, privacy and security; the one that fits most of the entry."""

EXPERIMENTAL_INSTRUCTIONS = """Readers do not see experimental features: only researchers for whom an admin switched them on do.
Leave out everything about the following experimental features, also as a mention or a remark "(experimentell)". A
feature the log says has left the experimental group counts as new for readers (Ralf, 2026-10-11). Experimental now:
"""


def draft_instructions():
    """DRAFT_INSTRUCTIONS plus the experimental features of now (app/experimental.py), which the draft leaves out."""
    from app.experimental import FEATURES

    return DRAFT_INSTRUCTIONS + "\n\n" + EXPERIMENTAL_INSTRUCTIONS + "\n".join(f"- {label}" for _k, label, _t in FEATURES)


TRANSLATE_INSTRUCTIONS = """Translate a news entry of a web platform for scientific surveys from German into British
English. Keep every mark exactly as it is and where it is: **bold**, *italic*, `code`, H~2~O, x^2^, line breaks, blank
lines between paragraphs, lines starting with "- ", placeholders like {{bild12}}, {{bild12|links}} and {marke}
(never what follows the |), links like [text](address) (translate only the text in the brackets, never the
address). Translate nothing else into marks and add nothing. Keep names of functions of the platform as its English
interface calls them where you know them."""

PROOFREAD_INSTRUCTIONS = """Proofread a news entry of a web platform for scientific surveys, lightly. The text is in
{language}. Correct only what is wrong: spelling and typing errors, grammar, agreement, punctuation, a missing or doubled
word, a clearly wrong word. Keep the author's style: their word choice, tone, sentence length, sentence order and
paragraphs. Do not reword what is correct, do not smooth or shorten, do not add or leave out sentences, do not change
facts, names, numbers or dates. If nothing is wrong, return the text unchanged. {spelling}
Keep every mark exactly as it is and where it is: **bold**, *italic*, `code`, H~2~O, x^2^, line breaks, blank lines
between paragraphs, lines starting with "- ", placeholders like {{{{bild12}}}}, {{{{bild12|links}}}} and {{marke}}, links like
[text](address) (never change the address). List every correction in "changes" as the short passage before and after
it, in the order of the text; an empty list when nothing was corrected."""
PROOFREAD_SPELLING = {"de": "German as the current rules of spelling (Duden) have it, with German quotation marks „…“.",
                      "en": "British English spelling."}

_TEXTS = {"type": "string"}
DRAFT_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["title_de", "summary_de", "body_de", "title_en", "summary_en", "body_en", "category"],
                "properties": {"title_de": _TEXTS, "summary_de": _TEXTS, "body_de": _TEXTS, "title_en": _TEXTS,
                               "summary_en": _TEXTS, "body_en": _TEXTS,
                               "category": {"type": "string", "enum": CATEGORY_KEYS}}}
PROOFREAD_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title", "summary", "body", "changes"],
                    "properties": {"title": _TEXTS, "summary": _TEXTS, "body": _TEXTS,
                                   "changes": {"type": "array", "items": {
                                       "type": "object", "additionalProperties": False, "required": ["before", "after"],
                                       "properties": {"before": _TEXTS, "after": _TEXTS}}}}}
TRANSLATE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title", "summary", "body"],
                    "properties": {"title": _TEXTS, "summary": _TEXTS, "body": _TEXTS}}


def ai_ready():
    """The AI buttons of the blog: a key on the server and generative AI on for the admin ("KI-Nutzung")."""
    from app import ai_access

    return bool(current_app.config.get("OPENAI_API_KEY")) and ai_access.ai_on()


def log_path():
    return os.path.join(os.path.dirname(current_app.root_path), "log.md")


def log_sections(text):
    """[(day, section)] of log.md in its order. A section starts at "## "; its day is the first date in its heading
    ("## 2026-10-05", "## 2026-10-05: …", "## … (2026-10-10)"); a heading without one keeps the day before it, since
    the log is written in order. Sections before the first date are left out."""
    out, day = [], None
    for part in _LOG_HEAD.split(text)[1:]:
        heading = part.split("\n", 1)[0]
        found = _LOG_DATE.search(heading)
        if found:
            try:
                day = date.fromisoformat(found.group(0))
            except ValueError:
                pass
        if day is not None and part.strip():
            out.append((day, "## " + part.strip()))
    return out


def log_since(day):
    """The sections of log.md from `day` on, as one text (at most LOG_LIMIT characters, the newest whole sections);
    "" when the installation has no log.md."""
    try:
        with open(log_path(), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    out, size = [], 0
    for _day, section in reversed([s for s in log_sections(text) if s[0] >= day]):
        if size + len(section) > LOG_LIMIT:
            if not out:
                out.append(section[:LOG_LIMIT])
            break
        out.append(section)
        size += len(section) + 2
    return "\n\n".join(reversed(out))


def draft_start():
    """From when the draft looks at the log: the day of the newest published entry, else two weeks back."""
    from datetime import timedelta

    newest = db.session.scalar(db.select(db.func.max(BlogPost.published_at)).filter(BlogPost.status == BlogPost.PUBLISHED))
    return to_local(newest).date() if newest else to_local(utcnow()).date() - timedelta(days=14)


@bp.route("/admin/blog/draft", methods=["GET", "POST"])
@_admin
def admin_draft():
    """"Entwurf aus den Neuerungen": the AI writes a draft in German and English from the developers' log (log.md
    from a day on; the admin sees and may shorten what it gets). The draft stays a draft until someone publishes it."""
    from app import ai

    if request.method == "GET":
        try:
            since = date.fromisoformat(request.args.get("seit", ""))
        except ValueError:
            since = draft_start()
        return render_template("admin/blog_draft.html", since=since.isoformat(), source=log_since(since),
                               has_log=os.path.exists(log_path()), ai_ready=ai_ready())
    source = _clean(request.form.get("source"), LOG_LIMIT).strip()
    if not source:
        flash(de_en("Bitte die Grundlage angeben: die Einträge des Entwicklungsprotokolls oder eigene Stichpunkte.",
                    "Please give the basis: the entries of the development log or notes of your own."), "error")
        return redirect(url_for("blog.admin_draft"))
    try:
        data = ai.json_answer(ai.generate_text(draft_instructions(), source, purpose="blog_draft", schema=DRAFT_SCHEMA))
    except ai.AIError as e:
        flash(str(e), "error")
        return redirect(url_for("blog.admin_draft"))
    entry = BlogPost(slug=f"entwurf-{secrets.token_hex(4)}", status=BlogPost.DRAFT, created_by_id=current_user.id,
                     updated_by_id=current_user.id,
                     category=data.get("category") if data.get("category") in CATEGORIES else "feature",
                     title_de=_one_line(data.get("title_de"), 200), title_en=_one_line(data.get("title_en"), 200),
                     summary_de=_clean(data.get("summary_de"), 1000).strip(),
                     summary_en=_clean(data.get("summary_en"), 1000).strip(),
                     body_de=_clean(data.get("body_de"), 100_000).strip(), body_en=_clean(data.get("body_en"), 100_000).strip())
    db.session.add(entry)
    db.session.flush()
    if entry.title_de:
        entry.slug = unique_slug(slugify(entry.title_de), entry.id)
    db.session.commit()
    flash(de_en("Entwurf geschrieben. Bitte lesen und anpassen, bevor Sie ihn veröffentlichen.",
                "Draft written. Please read and adjust it before you publish it."), "success")
    return redirect(url_for("blog.admin_edit", post_id=entry.id))


def _save_form(entry, f):
    """Takes the fields of the edit page into `entry` (not committed): texts, category, brands, alternative texts,
    cover, address and date of publication. Also used by the AI buttons, which work on what is in the fields now,
    not on what the autosave last stored."""
    before = (entry.title_de, entry.title_en, entry.summary_de, entry.summary_en, entry.body_de, entry.body_en)
    entry.title_de, entry.title_en = _one_line(f.get("title_de"), 200), _one_line(f.get("title_en"), 200)
    entry.summary_de, entry.summary_en = _clean(f.get("summary_de"), 1000).strip(), _clean(f.get("summary_en"), 1000).strip()
    entry.body_de, entry.body_en = _clean(f.get("body_de"), 100_000), _clean(f.get("body_en"), 100_000)
    if f.get("category") in CATEGORIES:
        entry.category = f.get("category")
    keys = {k for k, _ in brand_choices()}
    picked = [k for k in f.getlist("brands") if k in keys]
    if "brands_sent" in f:
        entry.brands = None if not picked or set(picked) == keys else picked
    images = {img.id: img for img in entry.images}
    for img in entry.images:
        if f"alt_de_{img.id}" in f:
            img.alt_de = _one_line(f.get(f"alt_de_{img.id}"), 300)
            img.alt_en = _one_line(f.get(f"alt_en_{img.id}"), 300)
    if "cover" in f:
        cover = f.get("cover", type=int)
        entry.cover_image_id = cover if cover in images else None
    if entry.published_at is None:  # the address follows the German title until the first publication
        if entry.title_de:
            _set_slug(entry, unique_slug(slugify(entry.title_de), entry.id))
    elif "slug" in f:
        slug = f.get("slug", "").strip().lower()
        if not SLUG_RE.match(slug) or len(slug) > 100:
            flash(de_en("Die Adresse darf nur aus kleinen Buchstaben, Ziffern und Bindestrichen bestehen.",
                        "The address may only contain lower-case letters, digits and hyphens."), "error")
        elif _taken(slug, entry.id):
            flash(de_en("Diese Adresse hat schon ein anderer Eintrag.", "Another entry already has this address."), "error")
        else:
            _set_slug(entry, slug)
    if f.get("published_on") and entry.published_at is not None:
        try:
            day = date.fromisoformat(f.get("published_on"))
        except ValueError:
            day = None
        if day is not None and day != to_local(entry.published_at).date():
            entry.published_at = local_to_utc(day, to_local(entry.published_at).time())
    after = (entry.title_de, entry.title_en, entry.summary_de, entry.summary_en, entry.body_de, entry.body_en)
    if entry.status == BlogPost.PUBLISHED and after != before:
        entry.content_changed_at = utcnow()
    entry.updated_at, entry.updated_by_id = utcnow(), current_user.id


@bp.route("/admin/blog/<int:post_id>/translate", methods=["POST"])
@_admin
def admin_translate(post_id):
    """"Ins Englische übersetzen": title, teaser and text of the German version into English, marks kept. It
    replaces the English version (the button asks first when there is one)."""
    from app import ai

    entry = _post_or_404(post_id)
    if "title_de" in request.form:  # sent from the edit page: the fields as they are now
        _save_form(entry, request.form)
        db.session.commit()
    if not (entry.title_de.strip() or entry.body_de.strip()):
        flash(de_en("Erst den deutschen Text schreiben.", "Write the German text first."), "error")
        return redirect(url_for("blog.admin_edit", post_id=entry.id))
    prompt = "\n\n".join((f"TITLE:\n{entry.title_de}", f"SUMMARY:\n{entry.summary_de}", f"TEXT:\n{entry.body_de}"))
    try:
        data = ai.json_answer(ai.generate_text(TRANSLATE_INSTRUCTIONS, prompt, purpose="blog_translation",
                                               schema=TRANSLATE_SCHEMA))
    except ai.AIError as e:
        flash(str(e), "error")
        return redirect(url_for("blog.admin_edit", post_id=entry.id))
    before = (entry.title_en, entry.summary_en, entry.body_en)
    entry.title_en = _one_line(data.get("title"), 200)
    entry.summary_en = _clean(data.get("summary"), 1000).strip() if entry.summary_de.strip() else ""
    entry.body_en = _clean(data.get("body"), 100_000).strip()
    if entry.status == BlogPost.PUBLISHED and before != (entry.title_en, entry.summary_en, entry.body_en):
        entry.content_changed_at = utcnow()
    entry.updated_at, entry.updated_by_id = utcnow(), current_user.id
    db.session.commit()
    flash(de_en("Englische Fassung vorgeschlagen. Bitte lesen und anpassen.",
                "English version suggested. Please read and adjust it."), "success")
    return redirect(url_for("blog.admin_edit", post_id=entry.id))

@bp.app_context_processor
def _blog_globals():
    return {"blog_any_public": any_public, "blog_latest": latest, "blog_view": view,
            "blog_category": category_label, "blog_base": brand.public_base}


MAX_CHANGES_SHOWN = 12  # corrections named in the message after "Textkorrekturen"


@bp.route("/admin/blog/<int:post_id>/proofread/<lang>", methods=["POST"])
@_admin
def admin_proofread(post_id, lang):
    """"Textkorrekturen": a light proofreading of title, teaser and text of one language (de, en) that keeps the
    author's style; only spelling, typing errors, grammar and punctuation (PROOFREAD_INSTRUCTIONS). Works on the fields
    as they are now and names what it corrected, so the author sees every change."""
    from app import ai

    if lang not in PROOFREAD_SPELLING:
        abort(404)
    entry = _post_or_404(post_id)
    if "title_de" in request.form:
        _save_form(entry, request.form)
        db.session.commit()
    title, summary, body = (getattr(entry, f"{field}_{lang}") for field in ("title", "summary", "body"))
    if not (title.strip() or summary.strip() or body.strip()):
        flash(de_en("Hier steht noch kein Text zum Korrigieren.", "There is no text to correct here yet."), "error")
        return redirect(url_for("blog.admin_edit", post_id=entry.id))
    instructions = PROOFREAD_INSTRUCTIONS.format(language="German" if lang == "de" else "English",
                                                 spelling=PROOFREAD_SPELLING[lang])
    prompt = "\n\n".join((f"TITLE:\n{title}", f"SUMMARY:\n{summary}", f"TEXT:\n{body}"))
    try:
        data = ai.json_answer(ai.generate_text(instructions, prompt, purpose="blog_proofreading", schema=PROOFREAD_SCHEMA))
    except ai.AIError as e:
        flash(str(e), "error")
        return redirect(url_for("blog.admin_edit", post_id=entry.id))
    new = (_one_line(data.get("title"), 200) if title.strip() else title,
           _clean(data.get("summary"), 1000).strip() if summary.strip() else summary,
           _clean(data.get("body"), 100_000).strip() if body.strip() else body)
    if not all(new[k] or not old.strip() for k, old in enumerate((title, summary, body))):
        # an empty answer for a field that has text: nothing is replaced, nothing is lost
        flash(de_en("Die KI hat keinen vollständigen Text zurückgegeben; nichts wurde geändert.",
                    "The AI did not return a complete text; nothing was changed."), "error")
        return redirect(url_for("blog.admin_edit", post_id=entry.id))
    if new == (title, summary, body):
        flash(de_en("Keine Korrekturen nötig.", "No corrections needed."), "success")
        return redirect(url_for("blog.admin_edit", post_id=entry.id))
    for field, value in zip(("title", "summary", "body"), new):
        setattr(entry, f"{field}_{lang}", value)
    if entry.status == BlogPost.PUBLISHED:
        entry.content_changed_at = utcnow()
    entry.updated_at, entry.updated_by_id = utcnow(), current_user.id
    db.session.commit()
    changes = [c for c in data.get("changes") or [] if isinstance(c, dict) and c.get("before") != c.get("after")]
    named = "; ".join(de_en("„{a}“ → „{b}“", "“{a}” → “{b}”", a=c.get("before", ""), b=c.get("after", ""))
                      for c in changes[:MAX_CHANGES_SHOWN])
    more = len(changes) - MAX_CHANGES_SHOWN
    flash(de_en("Korrigiert ({n}): {named}{more}. Bitte prüfen: Vor dem Pfeil steht der alte Text.",
                "Corrected ({n}): {named}{more}. Please check: the old text is before the arrow.",
                n=len(changes), named=named, more=de_en(" und {k} weitere", " and {k} more", k=more) if more > 0 else "")
          if changes else de_en("Korrigiert. Bitte prüfen.", "Corrected. Please check."), "success")
    return redirect(url_for("blog.admin_edit", post_id=entry.id))
