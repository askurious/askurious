"""Duplicate a survey: a new draft with the questionnaire and the settings of another survey, and nothing of
what was collected with it.

The copy shares no row and no file with its source. Everything gets new ids (survey, blocks, questions,
images; ids of deleted rows are never given again, models.IdFloor), and what points at blocks and questions by
id is rewritten to the new ones: the flow tree, the conditions in it, the questions a feedback element shows
and the marks of the translation page. Image and logo files are copied into the folder of the copy under new
names. Answers hang on the id of the survey and of the question, so the two surveys can never read or write
each other's.

Copied: everything a researcher set up (project, languages, options, flow, blocks, questions with their choice
designs, approved and pending images, theme with logos, entry parameters and redirects, end texts, separated
section, translations and their status, the dashboards without their links). The title is new, so its
translations are not taken.
Not copied: participations of any kind (real, test, dummy) and their count of declined consents, contact
data, saved states, HB results, comments from test runs, the trash, the link label, the Zenodo record, field
time and status history, pins and edit marks, links that show the quota state or a dashboard, and no secret:
neither the key that signs completion links (panel.sign.key) nor the keys of external sections that check the
returned values (external.py, "sign"). Whoever may edit a survey becomes admin of the copy; with the key of the
original they could make signed links the panel takes for those of the original's admins. The copy says how links
were signed; its admin enters the panel's key again, or turns signing of returned values on for a new key.
The contact address of the consent and of a live vote's privacy page comes along only for an admin of the
original (audit 2026-10-05, L-12): it belongs to the people responsible there, and the survey file leaves it out.
Elements of an experimental type and web services in the flow come along only for whoever has the feature switched
on (app/experimental.py), as when a survey file is read in (code audit 2026-10-08, W3 and X); the others are told.
"""
import copy as _copy
import os
import secrets
import shutil

from app import answer_lock, asset_rights, transfer
from app.extensions import db
from app.lang import de_en, de_en_n
from app.media import upload_folder
from app.models import Asset, Block, EditMark, Item, Quota, QuotaCell, Survey, SurveyGroup, SurveyMember, TranslationMark

TITLE_SUFFIX = " (Kopie)"
# copied as they are; everything else of Survey is either new (ids, dates, status) or handled below
SETTINGS = transfer.SETTINGS + transfer.DUPLICATE_ONLY  # the one list (transfer.HANDLED, NOT_TAKEN)
ASSET_FIELDS = tuple(f for f in transfer.ASSET_FIELDS if f != "rights")  # the rights by asset_rights.copied


# why the button is greyed and the page refuses: duplicating creates a survey of one's own (Ralf, 2026-10-06: it
# stays bound to the right to create, otherwise it would be a way round it)
NOT_ALLOWED = ("Duplizieren legt eine eigene Befragung an, und Ihr Konto darf keine eigenen Befragungen anlegen. "
               "Das schaltet ein Admin der Plattform frei (siehe Impressum).")
NOT_ALLOWED_EN = ("Duplicating creates a survey of your own, and your account may not create surveys of its own. "
                  "An admin of the platform can enable this (see legal notice).")


def not_allowed():
    """NOT_ALLOWED in the language of the page."""
    return de_en(NOT_ALLOWED, NOT_ALLOWED_EN)


def may(user, survey):
    """Who may duplicate: whoever can edit the questionnaire ("Bearbeiten" or "Admin", also through a group) and
    may create surveys of its type ("Anlegen", app/kind_access.py); an example survey also whoever sees it only as one
    ("Als Kopie übernehmen", demo-befragungen-konzept.md), alone, without its people."""
    from app import access, kind_access

    return ((survey.can(user, "editor") or access.showcase_only(user, survey))
            and kind_access.may_create(user, survey.kind))  # a copy is a new survey of the same type


def suggested_title(survey):
    return survey.title[:300 - len(TITLE_SUFFIX)] + TITLE_SUFFIX


def title_error(survey, title):
    title = " ".join((title or "").split())
    if not title:
        return de_en("Bitte einen Titel für das Duplikat eingeben.", "Please enter a title for the duplicate.")
    if title.casefold() == " ".join(survey.title.split()).casefold():
        return de_en("Das Duplikat braucht einen anderen Titel als das Original, damit beide zu unterscheiden sind.",
                     "The duplicate needs a title other than the original's so that the two can be told apart.")
    return None


def _copy_file(name, from_uid, to_uid):
    """The file under a new random name in the other folder; None if it is gone."""
    source = os.path.join(upload_folder(from_uid), name)
    if not os.path.isfile(source):
        return None
    os.makedirs(upload_folder(to_uid), exist_ok=True)
    new = f"{secrets.token_urlsafe(12)}{os.path.splitext(name)[1]}"
    from app import media

    media.copy_file(source, os.path.join(upload_folder(to_uid), new), to_uid)
    return new


