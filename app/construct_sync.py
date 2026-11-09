"""Sync with the construct catalogue (askurious-constructs): fetch a signed release, show what changes, take it.

Where from: CONSTRUCTS_SOURCE, by default the latest release on GitHub (construct_catalog.RELEASES + "/latest/download",
the files manifest.json and manifest.json.sig); "off" switches fetching off; a folder or a manifest.json on the server
works too (with manifest.json.sig next to it). CONSTRUCTS_PIN names a release to stay on; going back under
Administration > Konstrukte > Katalog pins the release gone back to (site setting constructs_pin) until the pin is
released. Each fetch goes through webservice.fetch_full (https only, no address of this server or a protected network,
a time limit, MAX_MANIFEST bytes at most) and follows at most MAX_REDIRECTS redirects, each checked anew. A manifest
counts only with a valid signature of the catalogue's key (construct_catalog.verify_signature), also an uploaded one.

Taking a manifest stores it as a row of construct_sync (status "applied"), which makes it the active catalogue for
every process within construct_catalog.ACTIVE_SECONDS, and seeds the toolbox anew (constructs.ensure_seed): new
constructs come in, changed ones get their new version (licensed scales only with an installation's wording that
matches the documented hash), withdrawn ones leave the toolbox but stay in the surveys that use them. Surveys keep the
copy of an element they have; nothing in them changes. A construct that is missing from a newer manifest is kept as
withdrawn. Every row keeps what changed; "Rückgängig" takes the manifest before again and pins its release.

Nightly: housekeeping (lifecycle._rare_sweeps) calls nightly() every hour; it syncs once a night between NIGHT_FROM
and NIGHT_TO o'clock (Europe/Berlin), only in the housekeeping process or the cron route, never in a participant's or
researcher's request. No personal data is sent or kept: the server only downloads two files.
"""
import json
import os
from datetime import timedelta
from urllib.parse import urljoin

from flask import current_app

from app import construct_catalog as catalog
from app.extensions import db
from app.lang import de_en
from app.models import ConstructSync, SiteSetting, utcnow

DEFAULT_SOURCE = catalog.RELEASES + "/latest/download"
PIN_SETTING = "constructs_pin"
MAX_MANIFEST = 5_000_000
MAX_SIGNATURE = 4096
MAX_REDIRECTS = 5
TIMEOUT = 30
NIGHT_FROM, NIGHT_TO = 2, 5
KINDS = ("new", "structure", "wording", "retired", "metadata")


class SyncError(Exception):
    """A sync that cannot go on; the message is for the admin."""


# ---- where from ----

def source():
    value = (current_app.config.get("CONSTRUCTS_SOURCE") or "").strip()
    return value or DEFAULT_SOURCE


def is_off():
    return source().lower() == "off"


def pin():
    """The release to stay on: the site setting set by going back, else CONSTRUCTS_PIN, else None."""
    row = db.session.get(SiteSetting, PIN_SETTING)
    if row is not None and (row.value or "").strip():
        return row.value.strip()
    return (current_app.config.get("CONSTRUCTS_PIN") or "").strip() or None


def pinned_by_setting():
    row = db.session.get(SiteSetting, PIN_SETTING)
    return bool(row is not None and (row.value or "").strip())


def set_pin(release):
    row = db.session.get(SiteSetting, PIN_SETTING)
    if not release:
        if row is not None:
            db.session.delete(row)
        return
    if row is None:
        row = SiteSetting(key=PIN_SETTING)
        db.session.add(row)
    row.value = release
    row.changed_at = utcnow()


def _location():
    """(kind, where): ("url", base address of the two files) or ("file", path of manifest.json)."""
    where, wanted = source(), pin()
    if where.lower() == "off":
        raise SyncError(de_en("Der Abruf ist abgeschaltet (CONSTRUCTS_SOURCE=off).",
                              "Fetching is switched off (CONSTRUCTS_SOURCE=off)."))
    if where.startswith("https://"):
        if wanted and where.rstrip("/") == DEFAULT_SOURCE:
            return "url", f"{catalog.RELEASES}/download/{wanted}/"
        return "url", where.rstrip("/") + "/"
    if where.startswith("http://"):
        raise SyncError(de_en("Nur https-Adressen.", "https addresses only."))
    path = os.path.join(where, "manifest.json") if os.path.isdir(where) else where
    return "file", path


