"""Experimental features: parts of the platform that only researchers see for whom a site admin switched them on
(Ralf, 2026-10-05: no self-selection any more; concept "Freigabe experimenteller Funktionen", all as recommended).

A site admin switches a feature on for one person (User.experiments), for a group (ExperimentGrant with group_id,
live for every member, also future ones), for an organisation (org_id, everyone in it as app/orgs.py counts them: own
row, a group of it, home "main"; Ralf 2026-10-20) or for everyone (ExperimentGrant without group, organisation and
person), on
Administration > Experimentell and on the admin page of an account. A person kept out (ExperimentGrant with user_id,
allowed false) does not get it, whatever groups, organisations and "everyone" say. Site admins are no exception: they switch
features on for themselves like for anyone. The page "Konto" only lists what is on, without ticks. What people had
ticked themselves until 2026-10-05 stays on as their personal switch.

Whoever does not have a feature sees nothing of it: no card, no link, no setting, no wording; its routes answer 404
(`require("key")`). Templates ask `experimental("key")`, Python code `enabled("key")`. A survey that uses a feature
shows it to its whole team from then on (`experimental("key", survey)`, `in_use`): a colleague without it opens the
block like anyone else (UI audit 2026-10-04, R21, G5), and switching a
feature off never takes it from a survey that uses it; only new use needs the feature. (Vignettes started here and
are a regular part of the loop since then: app/vignettes.py; so is "Interviews erheben" of the interview guide since
2026-10-05: app/interview_data.py; the IAT with its IAT-Ergebnis and delay discounting since 2026-10-10; the paper questionnaire since 2026-10-11:
app/paper.py; topic map, topic profile and scale profile since 2026-10-11: app/items/topic_feedback.py, scale_profile.py;
structural equation models with the drawing of the model since 2026-10-11: app/sem_model.py, app/plan_model.py; the
voice message, video / audio with a playback log, the sound of the video conference in the interview view and "Schneller
blättern, auch bei schwachem Netz" since 2026-10-16: app/items/voice.py, app/items/stimulus.py, app/interview.py,
app/blockload.py; live feedback on a talk (the template "Fragen & Feedback zum Vortrag", slides from a PDF, the
slides on the phones) since 2026-10-17: app/live/; Coscine since 2026-10-20: app/coscine.py; the participant screen
in the interview since 2026-10-22: app/interview_screen.py; the diary study with its AI assistant and interviews without a
network since 2026-10-27: app/diary.py, app/diary_ai.py, app/interview_offline.py.)

A new experimental feature: one entry in FEATURES, then gate its parts with its key; it is on for nobody until an
admin switches it on. A feature that leaves the experimental group: remove its entry and its checks; switches and
grants stored for it are ignored from then on."""
from flask import abort, g, has_app_context
from flask_login import current_user

from app.lang import de_en, english

MAYRING = "Mayring, P. (2022). Qualitative Inhaltsanalyse: Grundlagen und Techniken (13. Aufl.). Beltz."