class _MediaRoom:
    """The room for video and audio files left in the survey a copy goes into (MEDIA_SURVEY_MAX_MB, counted as on
    upload, media.media_bytes): copying and duplicating copied them without that check, and each copy could take
    the whole room again (audit 2026-10-09, D1). A file that does not fit is left out; its question stays and the
    researcher is told to upload it again."""

    def __init__(self, target=None):
        from flask import current_app

        from app import media

        self.left = int(current_app.config["MEDIA_SURVEY_MAX_MB"]) * 1024 * 1024 - (media.media_bytes(target) if target is not None else 0)
        self.dropped = []

    def take(self, item, other, asset, from_uid):
        """True if the file of `asset` may be copied (no video or audio, or it fits); else the copy `other` forgets it."""
        from app.items.stimulus import EXTENSIONS

        if asset.filename.rsplit(".", 1)[-1] not in EXTENSIONS:
            return True
        try:
            size = os.path.getsize(os.path.join(upload_folder(from_uid), asset.filename))
        except OSError:
            return True  # gone: _copy_file leaves it out anyway
        if size <= self.left:
            self.left -= size
            return True
        files = dict((other.config or {}).get("files") or {})
        if files.pop(asset.key or "", None) is not None:
            other.config = {**other.config, "files": files}
        if item.code not in self.dropped:
            self.dropped.append(item.code)
        return False

    def note(self):
        from flask import current_app

        if not self.dropped:
            return None
        return de_en("Video- und Audiodateien von {codes} wurden nicht kopiert: Sie hätten den Platz für Video und Audio "
                     "der Befragung ({mb} MB zusammen) überschritten. Bitte laden Sie die Dateien dort neu hoch, wo sie "
                     "gebraucht werden.",
                     "Video and audio files of {codes} were not copied: they would have exceeded the survey's room for "
                     "video and audio ({mb} MB together). Please upload the files again where they are needed.",
                     codes=", ".join(self.dropped[:10]) + (" …" if len(self.dropped) > 10 else ""),
                     mb=current_app.config["MEDIA_SURVEY_MAX_MB"])


def _locked(item, user):
    """A question of an experimental type that is not switched on for `user` (app/experimental.py). That the
    original uses it does not count: the copy is a survey of the person's own (code audit 2026-10-08, X)."""
    from app import experimental

    feature = getattr(item.kind, "experimental", None)
    return bool(feature) and not experimental.enabled(feature, user=user)


def _questionnaire(source, new, user=None, locked=None, room=None):
    """Blocks, questions and images, built but not yet written to the database (`new` is not in the session
    yet, see create); returns the pairs (question, its copy). Questions of an experimental type `user` does not
    have are left out, as when a survey file is read in (transfer._questionnaire); their codes go to `locked`.
    What pointed at them points at nothing in the copy (conditions, quotas, sources, carried options, tiles), as
    for a question with no twin anywhere below; the pre-launch check names such conditions and quota cells. `room` (_MediaRoom): video and audio files only as
    far as they fit."""
    pairs = []
    for block in source.blocks:
        twin = Block(position=block.position, titles=_copy.deepcopy(block.titles), show_title=block.show_title,
                     randomize_items=block.randomize_items, settings=_copy.deepcopy(block.settings))
        for item in block.items:
            if user is not None and _locked(item, user):
                if locked is not None:
                    locked.append(item.code)
                continue
            other = Item(position=item.position, type=item.type, code=item.code, answer_mode=item.answer_mode,
                         config=answer_lock.without_since(_copy.deepcopy(item.config)), texts=_copy.deepcopy(item.texts))
            from app import linked

            if (user is not None and linked.is_linked(item)
                    and (not source.can(user, "editor") or not linked.usable(user, linked.element(item)))):
                other.config = {k: v for k, v in other.config.items() if k != "construct"}
            for asset in item.assets:
                if room is not None and not room.take(item, other, asset, source.uid):
                    continue
                name = _copy_file(asset.filename, source.uid, new.uid)
                if name:  # a missing file: a question without that image, not one with a broken image
                    other.assets.append(Asset(filename=name, rights=asset_rights.copied(asset),
                                              **{f: getattr(asset, f) for f in ASSET_FIELDS}))
            twin.items.append(other)
            pairs.append((item, other))
        new.blocks.append(twin)
    return pairs


