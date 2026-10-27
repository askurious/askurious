"""Open data on Zenodo: draft deposit (no DOI reserved) and file upload. Askurious never publishes: the researchers
review the draft on Zenodo and publish it there, which issues the DOI (Philipp, 2026-10-04); "Stand abrufen" reads
the state back (refresh).

Each researcher uses their own Zenodo account, connected via OAuth ("Mit Zenodo verbinden", app/zenodo_auth.py);
no operator token and no personal tokens (2026-10-04). The deposit belongs to the token's account; all researchers
of the survey are listed as creators with their ORCID iDs. API: https://developers.zenodo.org/ (scopes deposit:write, deposit:actions).
"""
import logging

import requests
from flask import current_app
from markupsafe import escape

from app import bounded, brand
from app.lang import de_en
from app.links import participant_url

URLS = {"sandbox": "https://sandbox.zenodo.org/api", "zenodo": "https://zenodo.org/api"}
# one call to Zenodo (app/bounded.py; audit 2026-10-13, IN-7): its answers are JSON of some kilobytes; an upload of
# the data files may take minutes, so the whole call has a quarter of an hour
MAX_ANSWER = 10 * 1024 * 1024
WHOLE_SECONDS = 900
# Spaces and case are normalized by license_id; accepting an identifier never changes its licence.
LICENSES = {"cc-by-4.0": "cc-by-4.0", "cc-by-sa-4.0": "cc-by-sa-4.0", "cc-by-nc-4.0": "cc-by-nc-4.0",
            "cc0": "cc-zero", "cc0-1.0": "cc-zero", "cc-zero": "cc-zero"}


def not_responding():
    return de_en("Zenodo antwortet gerade nicht. Bitte in einigen Minuten erneut versuchen; bleibt es dabei, die Admins "
                 "der Plattform benachrichtigen.",
                 "Zenodo is not responding right now. Please try again in a few minutes; if it persists, notify the "
                 "platform admins.")


class ZenodoError(Exception):
    pass


def license_id(survey):
    """Validate the configured licence before a deposit is created or changed. No implicit permission grant."""
    consent = survey.consent_item
    value = consent.config.get("license") if consent else None
    if value is None or isinstance(value, str) and not value.strip():
        if survey.kind == "interview":
            raise ZenodoError(de_en(
                "Für Interviewleitfäden bitte unter „Fassungen“ eine Materiallizenz wählen und die bestätigte Fassung "
                "hochladen. Eine Datenlizenz ersetzt diese Auswahl nicht.",
                "For interview guides, please select a material licence under “Revisions” and upload the confirmed "
                "revision. A data licence does not replace this selection."))
        raise ZenodoError(de_en(
            "Für den Zenodo-Upload fehlt eine Lizenz. Bitte in den Einwilligungseinstellungen eine Lizenz angeben: "
            "CC BY 4.0, CC BY-SA 4.0, CC BY-NC 4.0 oder CC0 1.0.",
            "A licence is required for Zenodo upload. Please specify a licence in the consent settings: "
            "CC BY 4.0, CC BY-SA 4.0, CC BY-NC 4.0 or CC0 1.0."))
    key = "-".join(value.lower().split()) if isinstance(value, str) else ""
    if key not in LICENSES:
        raise ZenodoError(de_en(
            "Die angegebene Lizenz wird für den Zenodo-Upload nicht unterstützt. Bitte die Lizenz prüfen; unterstützt "
            "werden CC BY 4.0, CC BY-SA 4.0, CC BY-NC 4.0 und CC0 1.0. Es wurde kein Entwurf angelegt oder geändert.",
            "The specified licence is not supported for Zenodo upload. Please check the licence; supported licences "
            "are CC BY 4.0, CC BY-SA 4.0, CC BY-NC 4.0 and CC0 1.0. No draft was created or changed."))
    return LICENSES[key]