def _get(url, limit):
    """The body of `url`, following at most MAX_REDIRECTS redirects, each address checked (webservice)."""
    from app import webservice

    for _ in range(MAX_REDIRECTS + 1):
        try:
            status, headers, data = webservice.fetch_full("GET", url, headers={"Accept": "application/octet-stream",
                                                                               "User-Agent": "Askurious-Constructs/1"},
                                                          timeout=TIMEOUT, max_bytes=limit)
        except (webservice.Blocked, webservice.Unreachable, OSError, ValueError) as e:
            raise SyncError(de_en("Abruf fehlgeschlagen: {e}", "Fetching failed: {e}", e=e)) from e
        if status in (301, 302, 303, 307, 308) and headers.get("location"):
            url = urljoin(url, headers["location"])
            continue
        if status != 200:
            raise SyncError(de_en("Abruf fehlgeschlagen: HTTP {s}", "Fetching failed: HTTP {s}", s=status))
        return data
    raise SyncError(de_en("Zu viele Weiterleitungen.", "Too many redirects."))


def fetch():
    """(manifest bytes, signature text, where) from CONSTRUCTS_SOURCE (and the pin)."""
    kind, where = _location()
    if kind == "url":
        return _get(where + "manifest.json", MAX_MANIFEST), _get(where + "manifest.json.sig", MAX_SIGNATURE).decode(
            "utf-8", "replace"), where
    try:
        with open(where, "rb") as f:
            data = f.read(MAX_MANIFEST + 1)
        with open(where + ".sig", encoding="utf-8") as f:
            signature = f.read(MAX_SIGNATURE)
    except OSError as e:
        raise SyncError(de_en("Datei nicht lesbar: {e}", "File not readable: {e}", e=e)) from e
    return data, signature, where


# ---- reading and comparing ----

def read(data, signature):
    """(manifest, trusted comment) of a signed manifest; SyncError when it does not hold."""
    if len(data) > MAX_MANIFEST:
        raise SyncError(de_en("Das Manifest ist zu groß.", "The manifest is too large."))
    try:
        comment = catalog.verify_signature(data, signature or "")
    except ValueError as e:
        raise SyncError(de_en("Signatur ungültig: {e}. Nichts übernommen.", "Invalid signature: {e}. Nothing taken.",
                              e=e)) from e
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise SyncError(de_en("Kein gültiges JSON.", "Not valid JSON.")) from e
    problems = catalog.check_manifest(manifest)
    if problems:
        raise SyncError(de_en("Manifest abgelehnt: {p}", "Manifest refused: {p}", p="; ".join(problems[:5])))
    return manifest, comment


def merge(new, old):
    """`new` with every construct of `old` it lacks, kept as withdrawn: a key never disappears (E11)."""
    keys = {c["key"] for c in new["constructs"]}
    kept = [{**c, "status": "retired"} for c in old["constructs"] if c["key"] not in keys]
    return {**new, "constructs": new["constructs"] + kept} if kept else new


def changes(old, new):
    """[{key, kind, name, before, after}] from `old` to `new`, kind one of KINDS."""
    before = {c["key"]: c for c in old["constructs"]}
    out = []
    for c in new["constructs"]:
        b = before.get(c["key"])
        name = (c.get("name") or {}).get("de") or c["key"]
        entry = {"key": c["key"], "name": name, "before": b and b.get("version"), "after": c.get("version")}
        if b is None:
            kind = "retired" if c.get("status") == "retired" else "new"
        elif c.get("status") == "retired" and b.get("status") != "retired":
            kind = "retired"
        elif catalog.structure_hash(c) != catalog.structure_hash(b):
            kind = "structure"
        elif (c.get("hash") or {}).get("wording") != (b.get("hash") or {}).get("wording") or c.get("wording") != b.get("wording"):
            kind = "wording"
        elif {k: v for k, v in c.items() if k != "hash"} != {k: v for k, v in b.items() if k != "hash"}:
            kind = "metadata"
        else:
            continue
        out.append({**entry, "kind": kind})
    return out