def _ids(source, new, pairs, notes=None):
    """Writes the copy and returns ({old block id: new}, {old item id: new}). notes: what the researcher is told
    (a condition "Nur zeigen, wenn …" on a question left out)."""
    from app import item_condition

    db.session.flush()  # the new ids
    blocks = {old.id: twin.id for old, twin in zip(source.blocks, new.blocks)}
    items = {old.id: twin.id for old, twin in pairs}
    dropped = []
    for old, twin in pairs:
        # feedback elements name the questions they show by id (items/sample_compare.py and its relatives)
        if "sources" in (twin.config or {}) and callable(getattr(twin.kind, "sources", None)):
            twin.config = {**twin.config, "sources": [items[s] for s in old.config.get("sources") or [] if s in items]}
        # ... and so do settings of other types (a total from an earlier answer): ItemType.remap_config
        config = twin.kind.remap_config(twin.config or {}, items.get)
        # ... and the condition of the question itself (app/item_condition.py)
        config, gone = item_condition.remap(config, items.get)
        if gone:
            dropped.append(twin.code)
        if config != twin.config:
            twin.config = config
    if dropped and notes is not None:
        notes.append(de_en("Ohne „Nur zeigen, wenn …“, weil die Frage der Bedingung nicht übernommen wurde: {codes}.",
                           "Without “Show only if …”, as the question of the condition was not copied: {codes}.",
                           codes=", ".join(dropped)))
    return blocks, items


def _quotas(source, new, items):
    """The quotas with their cells: conditions and targets, no counts. Returns {old quota id: new}."""
    pairs = []
    for quota in source.quotas:
        twin = Quota(code=quota.code, name=quota.name, mode=quota.mode, tolerance=quota.tolerance,
                     tolerance_pct=quota.tolerance_pct, no_cell=quota.no_cell, position=quota.position,  # see _flow
                     hold_minutes=quota.hold_minutes, count_at=quota.count_at, pick=quota.pick)
        for cell in quota.cells:
            conds = _copy.deepcopy(cell.conds or [])
            for cond in conds:
                cond["item"] = _ref(cond.get("item"), items, None) if _is_question(cond.get("item")) else cond.get("item")
            twin.cells.append(QuotaCell(code=cell.code, name=cell.name, conds=conds, target=cell.target, closed=False,
                                        running=0, done=0, refused=0, over=0, position=cell.position))
        new.quotas.append(twin)
        pairs.append((quota, twin))
    db.session.flush()  # the new ids
    return {old.id: twin.id for old, twin in pairs}


def _is_question(ref):
    from app import flow

    return flow.source_id(ref) is not None


def _ref(ref, items, quotas):
    """What a condition tests, in the copy: the copy's question or quota; a URL parameter keeps its name."""
    from app import flow

    if isinstance(ref, int):
        return items.get(ref)
    if _is_question(ref):  # the state of a postal code question
        new = items.get(flow.source_id(ref))
        return f"{flow.STATE_REF}{new}" if new else None
    if isinstance(ref, str) and ref.startswith(("q:", "qc:")):
        kind, _, qid = ref.partition(":")
        new = (quotas or {}).get(int(qid)) if qid.isdigit() else None
        return f"{kind}:{new}" if new else None
    return ref


def _rules(panel, quotas, items=None):
    """The entry and exit settings with the link rules pointing at the quotas and questions of the copy."""
    if not (panel or {}).get("rules"):
        return panel
    rules = [{**rule, "item": _ref(rule.get("item"), items or {}, quotas)} for rule in panel["rules"]]
    return {**panel, "rules": [rule for rule in rules if rule["item"]]}