class Client:
    def __init__(self, token, target="sandbox"):
        if not token:
            raise ZenodoError(de_en("Kein Zenodo-Token angegeben.", "No Zenodo token given."))
        self.token, self.target = token, target if target in URLS else "sandbox"
        self.base = URLS[self.target]

    def request(self, method, url, **kwargs):
        if not url.startswith("http"):
            url = self.base + url
        from urllib.parse import urlsplit

        # addresses from Zenodo's answers (bucket, files) are followed with the token only on Zenodo itself
        # (security audit 2026-10-04, N6)
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname != urlsplit(self.base).hostname:
            raise ZenodoError(de_en("Zenodo hat auf eine andere Adresse verwiesen; der Vorgang wurde abgebrochen.",
                                    "Zenodo pointed to a different address; the process was stopped."))
        headers = {"Authorization": f"Bearer {self.token}", **kwargs.pop("headers", {})}
        try:
            # the whole call within a limit and the answer at most MAX_ANSWER (app/bounded.py; audit 2026-10-13, IN-7)
            r = bounded.request(method, url, max_bytes=MAX_ANSWER, seconds=WHOLE_SECONDS, headers=headers, timeout=120,
                                **kwargs)
        except requests.RequestException as e:
            logging.getLogger(__name__).warning("Zenodo %s %s not reachable: %s", method, url, e)
            raise ZenodoError(not_responding()) from e
        if r.status_code == 401:
            logging.getLogger(__name__).warning("Zenodo %s %s: 401", method, url)
            raise ZenodoError(de_en("Zenodo lehnt die Verbindung ab. Bitte unter „Konto › Verbindungen“ die Verbindung mit Zenodo trennen und "
                                    "neu verbinden (Sandbox oder Zenodo, je nach Ziel).",
                                    "Zenodo rejects the connection. Please disconnect Zenodo under “Account › Connections” and connect again "
                                    "(sandbox or Zenodo, depending on the target)."))
        if r.status_code >= 400:
            try:
                err = r.json()
                msg = err.get("message", "") + " " + "; ".join(
                    f"{e.get('field', '')}: {' '.join(e.get('messages', [])) if isinstance(e.get('messages'), list) else e.get('message', '')}"
                    for e in err.get("errors", []) or [])
            except ValueError:
                msg = r.text[:300]
            logging.getLogger(__name__).warning("Zenodo %s %s: %s %s", method, url, r.status_code, msg.strip())
            raise ZenodoError(de_en("Zenodo nimmt die Anfrage nicht an: {msg}", "Zenodo does not accept the request: {msg}",
                                    msg=msg.strip() or "–"))
        return r.json() if r.content else {}


def metadata(survey, *, material_licence=None):
    # Only the guide exporter supplies a previously validated, frozen material licence here.
    licence = material_licence if material_licence is not None else license_id(survey)
    creators = []
    for m in survey.members:
        parts = m.display_name.rsplit(" ", 1)
        c = {"name": f"{parts[1]}, {parts[0]}" if len(parts) == 2 else m.display_name}
        if m.orcid_id:  # not for an author without ORCID or whose account was deleted
            c["orcid"] = m.orcid_id
        if m.affiliation:
            c["affiliation"] = m.affiliation
        creators.append(c)
    # escaped first: the description is the researchers' plain text, not HTML (Zenodo cleans it as well)
    description = str(escape((survey.description or "").strip())).replace("\n", "<br>")
    return {
        "upload_type": "dataset",
        "title": f"{survey.title} (survey data and materials)",
        "creators": creators,
        "description": (f"<p>{description}</p>" if description else "")
                       + "<p>Anonymous survey data (completed participations), codebook, and the full questionnaire "
                         "including randomization and flow.</p>",
        # the keywords of the survey (Befragung › Schlagwörter, app/study_plan.archive_of), else the project
        "keywords": _keywords(survey),
        # where the data come from: the survey's address and the tool (Philipp, 2026-10-04)
        "related_identifiers": [
            {"identifier": participant_url(survey), "relation": "isDerivedFrom", "resource_type": "other"},
            {"identifier": brand.public_base(), "relation": "isCompiledBy", "resource_type": "software"}],
        "notes": "Collected with " + brand.name() + " (" + brand.public_base() + "), an open survey tool. "
                 "The survey: " + participant_url(survey) + "." + (
                     f" Field period: {survey.opened_at:%Y-%m-%d} to {survey.closed_at:%Y-%m-%d}." if survey.opened_at and survey.closed_at
                     else f" Field period from {survey.opened_at:%Y-%m-%d}." if survey.opened_at else ""),
        **({"dates": [{"type": "Collected", "start": f"{survey.opened_at:%Y-%m-%d}",
                       **({"end": f"{survey.closed_at:%Y-%m-%d}"} if survey.closed_at else {})}]} if survey.opened_at else {}),
        "access_right": "open",
        "license": licence,
        "language": {"de": "deu", "en": "eng"}.get(survey.default_language, "eng"),
    }