FEATURES = [  # (key, label, text): in this order on Administration > Experimentell and "Konto"
    ("ainstein", "AInstein: Forschungsfrage entwickeln, schärfen, operationalisieren und in den Fragebogen umsetzen",
     "Ein Gehirn-Knopf links in der Kopfzeile öffnet AInstein: aus Projektname und grober Idee im Gespräch mit der KI "
     "einen Forschungs-Canvas füllen (Forschungsfrage, Design, Stichprobe, Hypothesen, Konstrukte, Auswertung), "
     "daraus Hypothesen und Konstrukte ableiten und als Befragung anlegen."),
    ("coding", "Offene Antworten kategorisieren",
     "Auf der Ergebnisseite an jeder offenen Antwort der Knopf „Kategorisieren“: Kategorien mit Definition und "
     "Ankerbeispiel anlegen (nach Mayring, 2022), die Antworten einmal je Text per Häkchen oder Tasten 1 bis 9 "
     "zuordnen. Die Kategorien erscheinen als Häufigkeiten auf der Ergebnisseite, als Spalten im Export und im "
     "Codebuch, dazu ein Kodierleitfaden unter „Export“."),
    ("webservice", "Web Service im Ablauf: Der Server ruft eine Schnittstelle auf",
     "Ein Element im Ablauf, an dem der Server eine Adresse aufruft (z. B. eine Wetter- oder Geodaten-Schnittstelle), "
     "mit Platzhaltern für frühere Antworten; Felder der Antwort werden als Werte gespeichert, stehen in späteren Texten "
     "({{ws1_temp}}) und im Export. Nur https, nur öffentliche Server, höchstens 5 Sekunden. Erscheint im Reiter "
     "„Ablauf“ unter „Hinzufügen“."),
    ("agent", "KI-Agenten-Schnittstelle (MCP): Fragebögen mit einem eigenen KI-Agenten lesen und Entwürfe bearbeiten",
     "Ein persönliches Token auf der Seite „Konto › Verbindungen“ verbindet einen KI-Agenten (etwa Claude Code oder Claude Desktop) "
     "über das Model Context Protocol mit Askurious: Er liest Fragebögen (ohne Antworten und Daten) und legt in "
     "Entwürfen Blöcke und Elemente an, ändert, verschiebt und löscht sie (in den Papierkorb), fügt Konstrukte ein und "
     "nimmt eigene Schritte zurück. Starten, Löschen, Daten, Teilnahmelinks, Beteiligte und Einwilligung bleiben "
     "außen vor. Die Inhalte gehen an den KI-Dienst, den der Agent nutzt."),
    ("aca", "Adaptive Conjoint (ACA)",
     "Ein Element „Adaptive Conjoint (ACA)“ unter „Wahlexperimente“: Teilnehmende bewerten die Stufen und die Wichtigkeit "
     "der Merkmale, dann Paare aus Teilprodukten, die sich nach jeder Antwort anpassen (Johnson, 1987), optional eine "
     "Kalibrierung mit Kaufwahrscheinlichkeiten. Teilnutzen je Person, Ergebnisseite, Simulator und Export. Für sehr "
     "viele Merkmale; den Preis unterschätzt ACA."),
    ("coscine_media", "Video und Ton aus Coscine übernehmen",
     "Am Element „Video / Audio“ neben „Datei hochladen“ der Knopf „aus Coscine wählen“: eine Video- oder Tondatei aus "
     "einer Coscine-Ressource, die Ihr Token sieht. Askurious lädt sie im Hintergrund und prüft sie wie einen Upload "
     "(Format, Größe, Platz der Befragung); sie wird kopiert, nicht verknüpft. Braucht ein Coscine-Token unter „Konto“."),
    ("ai_chat", "KI-Chat für Teilnehmende: Gespräch mit einem KI-Assistenten, vollständig protokolliert",
     "Ein Element „KI-Chat“: Teilnehmende schreiben auf einer Seite des Fragebogens oder eines Tagebuchmoduls mit einem "
     "KI-Assistenten. Jede Nachricht beider Seiten wird als Studiendaten gespeichert und als eigene Datei mit einer "
     "Zeile je Nachricht exportiert. Das Modell kommt aus einem eigenen Modellzugang der Studie (Anbieter, Adresse, "
     "Schlüssel, Modell), nie aus dem persönlichen KI-Zugang oder dem der Plattform; seine Herkunft steht in der "
     "Dokumentation der Befragung. Anweisungen an das Modell je Sprache, Grenzen je Gespräch und Tag, ein Hinweis auf "
     "Hilfe bei Krisenwörtern, ein eigenes Häkchen in der Einwilligung."),
]
KEYS = [key for key, _label, _text in FEATURES]
FEATURES_EN = {  # key: (label, text), FEATURES in English; features() picks them when the page is English
    "ainstein": ("AInstein: develop a research question, sharpen it, operationalise it and turn it into the questionnaire",
                 "A brain button on the left of the header opens AInstein: from a project name and a rough idea, fill in a "
                 "research canvas in conversation with the AI (research question, design, sample, hypotheses, constructs, "
                 "analysis), derive hypotheses and constructs from it and create it as a survey."),
    "coding": ("Categorise open answers",
               "On the results page every open answer gets the button “Categorise”: create categories with a definition "
               "and an anchor example (after Mayring, 2022) and assign the answers once per text with ticks or the keys 1 "
               "to 9. The categories appear as frequencies on the results page, as columns in the export and the "
               "codebook, plus a coding guide under “Export”."),
    "webservice": ("Web Service in the flow: the server calls an API",
                   "An element of the flow where the server calls an address (e.g. a weather or geodata API), with "
                   "placeholders for earlier answers; fields of the answer are stored as values, appear in later texts "
                   "({{ws1_temp}}) and in the export. https only, public servers only, at most 5 seconds. Appears in the "
                   "tab “Flow” under “Add”."),
    "agent": ("AI agent interface (MCP): read questionnaires and edit drafts with your own AI agent",
              "A personal token on the page “Account › Connections” connects an AI agent (such as Claude Code or Claude Desktop) with "
              "Askurious through the Model Context Protocol: it reads questionnaires (without answers and data) and, in "
              "drafts, adds, changes, moves and deletes blocks and elements (into the bin), inserts constructs and takes "
              "back its own steps. Starting, deleting, data, participation links, people involved and the consent stay "
              "out. The contents go to the AI service the agent uses."),
    "aca": ("Adaptive conjoint (ACA)",
            "An element “Adaptive conjoint (ACA)” under “Choice experiments”: participants rate the levels and the "
            "importance of the attributes, then pairs of partial offers that adapt after every answer (Johnson, 1987), "
            "optionally a calibration with purchase likelihoods. Part-worths per person, results page, simulator and "
            "export. For very many attributes; ACA underestimates the price."),
    "coscine_media": ("Take video and sound from Coscine",
                      "On the element “Video / audio”, next to “Upload a file”, the button “choose from Coscine”: a video "
                      "or sound file from a Coscine resource your token sees. Askurious loads it in the background and "
                      "checks it like an upload (format, size, room of the survey); it is copied, not linked. Needs a "
                      "Coscine token under “Account”."),
    "ai_chat": ("AI chat for participants: a conversation with an AI assistant, logged in full",
                "An element “AI chat”: participants write with an AI assistant on a page of the questionnaire or of a "
                "diary module. Every message of both sides is stored as study data and exported as a file of its own "
                "with one row per message. The model comes from a model access of the study itself (provider, "
                "address, key, model), never from the personal AI access or the platform's; its origin is part of the "
                "survey's documentation. Instructions for the model per language, limits per conversation and day, a "
                "hint to help on crisis words, a tick of its own in the consent."),
}