def _flow(tree, blocks, items, quotas=None, web_service=True):
    """The flow tree with the ids of the copy. The ids of its own nodes (randomizers, conditions, loops and
    their rows) stay: they only mean something inside one survey, and the images of a loop are stored by them.
    web_service=False: web services are left out (experimental, not switched on for whoever copies; W3)."""
    def rec(node):
        if node.get("t") == "block":
            node["block"] = blocks[node["block"]]
        if node.get("t") == "quota" or (node.get("t") == "end" and "quotas" in node):
            # a quota calculation and an end "Quote voll" name their quotas by id. (A "Quotenprüfung" of a survey
            # in the field is copied as it is; the copy is a draft, so flow.normalize brings it to the new form)
            node["quotas"] = [quotas[q] for q in node.get("quotas") or [] if q in (quotas or {})]
            if node.get("links"):  # the links of single quotas at an end, by quota id (their cells by code: these stay)
                node["links"] = {str(quotas[int(q)]): entry for q, entry in node["links"].items()
                                 if q.isdigit() and int(q) in (quotas or {})}
        for case in node.get("cases") or [] if node.get("t") == "end" else []:
            # the triggers of an end name their quotas by id too (the cells of a quota by code: these stay)
            if "quotas" in case:
                case["quotas"] = [quotas[q] for q in case["quotas"] if q in (quotas or {})]
            if case.get("when") == "cellfull":
                case["quota"] = (quotas or {}).get(case.get("quota"))
        if node.get("t") == "ext":
            node.pop("sign", None)  # the key that checks the returned values stays with the original (_unsigned)
        if node.get("t") == "ws":
            node.pop("secret", None)  # the value of the header (an API key) stays with the original
        from app import flow

        for cond in flow.parts(node.get("cond")):  # one condition, or each of several
            if cond.get("item") is not None:
                cond["item"] = _ref(cond["item"], items, quotas)
        if "children" in node:  # a block that is gone (the tree is only tidied when it is edited) is left out
            node["children"] = [c for c in node["children"] if (c.get("t") != "block" or c.get("block") in blocks)
                                and (web_service or c.get("t") != "ws")]
            for child in node["children"]:
                rec(child)

    root = _copy.deepcopy(tree)
    rec(root)
    return root


def _unsigned(new, source, notes):
    """The copy without the keys of the original (see the head of this file), and what that means for its admin."""
    from app import external, panel

    if (new.panel or {}).get("value_labels"):  # whom the links were made for (e.g. students) stays with the original
        new.panel = {k: v for k, v in new.panel.items() if k != "value_labels"}
    if (new.panel or {}).get("sign"):
        new.panel = {**new.panel, "sign": {**new.panel["sign"], "key": ""}}
        if panel.signs(source):
            notes.append(de_en("Die Abschlusslinks des Originals sind signiert. Der Signaturschlüssel wurde nicht übernommen; "
                               "bitte unter „Datenerhebung → Teilnahmelink“ eintragen (am besten einen eigenen vom Panel).",
                               "The completion links of the original are signed. The signature key was not copied; "
                               "please enter it under “Data collection → Participation link” (best a new one from the panel)."))
    if any(external.signs(node) for node in external.nodes(source)):
        notes.append(de_en("Ein externer Abschnitt des Originals signiert seine Rückgabewerte. Das ist in der Kopie aus: Bitte "
                           "dort „Rückgabewerte signieren“ einschalten und den neuen Schlüssel in die Web-App übernehmen.",
                           "An external section of the original signs its return values. This is off in the copy: please "
                           "switch on “Sign return values” there and put the new key into the web app."))


def _theme(source, new, user):
    """The look with its own copies of the logo files. Returns False if the theme was left behind."""
    from app import designs, theme

    data = theme.clean(source.theme)
    if not data:
        return True
    logo = designs.copy_logo(data.get("logo"), source.uid, new.uid)
    partners = designs.copy_partners(data.get("partners"), source.uid, new.uid)
    new.theme = theme.clean({**{k: v for k, v in data.items() if k not in ("logo", "partners")},
                             "logo": logo, "partners": partners})
    asset_rights.copy_theme_records(source, new)
    return True


def _people(source, new, user, with_people):
    """The co-owners. The person who duplicates is admin of the copy in any case; the others and the groups
    come along only if asked for, the groups only where that person is a member (as when sharing by hand)."""
    mine = source.member_of(user)
    # entries of deleted accounts stay with the original: there participants consented under these names
    rows = [m for m in source.members if m is mine or (with_people and not m.is_former)]
    for m in rows:
        new.members.append(SurveyMember(
            user=m.user, name=m.name, affiliation=m.affiliation, invited_by_id=user.id,
            position=len(new.members), role="admin" if m is mine else m.role))
    if mine is None:
        new.members.append(SurveyMember(user=getattr(user, "_get_current_object", lambda: user)(), name=user.name,
                                        invited_by_id=user.id,
                                        affiliation=__import__("app.orgs", fromlist=["x"]).affiliation_of(user),
                                        position=len(new.members), role="admin"))
    from app import account_types

    account_types.add_supervisor(new, user)  # the supervisor of whoever duplicates becomes admin of the copy (N10)
    if with_people:
        from app import orgs

        for link in source.group_links:
            # only groups of the organisation the copy goes into, the home of who copies (app/orgs.py, A8)
            if link.group.member_of(user) is not None and orgs.of(link.group) == orgs.home_id(user):
                new.group_links.append(SurveyGroup(group_id=link.group_id, role="viewer", added_by_id=user.id))