def _keywords(survey):
    from app import study_plan

    words = study_plan.archive_of((survey.study_plan or {}).get("archive"))["keywords"]
    return (words or [k for k in [survey.project] if k and k != "None"])[:8] or ["survey data"]


def package(survey, open_answers=False, include_incomplete=False, require_public_image_rights=True):
    """[(filename, bytes)] for the deposit. open_answers: with the columns of open answers (free text, postal codes,
    exact dates, map positions), in data.csv and every long format alike. include_incomplete (Coscine only, the tick
    "Abgebrochene Teilnahmen mitnehmen"): data.csv and every long format with the unfinished participations, so
    resp_id is the same person in all of them."""
    from app import asset_rights, export, transfer

    if require_public_image_rights:
        asset_rights.require_public(survey)

    files = [
        ("data.csv", export.to_csv(survey, include_incomplete=include_incomplete, sources=("real",), public=True,
                                   open_answers=open_answers).encode("utf-8")),
        ("codebook.csv", export.codebook_csv(survey, public=True, open_answers=open_answers).encode("utf-8")),
        ("questionnaire.md", export.questionnaire_md(survey, public=True).encode("utf-8")),
        ("README.md", export.readme_md(survey, open_answers, include_incomplete).encode("utf-8")),
        # the questionnaire as a survey file that can be read in again (E3): without the wording of instruments that
        # may not be passed on, without images (they are in images.zip)
        ("survey.json", transfer.dumps(survey, with_files=False, public=True).encode("utf-8")),
    ]
    from app import cdif, ddi, ddi_codebook, packages

    extra = list(packages.study_material(survey, "en", public=True))  # the PDF of what participants saw, with notes (E10 C)
    if export.used_constructs(survey) or export.used_constructs(survey, linked=True):
        extra.append(("constructs.md", export.constructs_md(survey, public=True).encode("utf-8")))
    from app import coding

    guide = coding.guide_md(survey, examples=open_answers)  # the categories of open answers
    if guide:
        extra.append(("kodierleitfaden.md", guide.encode("utf-8")))
    # the same for archives and catalogues (Ralf 2026-10-27): DDI Lifecycle 4.0, DDI Codebook 2.5 (CESSDA) and CDIF,
    # describing the columns of codebook.csv, without the wording codebook.csv and questionnaire.md leave out; both
    # DDI files list the other files of the package as material
    material = [name for name, _ in files + extra]
    files += [("ddi-lifecycle.xml", ddi.build(survey, lang="en", public=True, open_answers=open_answers, material=material)),
              ("ddi-codebook.xml", ddi_codebook.build(survey, lang="en", public=True, open_answers=open_answers,
                                                      material=material)),
              ("cdif.jsonld", cdif.build(survey, lang="en", public=True, open_answers=open_answers))]
    files += extra
    from app import packages

    files += [(name, text.encode("utf-8")) for name, text in packages.long_files(
        survey, include_incomplete=include_incomplete, open_answers=open_answers)]
    for node in export.loop_tables(survey):  # the table of each loop, no answers
        files.append((export.loop_table_name(node), export.loop_table_csv(survey, node).encode("utf-8")))
    return files + seal_files(survey, files, completed=not include_incomplete) + images(survey, require_public_image_rights)


