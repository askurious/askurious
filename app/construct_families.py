"""Short and long versions of one instrument, linked (user test 2026-10-27, point 13: "Kurzskalen und Langskalen nicht
nur zwei Einträge, sondern miteinander verknüpft").

A construct belongs to a family (e.g. "Big Five") with a label of its own version (e.g. "BFI-10, 10 Items"). Site
admins set both on the construct's page under Administration > Konstrukte; they are kept as a site setting
("fam:<key>" = "<family>|<version>", app/models.SiteSetting), so no seed update of the construct overwrites them.
The list of constructs shows the other versions on each card, the construct's page compares them side by side.

Families of the construct catalogue (askurious-constructs families/, in the manifest; concept konzept-konstrukt-repo,
E10) count for every installation. A family set here for a construct (the site setting) stays and comes first, so
nothing set before is lost; catalogue_file() gives the families set here for constructs of the catalogue as files for
families/ of the catalogue, to take them there by pull request."""
from app.extensions import db
from app.models import SiteSetting, utcnow

PREFIX = "fam:"
MAX_KEY = 40


def _key(construct_key):
    key = PREFIX + construct_key
    return key if len(key) <= MAX_KEY else None


def catalogue():
    """{construct key: (family, version)} from the families of the active construct catalogue, in the language of the
    page."""
    from app import construct_catalog
    from app.lang import english

    lang = "en" if english() else "de"
    out = {}
    for fam in construct_catalog.active().get("families") or []:
        name = (fam.get("name") or {}).get(lang) or (fam.get("name") or {}).get("de") or fam.get("id", "")
        for member in fam.get("members") or []:
            label = (member.get("label") or {}).get(lang) or (member.get("label") or {}).get("de") or ""
            out[member["key"]] = (name, label)
    return out


def local():
    """{construct key: (family, version)} set on this installation (site settings fam:<key>)."""
    out = {}
    for row in db.session.scalars(db.select(SiteSetting).filter(SiteSetting.key.like(PREFIX + "%"))):
        family, _, version = (row.value or "").partition("|")
        if family.strip():
            out[row.key[len(PREFIX):]] = (family.strip(), version.strip())
    return out


def all_():
    """{construct key: (family, version)} of every construct with a family: the catalogue's, and over them those set
    on this installation."""
    return {**catalogue(), **local()}


def catalogue_file():
    """(file name, bytes): a ZIP with families/<id>.yaml for askurious-constructs from the families set here for
    constructs of the catalogue (written as JSON, which is valid YAML); None if there are none."""
    import io
    import json
    import re
    import zipfile

    from app import construct_catalog

    keys = {c["key"] for c in construct_catalog.active()["constructs"]}
    groups = {}
    for key, (family, version) in sorted(local().items()):
        if key in keys:
            groups.setdefault(family, []).append({"key": key, "label": {"de": version, "en": version}})
    groups = {f: m for f, m in groups.items() if len(m) >= 2}  # a family needs two versions at least
    if not groups:
        return None
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for family, members in groups.items():
            fid = re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-") or "familie"
            data = {"id": fid, "name": {"de": family, "en": family}, "members": members}
            head = "# askurious-constructs families/ (JSON, valid YAML); please check the English\n"
            z.writestr(f"families/{fid}.yaml", head + json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return "askurious-constructs-families.zip", out.getvalue()


def of(construct_key, known=None):
    known = all_() if known is None else known
    return known.get(construct_key)


def siblings(construct_key, known=None):
    """[(key, version)] of the other constructs of the same family, by version label."""
    known = all_() if known is None else known
    mine = known.get(construct_key)
    if not mine:
        return []
    return sorted(((k, v[1]) for k, v in known.items() if v[0] == mine[0] and k != construct_key), key=lambda kv: kv[1])


def put(construct_key, family, version, user=None):
    """Set (or with an empty family remove) the family of a construct. False if its key is too long to keep."""
    key = _key(construct_key)
    if key is None:
        return False
    family, version = " ".join((family or "").split())[:100], " ".join((version or "").split())[:95]
    row = db.session.get(SiteSetting, key)
    if not family:
        if row is not None:
            db.session.delete(row)
        return True
    if row is None:
        row = SiteSetting(key=key)
        db.session.add(row)
    row.value = f"{family}|{version}"
    row.changed_by_id = getattr(user, "id", None)
    row.changed_at = utcnow()
    return True