def _consent(source, new, standard, keep_contact=True):
    """The consent names researchers of the survey: only those who are also named on the copy, and its contact
    address only if the person who duplicates is admin of the original (keep_contact; as the survey file leaves it
    out, transfer.CONSENT_PEOPLE). A text that is still the untouched standard text follows the copy (title,
    researchers, contact), as it does in the settings. Returns True if a contact address was left out."""
    old, consent = source.consent_item, new.consent_item
    if consent is None:
        return False
    from app.extensions import db
    from app.items.consent import named as is_named, refs_of

    db.session.flush()  # the copy's member rows need their ids ("m<id>" for entries of deleted accounts)
    chosen = named = old.config.get("researchers") or []
    by_user = {m.user_id: m for m in new.members if m.user_id}
    kept = []
    for m in source.members:
        twin = by_user.get(m.user_id) if m.user_id else next(
            (n for n in new.members if not n.user_id and n.name == m.name), None)
        if twin is not None and is_named(m, chosen):
            kept += refs_of(twin)
    first = refs_of(new.members[0]) if new.members else []
    dropped = not keep_contact and bool(consent.config.get("contact_email"))
    consent.config = {**consent.config, "researchers": (kept or first) if named else [],
                      **({"contact_email": ""} if dropped else {})}
    texts = dict(consent.texts or {})
    for lang, was in standard.items():
        body = (texts.get(lang) or {}).get("body", "")
        now = consent.kind.template_body(consent, lang)
        if body and body == was and now != was:
            texts[lang] = {**texts[lang], "body": now}
    consent.texts = texts
    return dropped


def _marks(source, new, blocks, items):
    """What the translations are based on (page "Übersetzung"), so that the copy does not show every translated
    text as unchecked. The title is new, its translations and their marks are not taken."""
    for m in db.session.scalars(db.select(TranslationMark).filter_by(survey_id=source.id)):
        kind, _, ref = m.unit.partition(":")
        if kind in ("item", "block"):
            twin = (items if kind == "item" else blocks).get(int(ref)) if ref.isdigit() else None
            if twin is None:
                continue
            unit = f"{kind}:{twin}"
        elif m.unit == "title":
            continue
        else:
            unit = m.unit
        db.session.add(TranslationMark(survey_id=new.id, unit=unit, lang=m.lang, path=m.path, source=m.source,
                                       ai=m.ai, updated_at=m.updated_at))