# the texts that differ on the page "Profil und Anmeldung" (UI/UX audit 2026-10-09, X76; since Konto-Seiten B2 they
# name the page "Verbindungen", where the tokens are): key: (German, English)
ON_ACCOUNT = {
    "agent": ("Ein persönliches Token unter „Konto › Verbindungen“ verbindet einen KI-Agenten (etwa Claude "
              "Code oder Claude Desktop) über das Model Context Protocol mit Askurious: Er liest Fragebögen (ohne "
              "Antworten und Daten) und bearbeitet Entwürfe. Die Inhalte gehen an den KI-Dienst, den der Agent nutzt.",
              "A personal token under “Account › Connections” connects an AI agent (such as Claude Code or Claude "
              "Desktop) with Askurious through the Model Context Protocol: it reads questionnaires (without answers "
              "and data) and edits drafts. The contents go to the AI service the agent uses."),
}


def features(on_account=False):
    """FEATURES in the language of the page: [(key, label, text)], keys unchanged. on_account: with the texts of the
    page "Konto" (ON_ACCOUNT)."""
    rows = FEATURES if not english() else [(key,) + FEATURES_EN.get(key, (label, text)) for key, label, text in FEATURES]
    if on_account:
        rows = [(key, label, de_en(*ON_ACCOUNT[key]) if key in ON_ACCOUNT else text) for key, label, text in rows]
    return rows


def label(feature):
    """The name of a feature as the pages show it."""
    return next((text for key, text, _t in features() if key == feature), feature)


def source_groups(keys=None):
    """The sources the texts of FEATURES cite as short references, in full for the one fold of the page "Konto";
    with `keys`, only those of these features."""
    groups = [("coding", de_en("Offene Antworten kategorisieren", "Categorise open answers"), [MAYRING])]
    return [(title, sources) for key, title, sources in groups if keys is None or key in keys]