def seal_files(survey, files, completed=True):
    """The seal chain for the archive (Philipp, 2026-10-08): seal.json (hash values and timestamps, no answers) and
    the checker, so data.csv can be checked for completeness without Askurious; the README gets the check code and
    its link. The newest state is stamped first. [] without a chain. completed: data.csv holds the completed
    participations only, so the README names the check for such a file (seal_verify --completed, audit 2026-10-10,
    E-4): the plain check would miss every screen-out, "quota full" and exclusion."""
    import json
    import os

    from app import seal
    from app.extensions import db

    if not getattr(survey, "seal", False) or not seal.has_chain(survey):
        return []
    try:
        seal.anchor(survey.uid)
    except Exception as exc:  # the archive goes without the newest stamp; the hourly round catches up
        db.session.rollback()
        current_app.logger.warning("timestamp of the seal chain %s before the archive failed: %s", survey.uid, exc)
    data = seal.chain(survey, url=seal.chain_url(survey))  # where seal_verify.py finds the chain to compare (S2)
    stamped = data["anchors"][-1]["seq"] if data["anchors"] else None
    code = seal.cite_code(survey, stamped) if stamped else None
    with open(os.path.join(current_app.root_path, "seal_verify.py"), "rb") as f:
        checker = f.read()
    note = ("\n\n## Seal chain\n\n`seal.json` is the seal chain of this survey: every case that ended has a case number and "
            "a hash (columns `seal_seq` and `seal_hash` in `data.csv`), every deletion a deletion record; the chain is "
            "stamped by the timestamp service of the DFN (RFC 3161). It holds hash values and timestamps only, no answers.\n\n"
            "Check that no case is missing from `data.csv` without a deletion record, without Askurious:\n\n"
            + ("    python seal_verify.py --completed seal.json data.csv\n\n"
               "`data.csv` holds the completed participations only, so the chain's cases that ended otherwise "
               "(screen-out, quota full, excluded) are not in it by design. `--completed` checks every completed "
               "case and counts the others without missing them; its verdict \"holds for the cases in the file\" "
               "says nothing about those others. Without `--completed` the check names them as missing.\n"
               if completed else "    python seal_verify.py seal.json data.csv\n"))
    if code:
        note += (f"\nCheck code of the stamped state: `{code}`, online: "
                 f"{brand.public_base()}/verify/{code}\n")  # also outside a request (Coscine)
    for n, (name, body) in enumerate(files):
        if name == "README.md":
            files[n] = (name, body + note.encode("utf-8"))
    return [("seal.json", json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")), ("seal_verify.py", checker)]


def _jpeg(data):
    import io

    from PIL import Image

    from app import image_rights

    out = io.BytesIO()
    with Image.open(io.BytesIO(data)) as original:
        rights = image_rights.read(original)
        image = original.convert("RGB")
        image.info.clear()
        image.save(out, format="JPEG", quality=92, optimize=True, **image_rights.save_options(rights, "JPEG"))
    return out.getvalue()


def images(survey, require_public_rights=False):
    """[("images.zip", bytes)]: the approved images of the questionnaire (uploaded or generated) as
    image_<variable>_<n>.<ext> in one ZIP (Zenodo limits the files per record), with images.csv naming each one's
    element, variant, source and, for generated ones, model, style and prompt. Existing embedded attribution
    is also listed separately; it records supplied information, not a new licence grant. [] without images."""
    import csv
    import io
    import os

    from app import asset_rights, image_rights
    from app.media import upload_folder
    from app.models import Asset

    out, rows = [], []
    folder = upload_folder(survey.uid)
    for block in survey.blocks:
        for item in block.items:
            if item.type == "media":  # video and audio stimuli are no images (items/stimulus.py); too large for this ZIP
                continue
            n = 0
            for asset in item.assets:
                path = os.path.join(folder, asset.filename)
                if asset.status != Asset.APPROVED or not os.path.isfile(path):
                    continue
                n += 1
                ext = os.path.splitext(asset.filename)[1].lower()
                with open(path, "rb") as f:
                    data = f.read()
                if require_public_rights:
                    import hashlib

                    if not asset_rights.declared(survey, asset, hashlib.sha256(data).hexdigest()):
                        raise asset_rights.RightsError(de_en("Bildrechte wurden während des Exports geändert. Bitte erneut prüfen.",
                                                            "Image rights changed during export. Please review them again."))
                rights = image_rights.from_bytes(data)
                if ext == ".webp":  # the archive gets JPEG, which opens everywhere (Philipp, 2026-10-08)
                    data, ext = _jpeg(data), ".jpg"
                name = f"image_{item.code}_{n}{ext}"
                out.append((name, data))
                rows.append([name, item.code, asset.key or "", asset.source, asset.model or "", asset.style or "", asset.prompt or "",
                             *asset_rights.archive_values(asset, rights)])
    if not rows:
        return []
    import zipfile

    from app.export import safe_text

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["file", "variable", "variant", "source", "model", "style", "prompt",
                     "rights_creator", "rights_copyright", "rights_credit", "rights_source", "rights_license", "rights_license_url",
                     "rights_basis", "rights_permission", "rights_declared_by", "rights_declared_at", "rights_original_sha256"])
    writer.writerows([safe_text(v) for v in row] for row in rows)  # formula protection: a prompt may start with "-"
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in out:
            z.writestr(name, data)
        z.writestr("images.csv", buf.getvalue())
    return [("images.zip", packed.getvalue())]


def upload(survey, client, open_answers=False):
    """Create (or refresh) the draft deposit with metadata and files; returns the stored state."""
    meta = metadata(survey)  # validate before even creating an empty draft or touching an existing one
    from app import asset_rights

    try:
        files = package(survey, open_answers)  # complete rights preflight before touching an existing remote draft
    except asset_rights.RightsError as exc:
        raise ZenodoError(str(exc)) from exc
    state = survey.zenodo or {}
    dep = None
    if state.get("id") and state.get("target") == client.target:
        dep = client.request("GET", f"/deposit/depositions/{state['id']}")
        if dep.get("submitted"):
            raise ZenodoError(de_en("Dieser Datensatz ist bereits veröffentlicht. Eine neue Version bitte direkt auf Zenodo anlegen.",
                                    "This data set is already published. Please create a new version directly on Zenodo."))
    if dep is None:
        dep = client.request("POST", "/deposit/depositions", json={})
    client.request("PUT", f"/deposit/depositions/{dep['id']}", json={"metadata": meta})
    for f in dep.get("files", []):
        client.request("DELETE", f["links"]["self"])
    from app import packages

    for name, data in files + packages.checksums(files):  # the same checksum file as every package (E7)
        client.request("PUT", f"{dep['links']['bucket']}/{name}", data=data,
                       headers={"Content-Type": "application/octet-stream"})
    return {**_state(client.request("GET", f"/deposit/depositions/{dep['id']}"), client.target),
            "open_answers": bool(open_answers)}


def refresh(survey, client):
    """The deposit's state as Zenodo has it now: still a draft, or published by the researchers (with its DOI)."""
    state = survey.zenodo or {}
    if not state.get("id"):
        raise ZenodoError(de_en("Noch kein Entwurf vorhanden.", "No draft yet."))
    return {**_state(client.request("GET", f"/deposit/depositions/{state['id']}"), client.target),
            "open_answers": bool(state.get("open_answers"))}


def _state(dep, target):
    doi = dep.get("doi") or ""  # only once published on Zenodo: Askurious reserves no DOI (Philipp, 2026-10-04)
    links = dep.get("links") or {}
    return {"id": dep["id"], "doi": doi, "state": "published" if dep.get("submitted") else "draft",
            "url": links.get("html") or links.get("record_html", ""), "target": target,
            "files": [f.get("filename") for f in dep.get("files", [])]}


# --- a version of an interview guide (app/interview_versions.py, concept interviewleitfaden-konzept.md 6.3) ------


def guide_metadata(survey, version):
    """The record of a frozen version of an interview guide: the guide is the material, there are no data."""
    from app import interview_versions

    grant = interview_versions.material_license(version)
    meta = metadata(survey, material_licence=grant["id"])
    question = str(escape((survey.research_question or "").strip())).replace("\n", "<br>")
    meta.update(
        upload_type="other",
        # the uploader's language (E8): what a researcher reads on Zenodo; German outside a request
        title=de_en("{title} (Interviewleitfaden, Fassung {n})", "{title} (interview guide, revision {n})",
                    title=survey.title, n=version["n"]),
        description=(f"<p>{question}</p>" if question else "")
        + de_en("<p>Leitfaden für leitfadengestützte Interviews, Fassung {n} vom {at}. Die ZIP-Datei enthält den "
                "Leitfaden (Word, Vier-Spalten-Schema nach Helfferich), die Interviewkarte, das Kategoriensystem (REFI-QDA, .qdc) und "
                "die Befragungsdatei, jeweils mit SHA-256-Prüfsumme. Erhoben wurden mit Askurious keine Daten.</p>",
                "<p>Guide for guided interviews, revision {n} of {at}. The ZIP file holds the guide (Word, four-column "
                "scheme after Helfferich), the interview card, the category system (REFI-QDA, .qdc) and the survey file, "
                "each with a SHA-256 checksum. No data were collected with Askurious.</p>",
                n=version["n"], at=version["at"][:10])
        + de_en("<p>Die Materiallizenz gilt ausschließlich für eigene Materialien. Fremdbestandteile behalten ihre "
                "gesonderten Rechte; Quellen und Bedingungen stehen in LICENSE.txt und material-license.json im ZIP.</p>",
                "<p>The material licence applies only to the contributors' own material. Third-party components retain "
                "their separate rights; see LICENSE.txt and material-license.json in the ZIP for credits and conditions.</p>"),
        keywords=["interview guide", "qualitative research", "Interviewleitfaden"],
        related_identifiers=[{"identifier": brand.public_base(), "relation": "isCompiledBy", "resource_type": "software"}],
        notes=f"Created with {brand.name()} ({brand.public_base()}). SHA-256 of {version['name']}: {version['sha256']}.",
        license=grant["id"])
    meta.pop("dates", None)
    return meta


def upload_guide(survey, client, version, path):
    """Create (or refresh) the draft deposit of one version: its metadata and its ZIP as it was frozen."""
    from app import interview_versions

    meta = guide_metadata(survey, version)  # same preflight as data uploads; no implicit licence for guides
    interview_versions.verify_material_license(version, path)
    state = version.get("zenodo") or {}
    dep = None
    if state.get("id") and state.get("target") == client.target:
        dep = client.request("GET", f"/deposit/depositions/{state['id']}")
        if dep.get("submitted"):
            raise ZenodoError(de_en("Diese Fassung ist auf Zenodo bereits veröffentlicht.", "This revision is already published on Zenodo."))
    if dep is None:
        dep = client.request("POST", "/deposit/depositions", json={})
    client.request("PUT", f"/deposit/depositions/{dep['id']}", json={"metadata": meta})
    for f in dep.get("files", []):
        client.request("DELETE", f["links"]["self"])
    with open(path, "rb") as f:
        client.request("PUT", f"{dep['links']['bucket']}/{version['name']}", data=f.read(),
                       headers={"Content-Type": "application/octet-stream"})
    return _state(client.request("GET", f"/deposit/depositions/{dep['id']}"), client.target)


def refresh_guide(client, version):
    state = version.get("zenodo") or {}
    if not state.get("id"):
        raise ZenodoError(de_en("Noch kein Entwurf vorhanden.", "No draft yet."))
    return _state(client.request("GET", f"/deposit/depositions/{state['id']}"), client.target)