def create(source, user, title, with_people=True, link=False):
    """A new draft like `source` under the title `title`, committed. Returns (survey, notes for the researcher).
    If anything fails, nothing stays: neither rows nor copied files. With `link` ("Fragen mit dem Original
    verknüpfen") every question of the original that can be linked becomes a linked element first (app/linked.py),
    and its copy is a second use of it.

    The image files are copied before anything is written to the database, as in transfer.create: SQLite has
    one writer, and from the first INSERT (which comes as soon as the new survey is in the session and anything
    is read) to the commit every participant who sends a page waits."""
    from app import constructs, flow

    if constructs.restricted_keys(source):
        # A new random study UID has no documented grant. Existing study permissions cannot be copied.
        raise ValueError(constructs.permission_notice())

    notes = []
    keep_contact = source.can(user, "admin")  # the contact address of the original is that of its admins (L-12)
    consent = source.consent_item
    standard = {lang: consent.kind.template_body(consent, lang) for lang in source.languages} if consent else {}
    new = Survey(title=" ".join(title.split())[:300], uid=secrets.token_urlsafe(9), status=Survey.DRAFT,
                 created_by_id=user.id, **{f: _copy.deepcopy(getattr(source, f)) for f in SETTINGS})
    # not admin_hidden: a copy is a survey of its own, which a platform admin sees unless its people hide it; the
    # settings of the paper questionnaire come along (audit 2026-10-12, E-6)
    from app import paper

    new.paper = paper.copied(source.paper)
    try:
        locked, room = [], _MediaRoom()
        pairs = _questionnaire(source, new, user, locked, room)
        if room.note():
            notes.append(room.note())
        if locked:
            notes.append(de_en_n(len(locked), (
                "{n} Element eines experimentellen Typs, der für Sie nicht freigeschaltet ist, wurde nicht übernommen: "
                "{codes}. Bedingungen und Quoten, die es prüften, prüfen in der Kopie nichts mehr; bitte im Ablauf und "
                "bei den Quoten anpassen.",
                "{n} Elemente eines experimentellen Typs, der für Sie nicht freigeschaltet ist, wurden nicht übernommen: "
                "{codes}. Bedingungen und Quoten, die sie prüften, prüfen in der Kopie nichts mehr; bitte im Ablauf und "
                "bei den Quoten anpassen."), (
                "{n} element of an experimental type that is not switched on for you was not copied: {codes}. "
                "Conditions and quotas that tested it test nothing in the copy; please adjust them in the flow and "
                "the quotas.",
                "{n} elements of an experimental type that is not switched on for you were not copied: {codes}. "
                "Conditions and quotas that tested them test nothing in the copy; please adjust them in the flow and "
                "the quotas."),
                codes=", ".join(locked[:10]) + (" …" if len(locked) > 10 else "")))
        if link:
            _link(source, pairs, user, notes)
        own_look = _theme(source, new, user)
        db.session.add(new)
        _people(source, new, user, with_people)
        blocks, items = _ids(source, new, pairs, notes)
        quota_ids = _quotas(source, new, items)
        from app import experimental, webservice

        keep_ws = experimental.enabled("webservice", user=user)
        if not keep_ws and webservice.nodes(source):
            notes.append(de_en("Die Web Services im Ablauf wurden nicht übernommen: Das experimentelle Merkmal „Web Service im "
                               "Ablauf“ ist für Sie nicht freigeschaltet.",
                               "The web services in the flow were not copied: the experimental feature “Web Service in the "
                               "flow” is not switched on for you."))
        new.flow = _flow(flow.tree(source), blocks, items, quota_ids, keep_ws)
        new.panel = _rules(new.panel, quota_ids, items)
        _unsigned(new, source, notes)
        from app import boards

        boards.copy_to(source, new, items, quota_ids)
        if source.live is not None:  # a live vote stays one: settings and quiz, a fresh run (app/live/)
            from app import live

            new.live = live.copied_state(source, items)
            if not keep_contact and new.live.get("contact_email"):
                new.live = {**new.live, "contact_email": ""}
                notes.append(de_en("Die Kontaktadresse der Datenschutzseite wurde nicht übernommen, sie gehört zu den Admins des "
                                   "Originals. Ohne eigene Adresse unter „Einstellungen“ nennt die Seite die E-Mail-Adresse Ihres Kontos, "
                                   "falls eine hinterlegt ist.",
                                   "The contact address of the privacy page was not copied; it belongs to the admins of the "
                                   "original. Without an address of your own under “Settings” the page names the email address of "
                                   "your account, if there is one."))
        if source.interview is not None:  # an interview guide stays one, with its settings (app/interview.py)
            from app import interview

            new.interview = interview.guide_state(source)  # its versions stay with the original
        if source.sampling is not None:  # a diary study stays one, with its schedule; its persons stay behind (app/diary.py)
            from app import diary

            new.sampling = diary.copied_state(source)
            diary.fix_parts(new)
        flow.normalize(new)
        if not own_look:
            notes.append(de_en("Das Erscheinungsbild wurde nicht übernommen: Das Farbschema des Originals ist Angehörigen der "
                               "Einrichtung vorbehalten. Das Duplikat hat das Standard-Erscheinungsbild.",
                               "The appearance was not copied: the colour scheme of the original is reserved for members of the "
                               "institution. The duplicate has the standard appearance."))
        if _consent(source, new, standard, keep_contact):
            notes.append(de_en("Die Kontaktadresse der Einwilligung wurde nicht übernommen, sie gehört zu den Admins des Originals. "
                               "Bitte in der Einwilligung eine eigene eintragen.",
                               "The contact address of the consent was not copied; it belongs to the admins of the original. "
                               "Please enter your own in the consent."))
        _marks(source, new, blocks, items)
        db.session.commit()
    except Exception:
        db.session.rollback()
        shutil.rmtree(upload_folder(new.uid), ignore_errors=True)
        raise
    return new, notes


def _link(source, pairs, user, notes):
    """The questions of the original become linked elements; their copies use the same ones."""
    from app import linked

    made, skipped = linked.link_survey(source, user)
    for old, twin in pairs:
        if linked.is_linked(old) and not (twin.config or {}).get("construct"):
            twin.config = {**(twin.config or {}), "construct": _copy.deepcopy(old.config["construct"])}
    shared = sum(1 for old, _ in pairs if linked.is_linked(old))
    if shared:
        notes.append((de_en("{n} Frage ist mit dem Original verknüpft", "{n} question is linked to the original", n=shared)
                      if shared == 1 else
                      de_en("{n} Fragen sind mit dem Original verknüpft", "{n} questions are linked to the original", n=shared))
                     + (de_en(" ({n} davon neu angelegt)", " ({n} of them newly created)", n=made) if made and made != shared else "")
                     + de_en(": Ihre Antworten lassen sich gemeinsam auswerten, und sie sind gesperrt, sobald eine der "
                             "Befragungen damit läuft oder echte Teilnahmen hat.",
                             ": their answers can be analysed together, and they are locked as soon as one of the surveys "
                             "runs with them or has real participations."))
    if skipped:
        notes.append(de_en("Nicht verknüpft: ", "Not linked: ") + "; ".join(f"{code} ({reason.rstrip('.')})" for code, reason in skipped) + ".")