def _grants(user):
    """(feature, group_id, user_id, allowed, org_id) of the rows of ExperimentGrant that concern `user`: their groups,
    their organisations, everyone, and their own (kept out). Read once per request and person; User.experiments is
    read anew each time."""
    from app import orgs
    from app.extensions import db
    from app.groups import ids_of
    from app.models import ExperimentGrant as E

    cache = g.setdefault("experiment_grants", {}) if has_app_context() else {}
    if user.id not in cache:
        cache[user.id] = [(r.feature, r.group_id, r.user_id, r.allowed, r.org_id) for r in db.session.scalars(
            db.select(E).where(db.or_(E.user_id == user.id, E.group_id.in_(ids_of(user)),
                                      E.org_id.in_(list(orgs.ids_of(user))),
                                      db.and_(E.user_id.is_(None), E.group_id.is_(None), E.org_id.is_(None)))))]
    return cache[user.id]


def forget():
    """Read the grants anew in this request (after an admin changed them)."""
    if has_app_context():
        g.pop("experiment_grants", None)


def kept_out(user, feature):
    """Has a site admin kept `user` out of `feature` ("blocked" on their page)? Then a survey that already uses it
    does not open it for them either (Abgleich 2026-10-15 AU-15)."""
    if getattr(user, "id", None) is None:
        return False
    return any(f == feature and uid is not None and not allowed for f, _gid, uid, allowed, _oid in _grants(user))


def active(user):
    """The keys of the experimental features that are on for this researcher, in the order of FEATURES."""
    if getattr(user, "id", None) is None:
        return []
    own = set(getattr(user, "experiments", None) or ())
    shared, kept_out = set(), set()
    for feature, _group_id, user_id, allowed, _org_id in _grants(user):
        if user_id is not None:
            if not allowed:
                kept_out.add(feature)
        elif allowed:
            shared.add(feature)
    return [key for key in KEYS if key not in kept_out and (key in own or key in shared)]


def how(feature, user):
    """Why `feature` is on or off for `user`, for the admin pages: {"own", "blocked", "groups" (names), "orgs"
    (names), "everyone", "on"}."""
    from app.extensions import db
    from app.models import Organisation, WorkGroup

    rows = [r for r in _grants(user) if r[0] == feature]
    blocked = any(uid is not None and not allowed for _f, _gid, uid, allowed, _oid in rows)
    group_ids = [gid for _f, gid, _uid, allowed, _oid in rows if gid is not None and allowed]
    groups = list(db.session.scalars(db.select(WorkGroup.name).where(WorkGroup.id.in_(group_ids))
                                     .order_by(db.func.lower(WorkGroup.name)))) if group_ids else []
    org_ids = [oid for _f, _gid, _uid, allowed, oid in rows if oid is not None and allowed]
    org_names = list(db.session.scalars(db.select(Organisation.name).where(Organisation.id.in_(org_ids))
                                        .order_by(db.func.lower(Organisation.name)))) if org_ids else []
    everyone = any(gid is None and uid is None and oid is None and allowed for _f, gid, uid, allowed, oid in rows)
    own = feature in (user.experiments or ())
    return {"own": own, "blocked": blocked, "groups": groups, "orgs": org_names, "everyone": everyone,
            "on": not blocked and (own or bool(groups) or bool(org_names) or everyone)}


def in_use(feature, survey):
    """Does `survey` use the experimental feature: an element type of it among its questions, a Coscine resource
    set, …?"""
    if survey is None:
        return False
    if feature in _item_features():  # an element type of the feature (its `experimental` key, app/items)
        return any(getattr(i.kind, "experimental", None) == feature for b in survey.blocks for i in b.items)
    if feature == "coding":  # categories of open answers (app/coding.py)
        return bool(getattr(survey, "codings", None))
    if feature == "webservice":
        from app import webservice

        return bool(webservice.nodes(survey))
    if feature == "agent":  # an AI agent changed the survey (app/agent_tools.py): its team sees the banner and marks
        from app.edit_guard import AGENT_KEY
        from app.extensions import db
        from app.models import EditMark

        return db.session.scalar(db.select(EditMark.id).where(EditMark.survey_id == survey.id,
                                                              EditMark.key.startswith(AGENT_KEY)).limit(1)) is not None
    return False


def _item_features():
    """The features that element types belong to (their `experimental` key; none since the voice message and video /
    audio became regular on 2026-10-16)."""
    from app import items

    return {getattr(k, "experimental", None) for k in items.all_types()} - {None}