def kind_label(kind):
    return {"new": de_en("neu", "new"), "structure": de_en("Struktur", "structure"),
            "wording": de_en("Wortlaut", "wording"), "retired": de_en("zurückgezogen", "withdrawn"),
            "metadata": de_en("Angaben", "details")}.get(kind, kind)


def uses(key):
    """How many surveys have an element of construct `key`."""
    from app.models import Block, Item

    return db.session.scalar(db.select(db.func.count(db.distinct(Block.survey_id))).join(Item, Item.block_id == Block.id)
                             .filter(Item.config["construct"]["key"].as_string() == key)) or 0


# ---- rows ----

def _row(trigger, status, where="", manifest=None, comment="", found=None, error="", read=None):
    """A row of construct_sync; `manifest` is stored, `read` (a manifest not kept) only names release and commit."""
    named = manifest or read
    rel, commit = catalog.release(named) if named else (None, None)
    row = ConstructSync(trigger=trigger, status=status, release=rel, commit=commit, source=(where or "")[:500],
                        signature=(comment or "")[:300], changes=found, manifest=manifest, error=(error or "")[:2000])
    db.session.add(row)
    return row


def current():
    """The row of the active manifest, or None (the snapshot in the code is active)."""
    return db.session.scalar(db.select(ConstructSync).filter_by(status=ConstructSync.APPLIED)
                             .order_by(ConstructSync.id.desc()).limit(1))


def previous():
    """The applied row before the active one, to go back to; None if there is none."""
    rows = db.session.scalars(db.select(ConstructSync).filter_by(status=ConstructSync.APPLIED)
                              .order_by(ConstructSync.id.desc()).limit(2)).all()
    return rows[1] if len(rows) > 1 else None


def last_attempt():
    return db.session.scalar(db.select(ConstructSync).filter(ConstructSync.status != ConstructSync.PREVIEW)
                             .order_by(ConstructSync.id.desc()).limit(1))


def history(limit=10):
    return db.session.scalars(db.select(ConstructSync).filter(ConstructSync.status != ConstructSync.PREVIEW)
                              .order_by(ConstructSync.id.desc()).limit(limit)).all()


# ---- the steps ----

def _check_release(manifest):
    wanted = pin()
    if wanted and manifest.get("release") != wanted:
        raise SyncError(de_en("Festgelegt auf Release {p}, das Manifest ist {r}.",
                              "Pinned to release {p}, the manifest is {r}.", p=wanted, r=manifest.get("release")))


def prepare(data, signature, trigger, where=""):
    """Read a manifest and keep it as a preview (status "preview"): the admin sees what changes and takes it with
    apply(). Returns the row; SyncError (recorded as "failed") when it does not hold."""
    try:
        manifest, comment = read(data, signature)
        if trigger != "upload":
            _check_release(manifest)
    except SyncError as e:
        _row(trigger, ConstructSync.FAILED, where, error=str(e))
        db.session.commit()
        raise
    active = catalog.active()
    manifest = merge(manifest, active)
    found = changes(active, manifest)
    status = ConstructSync.PREVIEW if found or catalog.release(manifest) != catalog.release(active) else ConstructSync.UNCHANGED
    row = _row(trigger, status, where, manifest if status == ConstructSync.PREVIEW else None, comment, found,
               read=manifest)
    db.session.commit()
    return row


def check(trigger="manual"):
    """Fetch from CONSTRUCTS_SOURCE and keep it as a preview (prepare)."""
    try:
        data, signature, where = fetch()
    except SyncError as e:
        _row(trigger, ConstructSync.FAILED, source(), error=str(e))
        db.session.commit()
        raise
    return prepare(data, signature, trigger, where)