def item_targets(user, item):
    """The surveys a question can be copied into: drafts the person may edit, other than its own, the ones the person
    saved in last first, then the newest (UI/UX audit 2026-10-07, W27; nothing is chosen beforehand); only
    of the same kind (a questionnaire's slider in a questionnaire, not in a live vote or an interview guide, whose
    element types differ; UI/UX audit 2026-10-05, V90). No survey for a question of an experimental type the person does
    not have (app/experimental.py): using it in the own survey is not copying it into a new one (security audit
    2026-10-06, S12)."""
    from app import access, experimental

    feature = getattr(item.kind, "experimental", None)
    if feature and not experimental.enabled(feature, user=user):
        return []
    rows = db.session.scalars(db.select(Survey).filter(Survey.id.in_(access.survey_ids(user)), Survey.id != item.block.survey_id,
                                                       Survey.status == Survey.DRAFT).order_by(Survey.id.desc()))
    kind = item.block.survey.kind
    rows = [s for s in rows if s.kind == kind]
    roles = access.roles_of_user(user, rows)  # all at once, not three queries per draft (PERF-04)
    rows = [s for s in rows if roles[s.id] is not None and SurveyMember.LEVEL[roles[s.id]] >= SurveyMember.LEVEL["editor"]]
    last = dict(db.session.execute(db.select(EditMark.survey_id, db.func.max(EditMark.saved_at)).filter(
        EditMark.user_id == user.id, EditMark.saved_at.is_not(None)).group_by(EditMark.survey_id)).all())
    # stable sort: the newest-first order of the query stays among surveys without a save of the person
    return sorted(rows, key=lambda s: (s.id not in last, -(last[s.id].timestamp() if s.id in last else 0)))


ROW_LISTS = {"likert": "statements", "semantic": "pairs", "slider": "rows"}  # rows with a variable name of their own


def number_rows(kind_key, config, code):
    """A second copy of a matrix whose rows have fixed variable names (a construct: KAMB_1, KAMB_2 …) takes its
    own item code for them, <code>_1, <code>_2 …: otherwise both copies export the same columns."""
    for n, row in enumerate(config.get(ROW_LISTS.get(kind_key, ""), None) or []):
        if row.get("code"):  # an empty one already follows the item code
            row["code"] = f"{code}_{n + 1}"


def copy_item(item, target):
    """A copy of a question at the end of another survey, in a block of its own, with its images and its settings
    (a choice design too); a variable name the target uses already gets a number. Questions it showed by id
    (feedback elements) are left out, the consent is never copied. Returns the new question; the caller commits.

    Texts are kept per language: the copy has those of the target's languages. If the question has none in the
    target's default language, the texts of the source's default language stand there, so it is not empty; the
    copy then has `.untranslated` (the language the texts are in), and translating them is the person's job."""
    from app import flow

    if item.type in ("consent", "page_break"):
        raise ValueError(item.type)
    block = Block(position=len(target.blocks), titles={target.default_language: target.next_block_title()})
    other = item_copy(item, target, {i.code for b in target.blocks for i in b.items})
    block.items.append(other)
    target.blocks.append(block)
    db.session.flush()
    flow.normalize(target)
    return other