def uses(feature):
    """How often the feature is in use, for Administration > Experimentell: (number, what it counts)."""
    from app.extensions import db, json_given
    from app.models import Block, Item, ResearchProject, Survey

    if feature in _item_features():
        n = db.session.scalar(db.select(db.func.count(db.distinct(Block.survey_id))).select_from(Item).join(
            Block, Block.id == Item.block_id).where(Item.type.in_(
                [k.key for k in __import__("app.items", fromlist=["all_types"]).all_types()
                 if getattr(k, "experimental", None) == feature]))) or 0
    elif feature == "webservice":  # the flow is JSON: read in Python
        n = sum(1 for flow in db.session.scalars(db.select(Survey.flow).where(Survey.flow.is_not(None)))
                if isinstance(flow, dict) and '"t": "ws"' in __import__("json").dumps(flow))
    elif feature == "coding":
        n = sum(1 for value in db.session.scalars(db.select(Survey.codings).where(Survey.codings.is_not(None))) if value)
    elif feature == "ainstein":
        n = db.session.scalar(db.select(db.func.count(ResearchProject.id))) or 0
        # "genutzt in 0 Projekten": the dative after "in" (UI/UX audit 2026-10-09, X77)
        return n, de_en("Projekt", "project") if n == 1 else de_en("Projekten", "projects")
    else:
        n = 0
    return n, de_en("Befragung", "survey") if n == 1 else de_en("Befragungen", "surveys")


def enabled(feature=None, user=None, survey=None):
    """True if the researcher (by default the one signed in) sees the experimental feature `feature`: it is on for
    them, or `survey` already uses it (unless an admin kept them out); without a key, whether any of them is on for
    them."""
    if isinstance(user, object) and user is not None and not hasattr(user, "is_authenticated") and survey is None:
        survey, user = user, None  # experimental("key", survey) from a template
    user = current_user if user is None else user
    if not getattr(user, "is_authenticated", False):
        return False
    on = active(user)
    if feature is None:
        return bool(on)
    if feature in on:
        return True
    # a survey that uses the feature opens it for its people, but not for someone an admin kept out (AU-15)
    return in_use(feature, survey) and not kept_out(user, feature)


def require(feature=None, survey=None):
    """Answer 404 to whoever does not have the feature and is not in a survey that uses it."""
    if not enabled(feature, survey=survey):
        abort(404)


# --- what site admins change (app/admin.py) ---

def set_person(user, feature, state, by):
    """One person: "on" (switched on for them), "off" (then groups, organisations and everyone decide) or "blocked"
    (kept out, whatever groups, organisations and everyone say)."""
    from app.extensions import db
    from app.models import ExperimentGrant

    if feature not in KEYS or state not in ("on", "off", "blocked"):
        raise ValueError(feature)
    own = {key for key in (user.experiments or ()) if key in KEYS and key != feature}
    user.experiments = [key for key in KEYS if key in own or (key == feature and state == "on")]
    row = db.session.scalar(db.select(ExperimentGrant).filter_by(feature=feature, user_id=user.id))
    if state == "blocked" and row is None:
        db.session.add(ExperimentGrant(feature=feature, user_id=user.id, allowed=False, created_by_id=by.id))
    elif state != "blocked" and row is not None:
        db.session.delete(row)
    forget()
    _logged(feature, state, by, account=user.id, target="person")


def set_group(group, feature, on, by):
    """On or off for a whole group, live for its members."""
    from app.extensions import db
    from app.models import ExperimentGrant

    if feature not in KEYS:
        raise ValueError(feature)
    row = db.session.scalar(db.select(ExperimentGrant).filter_by(feature=feature, group_id=group.id))
    if on and row is None:
        db.session.add(ExperimentGrant(feature=feature, group_id=group.id, created_by_id=by.id))
    elif not on and row is not None:
        db.session.delete(row)
    forget()
    _logged(feature, "on" if on else "off", by, target=f"group {group.id}")


def set_org(org, feature, on, by):
    """On or off for a whole organisation, live for everyone in it (app/orgs.py: own row, a group of it, home)."""
    from app.extensions import db
    from app.models import ExperimentGrant

    if feature not in KEYS:
        raise ValueError(feature)
    row = db.session.scalar(db.select(ExperimentGrant).filter_by(feature=feature, org_id=org.id))
    if on and row is None:
        db.session.add(ExperimentGrant(feature=feature, org_id=org.id, created_by_id=by.id))
    elif not on and row is not None:
        db.session.delete(row)
    forget()
    _logged(feature, "on" if on else "off", by, target=f"org {org.id}")