def apply(row):
    """Take the manifest of a preview row: it becomes the active catalogue; the toolbox is seeded anew."""
    from app import constructs

    if row is None or row.status != ConstructSync.PREVIEW or not row.manifest:
        raise SyncError(de_en("Diese Vorschau gibt es nicht mehr.", "This preview no longer exists."))
    active = catalog.active()
    row.changes = changes(active, row.manifest)  # against what is active now
    row.status = ConstructSync.APPLIED
    row.created_at = utcnow()
    db.session.commit()
    catalog.invalidate()
    constructs.ensure_seed()
    return row


def undo():
    """Take the manifest before the active one again and pin its release (until the pin is released)."""
    from app import constructs

    back = previous()
    if back is None:
        raise SyncError(de_en("Es gibt keinen früheren Stand.", "There is no earlier version."))
    active = catalog.active()
    row = _row("undo", ConstructSync.APPLIED, back.source, back.manifest, back.signature, changes(active, back.manifest))
    set_pin(back.release)
    db.session.commit()
    catalog.invalidate()
    constructs.ensure_seed()
    return row


def unpin():
    set_pin(None)
    db.session.commit()


def sync(trigger="auto"):
    """Fetch and take at once (the nightly run): a preview with changes is applied. Returns the row."""
    row = check(trigger)
    if row.status == ConstructSync.PREVIEW:
        return apply(row)
    return row


def nightly(now=None):
    """Once a night (NIGHT_FROM to NIGHT_TO o'clock, Europe/Berlin): sync(); only in the housekeeping process or the
    cron route. Failures are recorded, never raised."""
    from flask import has_request_context, request

    if has_request_context() and request.blueprint != "cron":
        return None
    if is_off():
        return None
    from zoneinfo import ZoneInfo

    now = now or utcnow()
    local = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Berlin"))
    if not NIGHT_FROM <= local.hour < NIGHT_TO:
        return None
    done = db.session.scalar(db.select(ConstructSync.id).filter(ConstructSync.trigger == "auto",
                                                                ConstructSync.created_at > now - timedelta(hours=20)).limit(1))
    if done is not None:
        return None
    try:
        return sync("auto")
    except SyncError as e:
        current_app.logger.warning("construct catalogue: nightly sync failed: %s", e)
        return None


def drop_old_previews(keep=5):
    """Previews nobody took: only the last `keep` stay (their manifests are large)."""
    old = db.session.scalars(db.select(ConstructSync).filter_by(status=ConstructSync.PREVIEW)
                             .order_by(ConstructSync.id.desc()).offset(keep)).all()
    for row in old:
        db.session.delete(row)


# ---- for the page ----

def licensed_state():
    """(matching, differing, missing) counts of the licensed constructs of the active catalogue, against the
    installation's wordings."""
    from app import constructs

    state = constructs.wording_state()
    return len(state["ok"]), len(state["differing"]), len(state["missing"])


def counts():
    """{"catalog", "instance", "orgs"}: constructs of the toolbox by where they come from (linked elements left out)."""
    from app import linked
    from app.models import Construct

    rows = db.session.execute(db.select(Construct.source, Construct.visibility, Construct.key)
                              .filter(Construct.visibility.in_(("public", "org")))).all()
    own = [r for r in rows if not r.key.startswith(linked.PREFIX)]
    return {"catalog": sum(1 for r in own if r.source == "catalog" and r.visibility == "public"),
            "instance": sum(1 for r in own if r.source != "catalog" and r.visibility == "public"),
            "orgs": sum(1 for r in own if r.visibility == "org")}


def status():
    """What the page Administration > Konstrukte > Katalog shows."""
    row = current()
    rel, commit = catalog.release()
    return {"release": rel, "commit": commit, "row": row, "snapshot": row is None, "source": source(),
            "off": is_off(), "pin": pin(), "pinned_here": pinned_by_setting(), "last": last_attempt(),
            "previous": previous(), "counts": counts(), "licensed": licensed_state(), "history": history(),
            "check": current_app.config.get("INSTRUMENT_WORDING_CHECK", True)}