def item_copy(item, target, used, user=None):
    """The copy of a question for `target`, not yet in a block (copy_item; a diary study taking over a questionnaire,
    diary.take_over). `used`: the variable names taken in the target; the copy's name is added to it."""
    from app import constructs

    if not constructs.permitted_copy(item, target, user):
        raise ValueError(constructs.permission_notice())
    code = item.code
    n = 1
    while code in used:
        n += 1
        code = f"{item.code}_{n}"
    config = _copy.deepcopy(item.config or {})
    if constructs.wording_withheld(item):
        config["construct"]["wording_sources"] = constructs.copy_wording_sources(item, target)
        template = config.get("construct", {}).get("template", {})
        if isinstance(template.get("texts"), dict):
            needed = constructs.copy_languages(item, target)
            template["texts"] = {lang: words for lang, words in template["texts"].items() if lang in needed}
    if "sources" in config:
        config["sources"] = []
        config.pop("subscales", None)
    config = item.kind.remap_config(config, lambda old: None)  # other questions do not come along
    from app import item_condition

    config, cond_gone = item_condition.remap(config, lambda old: None)  # ... nor the one of "Nur zeigen, wenn …"
    if code != item.code:
        number_rows(item.type, config, code)
    lang = target.default_language
    source = item.block.survey.default_language
    texts = {l: _copy.deepcopy(t) for l, t in (item.texts or {}).items() if l in target.languages}
    untranslated = source if not texts.get(lang) and (item.texts or {}).get(source) else None
    if untranslated:
        texts[lang] = _copy.deepcopy(item.texts[source])
    used.add(code)
    other = Item(position=0, type=item.type, code=code, answer_mode=item.answer_mode, config=config, texts=texts)
    other.untranslated = untranslated
    room = _MediaRoom(target)
    for asset in item.assets:
        if not room.take(item, other, asset, item.block.survey.uid):
            continue
        name = _copy_file(asset.filename, item.block.survey.uid, target.uid)
        if name:
            other.assets.append(Asset(filename=name, rights=asset_rights.copied(asset),
                                      **{f: getattr(asset, f) for f in ASSET_FIELDS}))
    other.media_note = room.note()  # files left out for want of room, said to the researcher
    other.cond_note = (de_en("Die Bedingung „Nur zeigen, wenn …“ wurde nicht übernommen: Die Frage, auf die sie sich "
                             "bezieht, gibt es in dieser Befragung nicht.", "The condition “Show only if …” was not taken "
                             "over: the question it refers to does not exist in this survey.") if cond_gone else None)
    return other


# --- duplicating within the same survey (concept konzept-duplizieren-2026-10-21.md, all as recommended) -------------


def twins(items, user=None):
    """Copies of `items` (questions of one survey, in their order) for the same survey, not yet in a block: texts,
    settings and images come along, a taken variable name gets a number (q5 → q5_2, the rows of a Likert or matrix
    with it), a linked element or a construct stays one (E6). What they point at stays as it is: in the same
    survey the other questions are there. Returns (pairs [(item, copy)], note): the note names left-out video and
    audio files (_MediaRoom). Raises ValueError if a construct's terms forbid the copy."""
    from app import constructs

    survey = items[0].block.survey
    used = {i.code for b in survey.blocks for i in b.items}
    room = _MediaRoom(survey)
    pairs = []
    for item in items:
        if not constructs.permitted_copy(item, survey, user):
            raise ValueError(constructs.permission_notice())
        code, n = item.code, 1
        while code in used:
            n += 1
            code = f"{item.code}_{n}"
        used.add(code)
        config = answer_lock.without_since(_copy.deepcopy(item.config or {}))  # a new question: version 1
        if code != item.code:
            number_rows(item.type, config, code)
        other = Item(position=0, type=item.type, code=code, answer_mode=item.answer_mode, config=config,
                     texts=_copy.deepcopy(item.texts or {}))
        for asset in item.assets:
            if not room.take(item, other, asset, survey.uid):
                continue
            name = _copy_file(asset.filename, survey.uid, survey.uid)
            if name:
                other.assets.append(Asset(filename=name, rights=asset_rights.copied(asset),
                                          **{f: getattr(asset, f) for f in ASSET_FIELDS}))
        pairs.append((item, other))
    return pairs, room.note()


def rewire(pairs):
    """After the copies have ids (flush): what pointed from one copied question at another copied one points at
    its copy (E5): conditions "Nur zeigen, wenn …", settings that name a question by id (a total from an earlier
    answer, the questions a feedback element shows) and {{code}} in the texts. References to questions that did not
    come along stay as they are."""
    from app import item_condition
    from app.items.base import rename_placeholder

    ids = {old.id: new.id for old, new in pairs}
    names = []
    for old, new in pairs:
        if old.code != new.code:
            names.append((old.code, new.code))
            lang = old.block.survey.default_language
            parts_old = [p for p, _ in old.kind.pipe_parts(old, lang)]
            parts_new = [p for p, _ in new.kind.pipe_parts(new, lang)]
            if len(parts_old) == len(parts_new):
                names += [(a, b) for a, b in zip(parts_old, parts_new) if a != b]
    for old, new in pairs:
        config = dict(new.config or {})
        if "sources" in config and callable(getattr(new.kind, "sources", None)):
            config["sources"] = [ids.get(s, s) for s in config.get("sources") or []]
        config = new.kind.remap_config(config, lambda o: ids.get(o, o))
        config, _ = item_condition.remap(config, lambda o: ids.get(o, o))
        if config != new.config:
            new.config = config
        texts = new.texts or {}
        for a, b in names:
            texts = rename_placeholder(texts, a, b)
        if texts != new.texts:
            new.texts = texts