def _everyone(feature):
    from app.extensions import db
    from app.models import ExperimentGrant as E

    return db.and_(E.feature == feature, E.user_id.is_(None), E.group_id.is_(None), E.org_id.is_(None))


def set_everyone(feature, on, by):
    """On or off for every account; persons kept out stay out."""
    from app.extensions import db
    from app.models import ExperimentGrant

    if feature not in KEYS:
        raise ValueError(feature)
    there = db.session.scalar(db.select(ExperimentGrant.id).where(_everyone(feature)).limit(1)) is not None
    if on and not there:
        db.session.add(ExperimentGrant(feature=feature, created_by_id=by.id))
    elif not on:
        db.session.execute(db.delete(ExperimentGrant).where(_everyone(feature)))
    forget()
    _logged(feature, "on" if on else "off", by, target="everyone")


def _logged(feature, state, by, **fields):
    """A change of an experimental feature in the security log (Abgleich 2026-10-15, AU-7): which, to what, by whom."""
    from app import security_log

    security_log.event("experiment_changed", kind=feature, reason=state, by=getattr(by, "id", None), **fields)


def overview(feature):
    """For Administration > Experimentell: on for everyone?, the persons it is switched on for, the persons kept out,
    the groups, the organisations (with how many people are in them), and how often it is in use."""
    from app import orgs as org_module
    from app.extensions import db
    from app.models import ExperimentGrant, Organisation, User, WorkGroup

    people = [u for u in db.session.scalars(db.select(User).where(User.experiments.is_not(None)))
              if feature in (u.experiments or ())]
    kept = list(db.session.scalars(db.select(User).join(ExperimentGrant, ExperimentGrant.user_id == User.id)
                                   .where(ExperimentGrant.feature == feature, ExperimentGrant.allowed.is_(False))))
    groups = list(db.session.scalars(db.select(WorkGroup).join(ExperimentGrant, ExperimentGrant.group_id == WorkGroup.id)
                                     .where(ExperimentGrant.feature == feature)))
    orgs = sorted(db.session.scalars(db.select(Organisation).join(ExperimentGrant, ExperimentGrant.org_id == Organisation.id)
                                     .where(ExperimentGrant.feature == feature)), key=lambda o: (o.name.casefold(), o.id))
    by_name = lambda u: (u.display_name.casefold(), u.id)  # noqa: E731
    everyone = db.session.scalar(db.select(ExperimentGrant.id).where(_everyone(feature)).limit(1)) is not None
    return {"everyone": everyone, "people": sorted(people, key=by_name), "kept": sorted(kept, key=by_name),
            "groups": sorted(groups, key=lambda w: (w.name.casefold(), w.id)),
            "orgs": [(o, org_module.counts(o)["people"]) for o in orgs], "uses": uses(feature),
            "only_everyone": _only_through_everyone(people, kept, groups, orgs) if everyone else 0}


def _only_through_everyone(people, kept, groups, orgs=()):
    """How many accounts see a feature only because it is on for everyone: they lose it when "für alle" ends (the
    question of "Für alle ausschalten" names the number, UI audit 2026-10-07, W90)."""
    from app.extensions import db
    from app.models import User, WorkGroupMember

    keep = {u.id for u in people} | {u.id for u in kept}
    members = set(db.session.scalars(db.select(WorkGroupMember.user_id).where(
        WorkGroupMember.group_id.in_([w.id for w in groups])))) if groups else set()
    if orgs:
        from app import orgs as org_module

        members |= set(db.session.scalars(org_module.user_ids_in([o.id for o in orgs])))
    return sum(1 for uid in db.session.scalars(db.select(User.id).where(User.pending.is_(False)))
               if uid not in keep and uid not in members)


def upgrade_legacy():
    """Until October 2026 one tick switched on all experimental features (the column user.experimental). Whoever had
    it set gets a tick for each feature that is experimental now, once, at the start of the server (create_app);
    the old column is cleared then. Returns how many accounts were carried over. Never raises."""
    from flask import current_app

    from app.extensions import db
    from app.models import User

    try:
        users = list(db.session.scalars(db.select(User).filter(User.experimental_legacy.is_(True))))
        for user in users:
            user.experiments = sorted(set(user.experiments or ()) | set(KEYS))
            user.experimental_legacy = False
        db.session.commit()
        return len(users)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("experimental.upgrade_legacy: the old tick was not carried over")
        return 0
