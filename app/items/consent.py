"""Informed consent: structured facts -> editable text (template or AI) -> agree / decline.

Voluntariness and anonymity are always stated, because the tool guarantees them
(no IP addresses, no identifiers). Declining ends the survey (participant runtime, M4).
"""
import re

from app.emails import MAX_LENGTH as MAX_EMAIL, is_email
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.extensions import db
from app.lang import Both, de_en, english
from app.items.base import r_dict, r_flag, r_int, r_list, r_maybe, r_text  # rules for survey files, see base.py
from app.items.base import AnswerError, ItemType, prepared, whole
from app.ui_i18n import pick, tr

# Explicit confirmations participants tick before agreeing (all on by default). "special" (special categories,
# Art. 9 GDPR) and "audio" (a voice message) come only where the survey needs them and cannot be switched off
# (comparison with the RWTH template 2026-10-20, L8 L9); which a started survey asks stays as at its start. "ai_chat"
# likewise with the element "KI-Chat" (app/ai_chat.py; concept ki-assistent-tagebuch-konzept.md, E9).
CONFIRMATIONS = ("age", "voluntary", "data", "special", "audio", "ai_chat", "commitment")
REQUIRED = ("special", "audio", "ai_chat")
FORMER_CONFIRMATIONS = ("age", "voluntary", "data", "commitment")  # surveys started before 2026-10-20
CONFIRMATION_LABELS = {  # (German, English) for the editor (editor/base.py, consent_labels)
    "age": ("Mindestalter", "Minimum age"),
    "voluntary": ("Freiwilligkeit", "Voluntary participation"),
    "data": ("Datenverwendung", "Use of the data"),
    "special": ("Besondere Kategorien (Art. 9 DSGVO)", "Special categories (Art. 9 GDPR)"),
    "audio": ("Tonaufnahme", "Audio recording"),
    "ai_chat": ("KI-Chat", "AI chat"),
    "commitment": ("Gewissenhafte Teilnahme", "Careful participation"),
}


def _external_hosts(survey):
    from app import external

    return external.hosts(survey)


def _webservice_hosts(survey):
    from app import webservice

    return webservice.privacy_hosts(survey)


def _marker_days():
    from app.participate import MARKER_DAYS

    return MARKER_DAYS


def _retention(survey, lang, stored_ids):
    """What the standard text says about the retention periods of the survey (app/retention.py); "" if none."""
    from app import retention

    return retention.consent_sentence(survey, lang, bool(stored_ids), _has_split(survey))


def _location(survey, lang):
    """What the consent says about the location element (items/geolocation.py) in `lang`; None without one. Names the
    finest level the survey stores and, if set, the estimate from the IP address on a refusal."""
    from app.items import geolocation

    level = geolocation.finest(survey)
    if level is None:
        return None
    stored = {
        "exact": tr(lang, "Wir speichern die genaue Position, wie genau die Ortung war sowie Postleitzahl und Bundesland, "
                    "in denen sie liegt.", "We store the exact position, how accurate it was, and the postal code and "
                    "federal state it lies in."),
        "100m": tr(lang, "Wir speichern die auf etwa 100 m gerundete Position, wie genau die Ortung war sowie "
                   "Postleitzahl und Bundesland, in denen sie liegt.", "We store the position rounded to about 100 m, how "
                   "accurate it was, and the postal code and federal state it lies in."),
        "1km": tr(lang, "Wir speichern die auf etwa 1 km gerundete Position, wie genau die Ortung war sowie Postleitzahl "
                  "und Bundesland, in denen sie liegt.", "We store the position rounded to about 1 km, how accurate it "
                  "was, and the postal code and federal state it lies in."),
        "plz": tr(lang, "Wir speichern nur Postleitzahl und Bundesland, in denen die Position liegt, und wie genau die "
                  "Ortung war.", "We store only the postal code and federal state the position lies in and how accurate "
                  "it was."),
    }
    text = (tr(lang, "An einer Stelle der Befragung bitten wir Sie, den Standort Ihres Geräts zu übermitteln; Ihr Browser "
               "fragt dafür ausdrücklich nach, und Sie können ablehnen.", "At one point of the survey we ask you to share "
               "the location of your device; your browser asks for permission explicitly, and you may decline.")
            + " " + (stored.get(level) or tr(lang, "Wir speichern nur das Bundesland, in dem die Position liegt, und wie "
                                            "genau die Ortung war.", "We store only the federal state the position lies "
                                            "in and how accurate it was.")))
    if geolocation.ip_fallback(survey):
        text += " " + tr(lang, "Übermitteln Sie den Standort nicht, schätzen wir stattdessen einmalig aus Ihrer "
                         "IP-Adresse Land und Bundesland; gespeichert wird nur dieses Ergebnis, nicht die IP-Adresse.",
                         "If you do not share the location, we estimate country and federal state once from your IP "
                         "address instead; only this result is stored, not the IP address.")
    return text


def member_ref(m):
    """How a consent's "researchers" names a member from now on: "u<account id>", or "m<row id>" for the entry of a
    deleted account (which has no account)."""
    uid = m.user_id or (m.user.id if m.user is not None else None)  # a row not yet written knows only its user
    return f"u{uid}" if uid else f"m{m.id}"


def refs_of(m):
    """What goes into "researchers" for this member."""
    return [member_ref(m)]


def user_refs(user):
    """The same for the account that creates a consent (no member row needed yet)."""
    return [f"u{user.id}"]


def _ticked(m, values):
    """A researcher ticked in the form: by reference, or by the iD a form of before or a script sends."""
    from app import people_keys

    return member_ref(m) in values or (m.user is not None and people_keys.key_for(m.user) in values)


def named(m, chosen):
    """Whether a consent's "researchers" names this member, in either form."""
    return member_ref(m) in chosen


def sender(survey):
    """Who runs the survey, for its first page (UI/UX audit 2026-10-05, V34): the institutions of the researchers its
    consent names, each once; "" without a consent or without affiliations."""
    for block in survey.blocks:
        for item in block.items:
            if item.type == "consent":
                chosen = (item.config or {}).get("researchers", [])
                seen = []
                for m in survey.members:
                    a = (m.affiliation or "").strip()
                    if named(m, chosen) and a and a not in seen:
                        seen.append(a)
                return " · ".join(seen)
    return ""


def _has_split(survey):
    from app import flow

    return flow.has_separated(survey) or bool((survey.contact or {}).get("enabled"))


def default_confirmation(key, lang, config):
    if key == "age":
        age = config.get("min_age") or 18  # without a minimum age the field shows the usual one, never "None" (UI/UX audit 2026-10-16)
        return tr(lang, "Ich bin mindestens {age} Jahre alt.", "I am at least {age} years old.", age=age)
    if key == "voluntary":
        return (tr(lang, "Ich nehme freiwillig teil und weiß, dass ich jederzeit ohne Nachteile abbrechen kann.", "I take part voluntarily and know that I can stop at any time without disadvantage."))
    if key == "data":
        if config.get("open_data"):
            return tr(lang, "Ich bin einverstanden, dass meine anonymen Antworten gespeichert, ausgewertet und veröffentlicht "
                      "werden.", "I agree that my anonymous answers are stored, analysed, and published.")
        return tr(lang, "Ich bin einverstanden, dass meine anonymen Antworten gespeichert und für wissenschaftliche Zwecke "
                  "ausgewertet werden.", "I agree that my anonymous answers are stored and analysed for scientific purposes.")
    if key == "special":
        return tr(lang, "Ich willige ausdrücklich ein, dass meine Angaben zu besonders geschützten Daten (etwa Gesundheit, "
                  "religiöse oder politische Überzeugungen, sexuelle Orientierung) für diese Studie verarbeitet werden "
                  "(Art. 9 Abs. 2 lit. a DSGVO).", "I expressly consent to my details on specially protected data (such as "
                  "health, religious or political beliefs, sexual orientation) being processed for this study "
                  "(Art. 9(2)(a) GDPR).")
    if key == "audio":
        return tr(lang, "Ich bin einverstanden, dass meine Stimme aufgenommen und gespeichert wird, wenn ich eine "
                  "Sprachnachricht aufnehme.", "I agree that my voice is recorded and stored when I record a voice message.")
    if key == "ai_chat":
        return tr(lang, "Ich bin einverstanden, dass meine Nachrichten an den KI-Assistenten und seine Antworten "
                  "gespeichert und für die Studie ausgewertet werden.", "I agree that my messages to the AI assistant "
                  "and its answers are stored and analysed for the study.")
    if key == "commitment":
        return (tr(lang, "Ich werde die Fragen aufmerksam und ehrlich beantworten.", "I will answer the questions carefully and honestly."))
    return ""

# Sentences of the standard text as (German, English); the other languages through the catalog (tr).
# The sentence on anonymity up to 2026-10-20: "anonym" promised too much where people name themselves in free texts.
FORMER_ANONYMOUS = ("Ihre Antworten werden anonym gespeichert. Wir speichern keine IP-Adressen und keine Angaben, die Sie "
                    "identifizieren.", "Your answers are stored anonymously. We do not store IP addresses or any "
                    "information that identifies you.")
ANONYMOUS = ("Ihre Antworten werden ohne Namen und ohne IP-Adresse gespeichert; wir speichern keine Angaben, die Sie "
             "unmittelbar identifizieren.", "Your answers are stored without your name and without your IP address; we "
             "store no information that directly identifies you.")
FREE_TEXT = ("Nennen Sie in Textfeldern bitte keine Namen und keine anderen Angaben, an denen man Sie erkennen könnte.",
             "Please do not give names or other details in text fields by which you could be recognised.")
FORMER_USE = ("Die Daten werden ausschließlich für wissenschaftliche Zwecke verwendet und nicht an Dritte weitergegeben.",
              "The data are used for scientific purposes only and are not passed on to third parties.")
USE = ("Die Daten werden ausschließlich für wissenschaftliche Zwecke verwendet.",
       "The data are used for scientific purposes only.")
# the paragraph "Datenverarbeitung und Ihre Rechte" (Consent.rights_paragraph)
RECIPIENTS = ("Die Antworten liegen auf dem Server der Plattform; deren Betreiber verarbeitet sie nur im Auftrag der "
              "Verantwortlichen der Befragung.", "The answers lie on the server of the platform; its operator processes "
              "them only on behalf of those responsible for the survey.")
KEPT = ("Ohne festgelegte Frist werden die Antworten aufbewahrt, solange sie für die Studie und die Überprüfung ihrer "
        "Ergebnisse gebraucht werden, nach den Regeln guter wissenschaftlicher Praxis in der Regel zehn Jahre, und danach "
        "gelöscht.", "Without a set period, the answers are kept as long as they are needed for the study and for checking "
        "its results, under the rules of good scientific practice usually ten years, and then deleted.")
# with the switch for special categories (Art. 9 GDPR; L8): the explicit consent with a tick of its own
SPECIAL = ("Die Befragung fragt auch nach besonders geschützten Angaben, etwa zur Gesundheit, zu religiösen oder "
           "politischen Überzeugungen oder zur sexuellen Orientierung. Für diese Angaben ist Grundlage Ihre ausdrückliche "
           "Einwilligung (Art. 9 Abs. 2 lit. a DSGVO), die Sie mit einem eigenen Häkchen geben.", "The survey also asks "
           "for specially protected details, such as on health, religious or political beliefs or sexual orientation. "
           "For these details the legal basis is your explicit consent (Art. 9(2)(a) GDPR), which you give with a tick "
           "of its own.")
# open data with voice messages (L9): the recordings stay on the platform (app/zenodo.py publishes none)
VOICE_NOT_PUBLISHED = ("Tonaufnahmen werden nicht veröffentlicht, nur anonyme Daten.",
                       "Voice recordings are not published, only anonymous data.")
LEGAL_BASIS = ("Grundlage ist Ihre Einwilligung (Art. 6 Abs. 1 lit. a DSGVO).",
               "The legal basis is your consent (Art. 6(1)(a) GDPR).")
WITHDRAWAL = ("Sie können Ihre Einwilligung jederzeit mit Wirkung für die Zukunft widerrufen, indem Sie die Befragung "
              "abbrechen oder Ihre Antworten löschen. Die Rechtmäßigkeit der Verarbeitung bis zum Widerruf bleibt davon "
              "unberührt.", "You can withdraw your consent at any time with effect for the future by stopping the survey "
              "or deleting your answers. This does not affect the lawfulness of the processing up to the withdrawal.")
RIGHTS = ("Wer im Sinne der DSGVO verantwortlich ist, wie Sie die Datenschutzbeauftragte erreichen und welche Rechte Sie "
          "haben, etwa auf Auskunft, Löschung oder Beschwerde bei einer Aufsichtsbehörde, steht auf der Datenschutzseite "
          "dieser Befragung.", "Who is the controller under the GDPR, how to reach the data protection officer and which "
          "rights you have, such as access, erasure or a complaint to a supervisory authority, is set out on the privacy "
          "page of this survey.")
# the sentence of the privacy page (participant/privacy.html) on the AI help in coding free texts, word for word
AI_CODING = ("Antworten auf offene Fragen (Freitexte) können für die Auswertung an den KI-Dienst von OpenAI übermittelt "
             "werden, der Vorschläge für Kategorien macht. Übermittelt werden nur die Antworttexte, ohne weitere Angaben zu "
             "Ihrer Teilnahme; OpenAI speichert sie nicht für spätere Abrufe und verwendet sie nicht zum Training. Zur "
             "Missbrauchskontrolle bewahrt OpenAI sie bis zu 30 Tage auf. Haben die Forschenden einen eigenen KI-Zugang "
             "eingetragen (z. B. KI:connect.nrw), gehen die Texte stattdessen an diesen Anbieter, und es gelten dessen "
             "Bedingungen.",
             "Answers to open questions (free text) may be sent to the AI service of OpenAI for the analysis, which "
             "suggests categories. Only the answer texts are sent, without any other details of your participation; "
             "OpenAI does not store them for later retrieval and does not use them for training. To monitor abuse, OpenAI "
             "keeps them for up to 30 days. If the researchers have entered their own AI access (e.g. KI:connect.nrw), "
             "the texts go to that provider instead, under its terms.")


# researchers of several institutions (Art. 26 (2) GDPR): all institutions and one point of contact
JOINT = ("Die beteiligten Einrichtungen ({institutions}) sind gemeinsam für die Verarbeitung verantwortlich (Art. 26 "
         "DSGVO). Anlaufstelle für alle Anliegen, auch zu Ihren Rechten, ist {email}.", "The institutions involved "
         "({institutions}) are joint controllers of the processing (Art. 26 GDPR). The point of contact for all "
         "matters, including your rights, is {email}.")
JOINT_NO_EMAIL = ("Die beteiligten Einrichtungen ({institutions}) sind gemeinsam für die Verarbeitung verantwortlich "
                  "(Art. 26 DSGVO). Ihre Rechte können Sie bei jeder von ihnen geltend machen.", "The institutions "
                  "involved ({institutions}) are joint controllers of the processing (Art. 26 GDPR). You can exercise "
                  "your rights with each of them.")


def joint_sentence(lang, institutions, email):
    """The sentence on joint controllers for the paragraph "Kontakt"; "" for one institution."""
    if not institutions:
        return ""
    listed = ", ".join(institutions)
    return tr(lang, *JOINT, institutions=listed, email=email) if email else tr(lang, *JOINT_NO_EMAIL, institutions=listed)


# since 2026-10-21 the AI coding help runs only over the researchers' own access (app/coding_ai.py): the provider named
AI_CODING_OWN = ("Antworten auf offene Fragen (Freitexte) können für die Auswertung an den KI-Dienst {to} übermittelt "
                 "werden, der Vorschläge für Kategorien macht. Übermittelt werden nur die Antworttexte, ohne weitere "
                 "Angaben zu Ihrer Teilnahme; es gelten die Bedingungen dieses Anbieters.",
                 "Answers to open questions (free text) may be sent to the AI service {to} for the analysis, which "
                 "suggests categories. Only the answer texts are sent, without any other details of your participation; "
                 "the terms of this provider apply.")


def ai_coding_sentence(lang, survey):
    """The sentence on the AI coding help: the own accesses confirmed for the survey by name; a survey confirmed for
    the platform's OpenAI access before 2026-10-21 keeps the sentence of then (its texts may have gone there)."""
    from app import coding

    names, platform = coding.ai_recipients(survey)
    if platform or not names:
        return tr(lang, *AI_CODING)
    return tr(lang, *AI_CODING_OWN, to=", ".join(names))


def anonymous_sentence(lang):
    return tr(lang, *ANONYMOUS) + " " + tr(lang, *FREE_TEXT)


def use_sentence(lang, passed_on):
    """"Verwendung der Daten" without open data: "not passed on to third parties" only where nothing goes anywhere
    else (pooled constructs, a web service, another website, the AI help)."""
    return tr(lang, *USE) if passed_on else tr(lang, *FORMER_USE)


def _passed_on(f):
    return bool(f.get("web_service") or f.get("external_website") or f.get("ai_coding"))


def _ai_coding(survey):
    """True once the researchers confirmed that participants are told about the AI help (app/coding.py, B-1)."""
    from app import coding

    return coding.ai_informed(survey)


def _keeps_answers(survey):
    """True if the survey has a period after which all answers are deleted (app/retention.py)."""
    from app import retention

    return bool(retention.of(survey)["answers"])


# the facts on publishing the data; fixed after the first real consent (apply_facts)
OPEN_DATA_FACTS = ("open_data", "repository", "license")

DEFAULT_TEXTS = {
    "de": {
        "question": "Einwilligungserklärung",
        "agree": "Weiter",
        "disagree": "Ich möchte nicht teilnehmen",
        "agree_note": "Mit „Weiter“ bestätigen Sie, dass Sie die Informationen gelesen haben und der Teilnahme "
                      "zustimmen.",
        "compensation": "",
        "body": "",
    },
    "en": {
        "question": "Informed consent",
        "agree": "Next",
        "disagree": "I do not want to take part",
        "agree_note": "By clicking “Next” you confirm that you have read the information and agree to take part.",
        "compensation": "",
        "body": "",
    },
}

# the standard wordings before the step got one name on every page ("Weiter", UI/UX audit W53): consents made with
# them still count as unchanged, so a language switched on later shows its own standard wording (Item.t, W44)
FORMER_TEXTS = {
    "de": {"agree": "Fortsetzen",
           "agree_note": "Mit „Fortsetzen“ bestätigen Sie, dass Sie die Informationen gelesen haben und der Teilnahme "
                         "zustimmen."},
    "de-du": {"agree": "Fortsetzen",
              "agree_note": "Mit „Fortsetzen“ bestätigst du, dass du die Informationen gelesen hast und der Teilnahme "
                            "zustimmst."},
    "en": {"agree": "Continue",
           "agree_note": "By clicking “Continue” you confirm that you have read the information and agree to take part."},
}


# words with which a text of its own speaks of the deletion code (pre-launch check; ConsentType.shown_body)
ERASE_WORDS = ("löschcode", "lösch-code", "code zum löschen", "deletion code", "code to delete")


def _has_media(survey):
    """True if the survey shows a video or audio stimulus (items/stimulus.py): its playback is logged."""
    return any(i.type == "media" for b in survey.blocks for i in b.items)


def _compact(part, lang):
    """A paragraph "**Label**" + line break + text of the standard text in the compact style "**Label:** text"."""
    m = re.match(r"\*\*(.+?)\*\*\n", part)
    if not m:
        return part
    label = m.group(1).strip()
    label = label if label.endswith(("?", ":", "？", "：")) else label + ("：" if lang == "zh-Hans" else ":")
    return f"**{label}** " + part[m.end():].replace("\n", " ")


@register
class Consent(ItemType):
    key = "consent"
    import_rules = {"duration": r_int(0, 10000, none=True), "min_age": r_int(0, 150, none=True), "open_data": r_flag,
                    "ethics": r_flag, "repository": r_text(200), "ethics_ref": r_text(200), "license": r_text(100), "special_categories": r_flag,
                    "researchers": r_list(50, r_text(40)), "contact_email": r_text(200), "funding": r_text(200),
                    "controller": r_text(300), "dpo": r_text(300),
                    "confirm": r_dict(**{k: r_maybe(r_flag) for k in CONFIRMATIONS})}
    result_kind = None  # everyone who took part agreed: nothing to summarise
    label = Both("Einwilligung", "Consent")
    short = Both("Zustimmung oder Ablehnung", "Agree or refuse")
    description = Both("Informierte Einwilligung mit Zustimmung oder Ablehnung", "Informed consent with agreement or refusal")
    order = 5
    fact_sheet = FactSheet(
        "structure",
        keywords=["Einwilligung", "Consent", "Datenschutz", "Privacy", "DSGVO", "GDPR", "Information"],
        can=[("Informierte Einwilligung mit Zustimmung oder Ablehnung.",
              "Informed consent with agreement or refusal."),
             ("Bestätigungen für Alter, Freiwilligkeit, Daten und Sorgfalt.",
              "Confirmations of age, voluntariness, data and care.")],
        good_for=[("Jede Befragung mit Menschen: Information und Einwilligung vor dem Start",
                   "Every survey with people: information and consent before the start")],
    )
    allowed_in_loop = allowed_in_separated = False  # a decline ends the main session

    def default_config(self):
        return {
            "duration": 10,
            "min_age": 18,
            "open_data": True,  # default: open data (researchers opt out if needed)
            "repository": "",
            "ethics": False,       # ethics committee / IRB approval, if already known
            "ethics_ref": "",      # committee and reference number
            "license": "CC BY 4.0",
            "researchers": [],  # references by account (member_ref)
            "contact_email": "",
            "funding": "",     # funder and grant number, if any (Worum geht es?)
            "controller": "",  # controller under the GDPR with address; empty: the installation's (participant/_controller.html)
            "dpo": "",         # contact of the data protection officer; empty: the installation's (participant/_dpo.html)
            "special_categories": False,  # asks for special categories (Art. 9 GDPR): explicit consent, DPIA hint
            "confirm": {k: True for k in CONFIRMATIONS},
        }

    def default_texts(self, lang):
        return dict(pick(DEFAULT_TEXTS, lang))

    def former_default_texts(self, lang):
        return FORMER_TEXTS.get(lang, {})

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        for key in ("question", "body", "agree", "disagree", "agree_note"):
            lt[key] = form.get(key, "").strip()
        for key in CONFIRMATIONS:
            # empty = use the default wording (follows minimum age / open data automatically)
            lt[f"confirm_{key}"] = form.get(f"confirm_text_{key}", "").strip()[:500]
        texts[lang] = lt
        item.texts = texts
        # Facts are content, not logic: they may be corrected at any time (in the default language view)
        # The other facts (minimum age, contact, ethics vote, compensation, open data, researchers) are kept in the
        # survey settings and the setup wizard (apply_facts); the element itself only has the duration.
        if lang == item.survey.default_language:
            duration = form.get("duration", "").strip()
            # what participants confirm stays fixed once the survey has started: the export reports it as
            # ticked for everyone who agreed (export_values), so a later change would rewrite their answers
            locked = not structural or self.has_real_answers(item)  # also after "Zur Bearbeitung öffnen"
            item.config = {
                **item.config,
                "duration": whole(duration),
                "confirm": item.config.get("confirm", {}) if locked
                else {k: bool(form.get(f"confirm_{k}")) for k in CONFIRMATIONS},
            }

    def apply_facts(self, item, form, lang):
        """Facts from the wizard or the settings; a missing or still untouched standard text follows them (an
        edited text stays). After the start the minimum age is fixed (participants confirmed it); after the first
        real consent also open data, licence and repository (audit 2026-10-10, N-2): the export and the codebook
        report the consent text with open data as agreed by everyone, so switching it later would rewrite what
        earlier participants agreed to. Whoever wants to publish later duplicates the survey."""
        before = self.template_body(item, lang)
        body = ((item.texts or {}).get(lang) or {}).get("body", "")
        min_age, special = item.config.get("min_age"), bool(item.config.get("special_categories"))
        published = {k: item.config.get(k) for k in OPEN_DATA_FACTS}
        self.update_facts(item, form, lang)
        if item.survey.status != "draft" or self.has_real_answers(item):
            # participants confirmed the age and, with special categories, gave their explicit consent to them
            item.config = {**item.config, "min_age": min_age, "special_categories": special}
        if self.open_data_fixed(item):
            item.config = {**item.config, **published}
        if not body or body == before:
            texts = dict(item.texts or {})
            texts[lang] = {**texts.get(lang, {}), "body": self.template_body(item, lang)}
            item.texts = texts

    def open_data_fixed(self, item):
        """True once a real participation agreed: open data, licence and repository stay as they were (N-2)."""
        return self.has_real_answers(item)

    @staticmethod
    def facts_problem(form):
        """Why the facts of the form are refused before anything is saved, or None: a contact e-mail that is no
        address (settings and wizard; before, only the check at the start found it)."""
        email = form.get("contact_email", "").strip()
        if email and not is_email(email):
            return de_en("Kontakt-E-Mail: Bitte eine gültige E-Mail-Adresse angeben oder das Feld leer lassen.",
                         "Contact email: please enter a valid email address or leave the field empty.")
        return None

    def update_facts(self, item, form, lang):
        """Only the key facts (used by the setup wizard); texts and confirmations stay as they are.
        The duration is not asked there (the questionnaire does not exist yet); it keeps its value."""
        duration, min_age = form.get("duration", "").strip(), form.get("min_age", "").strip()
        item.config = {
            **item.config,
            "duration": whole(duration) if "duration" in form else item.config.get("duration"),
            "min_age": whole(min_age),
            "open_data": bool(form.get("open_data")),
            # the details of ethics vote, open data and compensation only count while their box is ticked
            "repository": form.get("repository", "").strip()[:200] if form.get("open_data") else "",
            "ethics": bool(form.get("ethics")),
            "ethics_ref": form.get("ethics_ref", "").strip()[:200] if form.get("ethics") else "",
            "license": (form.get("license", "").strip()[:100] or "CC BY 4.0") if form.get("open_data") else "",
            "researchers": [member_ref(m) for m in item.survey.members if _ticked(m, form.getlist("researchers"))],
            "contact_email": form.get("contact_email", "").strip()[:MAX_EMAIL],
            "special_categories": bool(form.get("special_categories")) if "special_box" in form
            else bool(item.config.get("special_categories")),  # forms without the box keep it
            # forms of before and scripts without these fields keep what is there
            **{key: form.get(key, "").strip()[:size] if key in form else item.config.get(key, "")
               for key, size in (("funding", 200), ("controller", 300), ("dpo", 300))},
        }
        texts = dict(item.texts or {})
        paid = bool(form.get("compensation_on")) if form.get("compensation_box") else True  # forms without the box
        texts[lang] = {**texts.get(lang, {}), "compensation": form.get("compensation", "").strip() if paid else ""}
        item.texts = texts
        for m in item.survey.members:
            if f"aff_{m.id}" in form:
                m.affiliation = form.get(f"aff_{m.id}", "").strip()[:300]

    def confirmation_keys(self, item):
        """The confirmations this consent can ask: in a draft "special" with the switch for special categories and
        "audio" with a voice message (app/items/voice.py); from the start on those of the start (freeze_confirmations),
        since the export reports them as ticked by everyone who agreed (export_values). A survey started before
        2026-10-20 has the four of before."""
        if item.survey.status != "draft":
            return list(item.config.get("confirm_keys") or FORMER_CONFIRMATIONS)
        from app.items import voice

        return [k for k in CONFIRMATIONS if (k != "special" or item.config.get("special_categories"))
                and (k != "audio" or voice.used(item.survey))
                and (k != "ai_chat" or _has_ai_chat(item.survey))]

    def freeze_confirmations(self, item):
        """At the start of a draft (lifecycle.start): the confirmations of this version stay as they are."""
        item.config = {**item.config, "confirm_keys": self.confirmation_keys(item)}

    def confirmations(self, item, lang):
        """Enabled confirmations as [{key, text}] (age only if a minimum age is set; "special" and "audio" always
        where the survey asks them)."""
        enabled = item.config.get("confirm", {k: True for k in CONFIRMATIONS})
        keys = self.confirmation_keys(item)
        out = []
        for key in CONFIRMATIONS:
            if key not in keys or (key not in REQUIRED and not enabled.get(key, True)) or (
                    key == "age" and not item.config.get("min_age")):
                continue
            text = item.t(lang).get(f"confirm_{key}") or default_confirmation(key, lang, item.config)
            out.append({"key": key, "text": text, "default": default_confirmation(key, lang, item.config)})
        return out

    def researchers(self, item):
        chosen = item.config.get("researchers", [])
        return [m for m in item.survey.members if named(m, chosen)]

    def institutions(self, item):
        """The institutions of the responsible researchers, each once (spelling of the first, compared without case
        and surrounding spaces)."""
        seen, out = set(), []
        for m in self.researchers(item):
            name = (m.affiliation or "").strip()
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                out.append(name)
        return out

    def joint(self, item):
        """True if the responsible researchers come from more than one institution: the institutions are then joint
        controllers (Art. 26 GDPR; comparison with the RWTH template 2026-10-20, G)."""
        return len(self.institutions(item)) > 1

    def has_media(self, item):
        return _has_media(item.survey)

    def facts(self, item, lang):
        from app import theme

        c = item.config
        return {
            "platform": "Askurious.de",
            "survey_title": item.survey.title,
            "survey_description": item.survey.description,
            "duration_minutes": c.get("duration"),
            "minimum_age": c.get("min_age"),
            "open_data": c.get("open_data"),
            "repository": c.get("repository"),
            "ethics_approval": (c.get("ethics_ref") or True) if c.get("ethics") else None,
            "license": c.get("license"),
            "compensation": item.t(lang).get("compensation", "") or "none (participants receive no compensation)",
            "researchers": [{"name": m.display_name, "affiliation": m.affiliation} for m in self.researchers(item)],
            "contact_email": c.get("contact_email") or None,
            "funding": c.get("funding") or None,
            # researchers of several institutions: joint controllers (Art. 26 GDPR), the contact e-mail the point of contact
            "joint_controllers": self.institutions(item) if self.joint(item) else None,
            "special_categories": ("the survey asks for special categories of personal data (Art. 9 GDPR); participants "
                                   "give their explicit consent with a tick of its own") if c.get("special_categories") else None,
            "legal_basis": "consent of the participant (Art. 6(1)(a) GDPR), revocable at any time with effect for the future",
            "recipients": ("the answers lie on the server of the platform, whose operator processes them only on behalf of "
                           "the researchers (Art. 28 GDPR)"),
            "ai_coding": ("answers to open questions may be sent to the AI service the researchers entered as their own "
                          "access for suggestions of categories") if _ai_coding(item.survey) else None,
            "anonymous": True,
            "separate_contact_form": _has_split(item.survey),
            "stored_panel_ids": __import__("app.panel", fromlist=["stored_names"]).stored_names(item.survey) or None,
            "technical_information": ("device type, operating system and browser without versions, window width "
                                      "class, orientation, touch screen") if item.survey.paradata else None,
            "page_times": "time spent on each page" if getattr(item.survey, "page_timing", False) else None,
            # a voice message (app/items/voice.py; for everyone since 2026-10-16): the recording itself is stored
            "voice_recordings": ("participants may answer by speaking; the recording of their voice is stored on the "
                                 "platform's own server, no other service, and deleted with the answers")
            if __import__("app.items.voice", fromlist=["used"]).used(item.survey) else None,
            # the element "KI-Chat" (app/ai_chat.py): every message goes to the model of the study's own access
            "ai_chat": _ai_chat_fact(item.survey),
            "media_playback": ("for the video or audio shown: how it was played (start, pause, jumps, which parts "
                               "played, whether the sound was off, whether the window was in the background); no "
                               "camera, no microphone") if _has_media(item.survey) else None,
            "focus_paste": ("how often the participant leaves the window or tab and how often text is pasted into a "
                            "text field (counts only, never what or where)"
                            + ("; also how many characters were pasted and how many input steps (keys, words of the "
                               "phone keyboard or dictation) the text fields received, to recognise text that was neither "
                               "typed nor pasted" if getattr(item.survey, "focus_inputs", False) else ""))
            if getattr(item.survey, "focus_paste", False) else None,
            "automation_check": ("every page carries a field and a note to AI systems that people neither see nor hear "
                                 "read out; only whether they came back filled in is stored, to recognise answers "
                                 "filled in automatically") if getattr(item.survey, "ai_bait", False) else None,
            "repeat_marker": (f"a cookie without any identifier that remembers in the browser for {_marker_days()} days after "
                              "the participation has ended that this survey was taken, so that nobody takes part twice")
            if getattr(item.survey, "repeat_marker", False) else None,
            "retention": _retention(item.survey, "en", __import__("app.panel", fromlist=["stored_names"]).stored_names(item.survey)) or None,
            "personal_code": ("a personal code made of letters and digits the participant knows (first letters of "
                              "names, day of birth ...) links the answers of several waves; it is an answer and is "
                              "deleted together with the answers, not with the identifiers")
            if any(i.type == "personal_code" for b in item.survey.blocks for i in b.items) else None,
            "deletion_code": ("at the end every participant is shown a deletion code; with it they can delete their own "
                              "answers later on the privacy page of the survey, from any device; without the code the "
                              "answers cannot be linked to them; what has been analysed or published by then cannot be "
                              "taken back") if getattr(item.survey, "erase_code", False) else None,
            "external_website": (f"part of the survey takes place on another website ({', '.join(_external_hosts(item.survey))}); "
                                 "a random participation ID is passed on to it (none of the answers), through which the "
                                 "data collected there can be linked to the answers; single results from there and the "
                                 "time spent there are stored with the answers")
            if _external_hosts(item.survey) else None,
            "web_service": (f"at one point the survey's server calls a web service ({', '.join(_webservice_hosts(item.survey))}) "
                            "and passes some of the participant's answers on to it; what the service answers is stored "
                            "with the answers; the web service has its own privacy notice")
            if _webservice_hosts(item.survey) else None,
            "ip_region": ("the country and, within Germany, the federal state the participant takes part from, estimated "
                          "once from the IP address by a lookup on our own server; only this result is stored, never "
                          "the IP address") if getattr(item.survey, "geo_ip", False) else None,
            "location": _location(item.survey, "en"),
            "display_mode": ("whether the survey was shown light or dark (it follows the setting of the device)"
                             if theme.records_scheme(item.survey) else None),
            "stored_data": "answers only; no IP addresses, no browser fingerprints, no tracking cookies",
            "pooled_constructs": _construct_names(item.survey, "en") or None,
            "linked_questions": ("some questions are asked in the same form in other surveys of the same researchers; "
                                 "the anonymous answers to them may be analysed together with the answers from those "
                                 "surveys") if _has_linked(item.survey) else None,
        }

    def masked_facts(self, item, lang):
        """The facts for the AI draft, without the people: names, affiliations and the contact address are personal
        data of the researchers and do not have to leave the server for a text to be written around them. Returns
        (facts with stand-ins like [[NAME_1]], {stand-in: what it stands for}); unmask puts them back."""
        facts, real = self.facts(item, lang), {}
        people = []
        for n, person in enumerate(facts["researchers"], start=1):
            real[f"[[NAME_{n}]]"] = person["name"]
            if person["affiliation"]:
                real[f"[[AFFILIATION_{n}]]"] = person["affiliation"]
            people.append({"name": f"[[NAME_{n}]]", "affiliation": f"[[AFFILIATION_{n}]]" if person["affiliation"] else ""})
        facts["researchers"] = people
        if facts.get("joint_controllers"):  # the institutions again, as the stand-ins of the people's affiliations
            mark = {}
            for stand_in, value in real.items():
                if stand_in.startswith("[[AFFILIATION_"):
                    mark.setdefault(value.strip().casefold(), stand_in)
            facts["joint_controllers"] = [mark.get(name.strip().casefold(), "[[AFFILIATION]]")
                                          for name in facts["joint_controllers"]]
        if facts["contact_email"]:
            real["[[CONTACT_EMAIL]]"] = facts["contact_email"]
            facts["contact_email"] = "[[CONTACT_EMAIL]]"
        return facts, real

    @staticmethod
    def unmask(text, real):
        """The text with the real values in place of the stand-ins of masked_facts. A stand-in the AI left out is
        simply not there; the draft is read and corrected by the researcher in any case."""
        for mark, value in real.items():
            text = text.replace(mark, value)
        return text

    def template_body(self, item, lang, facts=None):
        """The standard text in the compact style "**Label:** text" (used as AI fallback). facts: other facts than
        the item's own (masked_facts)."""
        return "\n\n".join(_compact(p, lang) for p in self._template_parts(item, lang, facts).split("\n\n"))

    def _template_parts(self, item, lang, facts=None):
        """The standard text in `lang`: every sentence through the catalog (ui_catalog.json), so all survey languages
        get it, the du version included."""
        f = facts or self.facts(item, lang)
        people = ", ".join(r["name"] + (f" ({r['affiliation']})" if r["affiliation"] else "") for r in f["researchers"])

        def para(head, *sentences):
            return head + "\n" + " ".join(s for s in sentences if s)

        about = para(tr(lang, "**Worum geht es?**", "**What is this study about?**"),
                     f["survey_description"] or tr(lang, "[Kurze Beschreibung der Studie]", "[Short description of the study]"),
                     tr(lang, "Förderung: {funding}.", "Funding: {funding}.", funding=f["funding"].rstrip("."))
                     if f.get("funding") else "")
        duration = para(tr(lang, "**Ablauf und Dauer**", "**Procedure and duration**"),
                        tr(lang, "Sie beantworten Fragen in einem Online-Fragebogen. Die Teilnahme dauert etwa {minutes} Minuten.",
                           "You will answer questions in an online questionnaire. Taking part takes about {minutes} minutes.",
                           minutes=f["duration_minutes"] or tr(lang, "[Dauer]", "[duration]")))
        diary = getattr(item.survey, "sampling", None)
        if diary is not None:  # a diary study (app/diary.py): the app, the period, the prompts a day
            from app.diary import modules, open_end, per_day, self_start

            days = int(diary.get("period_days") or 14)
            n = round(sum(per_day(m) for m in modules(item.survey)))
            # modules the participants start themselves (a button in the app) send no notifications; a fixed or
            # random module may also be started on an event (self_start)
            self_started = any(self_start(m) for m in modules(item.survey))
            endless = open_end(item.survey)  # no last day: until the researchers end it (diary.open_end)
            if not n and self_started:
                procedure = tr(lang, "Die Studie läuft in der App „Askurious“ für Android, ohne festes Ende: bis die "
                               "Forschenden sie beenden oder Sie aussteigen. Nach einem ersten Fragebogen beginnen Sie "
                               "kurze Befragungen selbst in der App, so oft Sie etwas melden möchten. Jede Antwort dauert "
                               "nur wenige Minuten.",
                               "The study runs in the app “Askurious” for Android, with no fixed end: until the "
                               "researchers end it or you leave. After a first questionnaire you start short surveys "
                               "yourself in the app, whenever you want to report something. Each answer takes only a few "
                               "minutes.") if endless else \
                    tr(lang, "Die Studie läuft in der App „Askurious“ für Android, für jede Person {days} Tage. "
                               "Nach einem ersten Fragebogen beginnen Sie kurze Befragungen selbst in der App, so oft "
                               "Sie etwas melden möchten. Jede Antwort dauert nur wenige Minuten.",
                               "The study runs in the app “Askurious” for Android, for {days} days per person. After a "
                               "first questionnaire you start short surveys yourself in the app, whenever you want to "
                               "report something. Each answer takes only a few minutes.", days=days)
            elif self_started:
                procedure = tr(lang, "Die Studie läuft in der App „Askurious“ für Android, ohne festes Ende: bis die "
                               "Forschenden sie beenden oder Sie aussteigen. Nach einem ersten Fragebogen erhalten Sie "
                               "Benachrichtigungen mit kurzen Fragen, etwa {n} am Tag; manche können Sie auch selbst "
                               "beginnen. Jede Antwort dauert nur wenige Minuten.",
                               "The study runs in the app “Askurious” for Android, with no fixed end: until the "
                               "researchers end it or you leave. After a first questionnaire you receive notifications "
                               "with short questions, about {n} a day; some you can also start yourself. Each answer "
                               "takes only a few minutes.", n=n) if endless else \
                    tr(lang, "Die Studie läuft in der App „Askurious“ für Android, für jede Person {days} Tage. "
                               "Nach einem ersten Fragebogen erhalten Sie Benachrichtigungen mit kurzen Fragen, etwa {n} "
                               "am Tag; manche können Sie auch selbst beginnen. Jede Antwort dauert nur wenige Minuten.",
                               "The study runs in the app “Askurious” for Android, for {days} days per person. After a "
                               "first questionnaire you receive notifications with short questions, about {n} a day; "
                               "some you can also start yourself. Each answer takes only a few minutes.", days=days, n=n)
            else:
                procedure = tr(lang, "Die Studie läuft in der App „Askurious“ für Android, ohne festes Ende: bis die "
                               "Forschenden sie beenden oder Sie aussteigen. Nach einem ersten Fragebogen erhalten Sie "
                               "Benachrichtigungen mit kurzen Fragen, etwa {n} am Tag. Jede Antwort dauert nur wenige "
                               "Minuten.",
                               "The study runs in the app “Askurious” for Android, with no fixed end: until the "
                               "researchers end it or you leave. After a first questionnaire you receive notifications "
                               "with short questions, about {n} a day. Each answer takes only a few minutes.",
                               n=n) if endless else \
                    tr(lang, "Die Studie läuft in der App „Askurious“ für Android, für jede Person {days} Tage. "
                               "Nach einem ersten Fragebogen erhalten Sie Benachrichtigungen mit kurzen Fragen, etwa {n} "
                               "am Tag. Jede Antwort dauert nur wenige Minuten.",
                               "The study runs in the app “Askurious” for Android, for {days} days per person. After a "
                               "first questionnaire you receive notifications with short questions, about {n} a day. "
                               "Each answer takes only a few minutes.", days=days, n=n)
            final = tr(lang, "Wenn die Forschenden die Studie für Sie beenden, folgt einmal eine Abschlussbefragung.",
                       "When the researchers end the study for you, there is one final survey.") if endless else \
                tr(lang, "Am Ende des Zeitraums folgt einmal eine Abschlussbefragung.",
                   "At the end of the period there is one final survey.")
            duration = para(tr(lang, "**Ablauf und Dauer**", "**Procedure and duration**"), procedure,
                            # an exit survey (blocks of the part "exit"): once after the period
                            final if any((b.settings or {}).get("part") == "exit" for b in item.survey.blocks) else "")
        voluntary = para(tr(lang, "**Freiwilligkeit**", "**Voluntary participation**"),
                         tr(lang, "Ihre Teilnahme ist freiwillig. Sie können die Befragung jederzeit ohne Angabe von Gründen "
                            "und ohne Nachteile abbrechen.", "Taking part is voluntary. You can stop at any time without "
                            "giving reasons and without any disadvantage."),
                         tr(lang, "Sie müssen mindestens {age} Jahre alt sein.", "You must be at least {age} years old.",
                            age=f["minimum_age"]) if f["minimum_age"] else "")
        if f["stored_panel_ids"]:
            privacy = [tr(lang, "**Pseudonymität und Datenschutz**", "**Pseudonymity and data protection**"),
                       tr(lang, "Wir speichern Ihre Kennung des Teilnahmeportals ({ids}) zusammen mit Ihren Antworten, um "
                          "die Teilnahme abrechnen und prüfen zu können. Ihre Antworten sind dadurch pseudonym, nicht "
                          "anonym. Wir speichern keine IP-Adressen und keine Namen.", "We store your participant ID of "
                          "the recruitment platform ({ids}) together with your answers so that your participation can be "
                          "paid and checked. Your answers are therefore pseudonymous, not anonymous. We store no IP "
                          "addresses and no names.", ids=", ".join(f["stored_panel_ids"]))]
        elif diary is not None:  # the entries of one person are linked (pseudonymous), with the times of the prompts
            privacy = [tr(lang, "**Pseudonymität und Datenschutz**", "**Pseudonymity and data protection**"),
                       tr(lang, "Ihre Antworten werden gespeichert und über eine zufällige Kennung verbunden, damit "
                          "die Forschenden Veränderungen über die Zeit auswerten können; sie sind dadurch pseudonym. Zu "
                          "jeder Antwort speichern wir, wann Sie gefragt wurden, wann Ihr Telefon die Frage anzeigte, wann "
                          "Sie geantwortet haben, wann die Antwort bei uns einging, und die Zeitzone Ihres Telefons. Die "
                          "App benötigt kein Konto und sendet keine Gerätekennung. Welche personenbezogenen Angaben die Fragen erheben, erklärt die Einwilligung der Studie. Wir speichern keine "
                          "IP-Adressen.",
                          "Your answers are stored and linked by a random identifier so that the "
                          "researchers can analyse changes over time; they are therefore pseudonymous. For each answer we "
                          "store when you were asked, when your phone showed the question, when you answered, when your "
                          "answer reached us, and the time zone of your phone. The app needs no account and sends no device identifier. The study consent explains which personal data the questions collect. We store no IP addresses."),
                       tr(lang, "In der App können Sie die Studie jederzeit verlassen und alle Ihre Antworten löschen.",
                          "In the app you can leave the study at any time and delete all your answers.")]
            if diary.get("invite_only"):  # personal codes (diary.join_settings): the code is linked to the person
                privacy.insert(2, tr(
                    lang, "Sie nehmen mit einem persönlichen Code teil. Wir speichern, welcher Code zu Ihrer Teilnahme "
                    "gehört, damit Sie sie mit dem Code wieder verbinden können, etwa auf einem neuen Telefon; das "
                    "bisherige Telefon verliert dann den Zugang. Wer weiß, wem welcher Code gegeben wurde, kann Ihre "
                    "Antworten Ihnen zuordnen.",
                    "You take part with a personal code. We store which code belongs to your participation so that "
                    "you can connect it again with the code, for example on a new phone; the earlier phone then loses "
                    "access. Anyone who knows who was given which code can link your answers to you."))
        else:
            privacy = [tr(lang, "**Anonymität und Datenschutz**", "**Anonymity and data protection**"),
                       anonymous_sentence(lang),
                       "" if f.get("deletion_code") else tr(
                           lang, "In dem Browser, in dem Sie teilnehmen, können Sie Ihre Antworten bis 15 Minuten nach der "
                           "Teilnahme über die Datenschutzseite dieser Befragung selbst löschen. Danach lassen sie sich "
                           "Ihnen nicht mehr zuordnen und deshalb nicht mehr einzeln löschen.", "In the browser you take "
                           "part with, you can delete your answers yourself on the privacy page of this survey for up to "
                           "15 minutes after taking part. After that they can no longer be linked to you and therefore cannot "
                           "be deleted individually.")]
        survey = item.survey
        privacy += [
            tr(lang, "Sie können Ihre Antworten nachträglich selbst löschen: Am Ende der Befragung erhalten Sie dafür "
               "einen Löschcode, der nur Ihnen angezeigt wird; eingelöst wird er auf der Datenschutzseite dieser "
               "Befragung. Ohne diesen Code lassen sich Ihre Antworten nicht mehr Ihnen zuordnen. Was bis dahin "
               "ausgewertet oder veröffentlicht wurde, lässt sich nicht zurückholen.", "You can delete your answers "
               "yourself later: at the end of the survey you are given a deletion code for this, shown to you only; you "
               "use it on the privacy page of this survey. Without this code your answers can no longer be linked to "
               "you. What has been analysed or published by then cannot be taken back.") if f.get("deletion_code") else "",
            _retention(survey, lang, f["stored_panel_ids"]) if f.get("retention") else "",
            # the code links the waves, so it stays until the answers go (audit 2026-10-10, L-14)
            tr(lang, "Ihr persönlicher Code verbindet Ihre Antworten aus mehreren Befragungen und wird zusammen mit Ihren "
               "Antworten gelöscht.", "Your personal code links your answers from several surveys and is deleted "
               "together with your answers.") if f.get("personal_code") else "",
            tr(lang, "Zusätzlich erfassen wir grobe technische Angaben (Gerätetyp, Betriebssystem und Browser ohne "
               "Versionsnummern, Fensterbreite in Klassen, Ausrichtung, Touchscreen).", "We also record coarse technical "
               "information (device type, operating system and browser without version numbers, window width class, "
               "orientation, touch screen).") if f["technical_information"] else "",
            tr(lang, "Außerdem speichern wir, wie lange Sie auf jeder Seite waren.",
               "We also record how long you spent on each page.") if f.get("page_times") else "",
            # blocks loaded at once (app/blockload.py): the answers of the block wait in the tab until they are sent
            tr(lang, "Damit Ihre Antworten bei einem kurzen Verbindungsabbruch nicht verloren gehen, hält Ihr Browser die Antworten des gerade bearbeiteten Blocks kurz im Sitzungsspeicher dieses Tabs (sessionStorage), bis sie gesendet sind; dieser Speicher wird beim Schließen des Tabs gelöscht.",
               "So that your answers are not lost if the connection drops for a moment, your browser keeps the answers of the block you are working on briefly in the session storage of this tab (sessionStorage) until they are sent; this storage is cleared when the tab is closed.") if getattr(survey, "block_load", False) else "",
            tr(lang, "Bei einer Sprachnachricht wird Ihre Stimme aufgenommen und auf dem Server der Plattform gespeichert, nicht bei anderen Diensten; die Aufnahme wird mit Ihren Antworten gelöscht.",
               "For a voice message your voice is recorded and stored on the platform's server, not with any other service; the recording is deleted with your answers.") if f.get("voice_recordings") else "",
            _ai_chat_sentence(survey, lang) if f.get("ai_chat") else "",
            tr(lang, "Beim Video oder der Tonaufnahme speichern wir, wie Sie sie abgespielt haben: Start, Pause, Sprünge, welche Teile liefen, ob der Ton aus war und ob das Fenster dabei im Hintergrund war.",
               "For the video or audio recording we record how you played it: start, pause, jumps, which parts played, whether the sound was off and whether the window was in the background.") if f.get("media_playback") else "",
            tr(lang, "Wir zählen außerdem, wie oft Sie während der Befragung das Fenster oder den Tab wechseln und wie oft "
               "Sie Text in ein Textfeld einfügen; nicht, was Sie einfügen oder wohin Sie wechseln.", "We also count how "
               "often you switch to another window or tab during the survey and how often you paste text into a text "
               "field; not what you paste or where you switch to.") if f.get("focus_paste") else "",
            tr(lang, "Bei Textfeldern zählen wir zudem, wie viele Zeichen eingefügt wurden und wie viele Eingaben "
               "(Tastendrücke, Wörter der Bildschirmtastatur oder des Diktats) sie erhielten, nicht deren Inhalt.",
               "For text fields we also count how many characters were pasted and how many input steps (key presses, "
               "words of the phone keyboard or of dictation) they received, not their content.")
            if f.get("focus_paste") and getattr(survey, "focus_inputs", False) else "",
            tr(lang, "Um automatisch ausgefüllte Antworten zu erkennen, enthält jede Seite ein Feld und einen Hinweis an "
               "KI-Systeme, die Menschen weder sehen noch vorgelesen bekommen; gespeichert wird nur, ob sie ausgefüllt "
               "zurückkamen.", "To recognise answers filled in automatically, every page contains a field and a note to "
               "AI systems that people neither see nor hear read out; we only store whether they came back filled in.")
            if f.get("automation_check") else "",
            tr(lang, "Damit niemand mehrfach teilnimmt, merkt sich Ihr Browser nach dem Ende Ihrer Teilnahme für {days} "
               "Tage in einem Cookie, dass an dieser Befragung teilgenommen wurde; das Cookie enthält keine Kennung und "
               "keine Ihrer Antworten.", "So that nobody takes part twice, your browser remembers in a cookie for {days} "
               "days after your participation has ended that this survey was taken; the cookie holds no identifier and "
               "none of your answers.", days=_marker_days()) if f.get("repeat_marker") else "",
            tr(lang, "Aus Ihrer IP-Adresse schätzen wir einmalig, aus welchem Land und (in Deutschland) aus welchem "
               "Bundesland Sie teilnehmen. Das geschieht auf unserem eigenen Server; gespeichert wird nur dieses "
               "Ergebnis, nicht die IP-Adresse.", "From your IP address we estimate once which country and (within "
               "Germany) which federal state you take part from. This happens on our own server; only this result is "
               "stored, not the IP address.") if f.get("ip_region") else "",
            _location(survey, lang) if f.get("location") else "",
            tr(lang, "Die Befragung erscheint hell oder dunkel, je nach Einstellung Ihres Geräts; wir speichern, welche "
               "der beiden Darstellungen Sie gesehen haben.", "The survey appears light or dark, following the setting "
               "of your device; we record which of the two you saw.") if f.get("display_mode") else "",
            tr(lang, "Ein Teil der Befragung findet auf einer anderen Website statt ({hosts}). Dorthin übermitteln wir "
               "eine zufällige Teilnahme-Kennung, aber keine Ihrer Antworten; über diese Kennung verbinden wir die dort "
               "erhobenen Daten mit Ihren Antworten. Wir speichern außerdem, wie lange Sie dort waren.", "Part of the "
               "survey takes place on another website ({hosts}). We pass a random participation ID on to it, but none "
               "of your answers; through this ID we link the data collected there to your answers. We also record how "
               "long you spent there.", hosts=", ".join(_external_hosts(survey))) if f.get("external_website") else "",
            tr(lang, "An einer Stelle der Befragung ruft unser Server einen Web Service auf ({hosts}) und übermittelt ihm dabei "
               "einzelne Ihrer Angaben aus der Befragung; was der Dienst antwortet, wird mit Ihren Antworten gespeichert. "
               "Für den Web Service gelten dessen eigene Datenschutzhinweise.", "At one point of the survey our server calls "
               "a web service ({hosts}) and passes some of your answers from the survey on to it; what the service answers "
               "is stored with your answers. The web service has its own privacy notice.",
               hosts=", ".join(_webservice_hosts(survey))) if f.get("web_service") else "",
        ]
        parts = [about, duration, voluntary, para(*privacy)]
        if f["open_data"]:
            repository, licence = f["repository"], f["license"]
            if repository and licence:
                text = tr(lang, "Die anonymen Daten werden im Repositorium {repository} unter der Lizenz {licence} "
                          "veröffentlicht, damit andere Forschende die Ergebnisse überprüfen und die Daten nachnutzen "
                          "können.", "The anonymous data will be published in the {repository} repository under the "
                          "{licence} license so that other researchers can check the results and reuse the data.",
                          repository=repository, licence=licence)
            elif repository:
                text = tr(lang, "Die anonymen Daten werden im Repositorium {repository} veröffentlicht, damit andere "
                          "Forschende die Ergebnisse überprüfen und die Daten nachnutzen können.", "The anonymous data "
                          "will be published in the {repository} repository so that other researchers can check the "
                          "results and reuse the data.", repository=repository)
            elif licence:
                text = tr(lang, "Die anonymen Daten werden unter der Lizenz {licence} veröffentlicht, damit andere "
                          "Forschende die Ergebnisse überprüfen und die Daten nachnutzen können.", "The anonymous data "
                          "will be published under the {licence} license so that other researchers can check the "
                          "results and reuse the data.", licence=licence)
            else:
                text = tr(lang, "Die anonymen Daten werden veröffentlicht, damit andere Forschende die Ergebnisse "
                          "überprüfen und die Daten nachnutzen können.", "The anonymous data will be published so that "
                          "other researchers can check the results and reuse the data.")
            parts.append(para(tr(lang, "**Open Data**", "**Open data**"), text,
                              tr(lang, *VOICE_NOT_PUBLISHED) if f.get("voice_recordings") else ""))
        else:
            parts.append(para(tr(lang, "**Verwendung der Daten**", "**Use of the data**"),
                              use_sentence(lang, passed_on=bool(f["pooled_constructs"] or _passed_on(f)))))
        if f.get("linked_questions"):
            parts.append(para(tr(lang, "**Gleiche Fragen in mehreren Befragungen**", "**The same questions in several surveys**"),
                              tr(lang, "Einige Fragen stellen wir in mehreren unserer Befragungen in gleicher Form. Ihre "
                                 "anonymen Antworten darauf werten wir auch zusammen mit den Antworten aus diesen "
                                 "Befragungen aus, zum Beispiel um Ergebnisse über die Befragungen hinweg zu vergleichen.",
                                 "We ask some questions in the same form in several of our surveys. Your anonymous "
                                 "answers to them are also analysed together with the answers from these surveys, for "
                                 "example to compare results across surveys.")))
        if f["ethics_approval"]:
            ref = f" ({f['ethics_approval']})" if isinstance(f["ethics_approval"], str) else ""
            parts.append(para(tr(lang, "**Ethische Prüfung**", "**Ethics review**"),
                              tr(lang, "Die Studie wurde von einer Ethikkommission geprüft{ref}.",
                                 "The study has been reviewed by an ethics committee{ref}.", ref=ref)))
        comp = item.t(lang).get("compensation", "")
        parts.append(para(tr(lang, "**Vergütung**", "**Compensation**"),
                          comp or tr(lang, "Für die Teilnahme erhalten Sie keine Vergütung.",
                                     "There is no compensation for taking part.")))
        if f["separate_contact_form"]:
            parts.append(para(tr(lang, "**Getrennte Angaben**", "**Separate details**"),
                              tr(lang, "Am Ende können Sie freiwillig weitere Angaben machen (z. B. eine E-Mail-Adresse "
                                 "für eine Verlosung). Diese werden getrennt von Ihren Antworten gespeichert und können "
                                 "ihnen nicht zugeordnet werden.", "At the end you can voluntarily provide further "
                                 "details (e.g. an email address for a raffle). These are stored separately from your "
                                 "answers and cannot be linked to them.")))
        parts.append(self.rights_paragraph(item, lang, f))
        parts.append(para(tr(lang, "**Kontakt**", "**Contact**"),
                          tr(lang, "Die Studie verantwortet {people}.", "The study is run by {people}.",
                             people=people or tr(lang, "[Namen und Einrichtung]", "[names and institution]")),
                          tr(lang, "Bei Fragen erreichen Sie uns unter {email}.", "If you have questions, contact us at "
                             "{email}.", email=f["contact_email"]) if f["contact_email"] else "",
                          joint_sentence(lang, f.get("joint_controllers"), f["contact_email"])))
        return "\n\n".join(parts)

    def rights_paragraph(self, item, lang, facts=None):
        """The paragraph of the standard text with what Art. 13 GDPR asks beyond the facts of the survey (comparison
        with the RWTH template 2026-10-20, L1 L5 L6 L7): where the answers lie and who else receives them, how long
        they are kept without a period of their own, the legal basis, the withdrawal and where the rights stand. Who
        is responsible, the data protection officer and the supervisory authority stand on the privacy page of the
        survey (participant/privacy.html), which the installation fills (instance_overlay/README.md)."""
        f = facts or self.facts(item, lang)
        sentences = (tr(lang, *RECIPIENTS), ai_coding_sentence(lang, item.survey) if f.get("ai_coding") else "",
                     "" if _keeps_answers(item.survey) else tr(lang, *KEPT),
                     tr(lang, *LEGAL_BASIS), tr(lang, *SPECIAL) if f.get("special_categories") else "",
                     tr(lang, *WITHDRAWAL), tr(lang, *RIGHTS))
        return (tr(lang, "**Datenverarbeitung und Ihre Rechte**", "**Data processing and your rights**") + "\n"
                + " ".join(s for s in sentences if s))

    def upgrade_body(self, item, lang, body):
        """A draft's text in `lang` with what the standard text says since 2026-10-20 (upgrade_drafts): the sentence
        on anonymity in its careful wording, "not passed on to third parties" only where that holds, and the paragraph
        "Datenverarbeitung und Ihre Rechte" before "Kontakt" (or at the end) unless the text already names a legal
        basis. An untouched standard text so becomes the new standard text and keeps following the facts."""
        body = body.replace(tr(lang, *FORMER_ANONYMOUS), anonymous_sentence(lang))
        f = self.facts(item, lang)
        if f["pooled_constructs"] or _passed_on(f):
            body = body.replace(tr(lang, *FORMER_USE), tr(lang, *USE))
        if tr(lang, *LEGAL_BASIS) in body or "Art. 6" in body or "6(1)" in body:
            return body
        parts, head = body.split("\n\n"), tr(lang, "**Kontakt**", "**Contact**").rstrip("*")
        rights = self.rights_paragraph(item, lang, f)
        at = next((n for n, p in enumerate(parts) if p.lstrip().startswith(head)), None)
        if at is None:
            return body.rstrip("\n") + "\n\n" + _compact(rights, lang)
        full = parts[at].lstrip()[len(head):].startswith("**\n")  # "**Kontakt**" + line break, not "**Kontakt:** …"
        parts.insert(at, rights if full else _compact(rights, lang))
        return "\n\n".join(parts)

    def shown_body(self, item, lang):
        """The text participants read: the stored text with the facts that changed since it was written (X43). The
        contact address and the way to delete come from the settings when the page is shown, not from a copy made
        when the text was: an address set later is added to the paragraph "Kontakt", the sentence of the standard text
        on deleting in this browser becomes the one on the deletion code (and back), and a text that says nothing
        about a deletion code the survey gives gets the sentence. A text in its own words keeps them."""
        body = item.t(lang).get("body", "") or ""
        if not body.strip():
            return body
        c, survey = item.config or {}, item.survey
        parts = body.split("\n\n")

        def add_to(head, sentence):
            """`sentence` at the end of the paragraph that starts with the bold `head`, else a paragraph of its own."""
            start = head.rstrip("*")
            for n, part in enumerate(parts):
                if part.lstrip().startswith(start):
                    parts[n] = part.rstrip() + " " + sentence
                    return
            parts.append(f"{head.rstrip('*').rstrip(':：')}:** {sentence}" if head.endswith("**") else sentence)

        email = (c.get("contact_email") or "").strip()
        if email and email.lower() not in body.lower():
            add_to(tr(lang, "**Kontakt**", "**Contact**"), tr(lang, "Bei Fragen erreichen Sie uns unter {email}.",
                                                            "If you have questions, contact us at {email}.", email=email))
        browser = tr(lang, "In dem Browser, in dem Sie teilnehmen, können Sie Ihre Antworten bis 15 Minuten nach der "
                     "Teilnahme über die Datenschutzseite dieser Befragung selbst löschen. Danach lassen sie sich "
                     "Ihnen nicht mehr zuordnen und deshalb nicht mehr einzeln löschen.", "In the browser you take "
                     "part with, you can delete your answers yourself on the privacy page of this survey for up to "
                     "15 minutes after taking part. After that they can no longer be linked to you and therefore cannot "
                     "be deleted individually.")
        code = tr(lang, "Sie können Ihre Antworten nachträglich selbst löschen: Am Ende der Befragung erhalten Sie dafür "
                  "einen Löschcode, der nur Ihnen angezeigt wird; eingelöst wird er auf der Datenschutzseite dieser "
                  "Befragung. Ohne diesen Code lassen sich Ihre Antworten nicht mehr Ihnen zuordnen. Was bis dahin "
                  "ausgewertet oder veröffentlicht wurde, lässt sich nicht zurückholen.", "You can delete your answers "
                  "yourself later: at the end of the survey you are given a deletion code for this, shown to you only; you "
                  "use it on the privacy page of this survey. Without this code your answers can no longer be linked to "
                  "you. What has been analysed or published by then cannot be taken back.")
        text = "\n\n".join(parts)
        standard_de, standard_en = list(NO_DELETION.values())[:2]
        legacy_browser = tr(lang, standard_de.replace("15 Minuten", "7 Tage"),
                            standard_en.replace("15 minutes", "7 days"))
        text = text.replace(legacy_browser, browser)
        if getattr(survey, "erase_code", False):
            if code not in text:
                if browser in text:
                    text = text.replace(browser, code)
                elif not (lang in ("de", "de-du", "en") and any(w in text.lower() for w in ERASE_WORDS)):
                    parts = text.split("\n\n")
                    heads = [tr(lang, "**Anonymität und Datenschutz**", "**Anonymity and data protection**"),
                             tr(lang, "**Pseudonymität und Datenschutz**", "**Pseudonymity and data protection**")]
                    head = next((h for h in heads if any(p.lstrip().startswith(h.rstrip("*")) for p in parts)), "")
                    add_to(head, code) if head else parts.append(code)
                    text = "\n\n".join(parts)
        elif code in text:  # no deletion code any more: the text must not promise one
            text = text.replace(code, "" if __import__("app.panel", fromlist=["stored_names"]).stored_names(survey) else browser)
        return text

    @staticmethod
    def settings_problems():
        """The problems of validate() whose fix is in the settings (Einwilligung und Datenschutz), not in the element:
        the pre-launch check links them there (UI/UX audit 2026-10-07, W9)."""
        return (de_en("keine verantwortliche Person ausgewählt (Einstellungen › Einwilligung und Datenschutz)",
                      "no responsible person chosen (Settings › Consent and data protection)"),
                de_en("Kontakt-E-Mail ist ungültig (Einstellungen › Einwilligung und Datenschutz)",
                      "contact email is invalid (Settings › Consent and data protection)"))

    def validate(self, item):
        problems = []
        if not item.text("body"):
            problems.append(de_en("Einwilligungstext fehlt", "Consent text missing"))
        no_person, bad_email = self.settings_problems()
        if not self.researchers(item):
            problems.append(no_person)
        email = item.config.get("contact_email", "")
        if email and not is_email(email):
            problems.append(bad_email)
        if "[" in item.text("body"):
            problems.append(de_en("Platzhalter [...] im Text ersetzen", "replace the placeholders [...] in the text"))
        return problems

    def parse_answer(self, item, form, lang="de", meta=None):
        value = form.get(f"i{item.id}")
        if value not in ("1", "0"):
            raise AnswerError(tr(lang, "Bitte wählen Sie eine der beiden Möglichkeiten.", "Please choose one option."))
        if value == "1":
            missing = [c for c in self.confirmations(item, lang) if not form.get(f"i{item.id}_c_{c['key']}")]
            if missing:
                raise AnswerError(tr(lang, "Bitte bestätigen Sie alle Punkte, um teilzunehmen.", "Please confirm all statements to take part."))
        return value

    def display_value(self, item, form, meta=None):
        return {"ticked": [c for c in CONFIRMATIONS if form.get(f"i{item.id}_c_{c}")]}

    def export_columns(self, item):
        cols = [(item.code, item.text("question"))]
        cols += [(f"{item.code}_{c['key']}", c["text"]) for c in self.confirmations(item, item.survey.default_language)]
        return cols

    def export_values(self, item, answer):
        agreed = None if answer is None else int(answer)
        out = {item.code: agreed}
        # the columns once per export, not per participation (code review 2026-10-07, R4)
        for column in prepared(item, "consent_columns", lambda: [
                f"{item.code}_{c['key']}" for c in self.confirmations(item, item.survey.default_language)]):
            # agreeing requires every confirmation, so they are 1 exactly for those who agreed
            out[column] = None if agreed is None else agreed
        return out

    ends_page = True  # the consent buttons replace the page's "Next" button
    has_answer_mode = False  # always required (app/editor/): the switch Freiwillig/Hinweis/Pflicht would change nothing

    def ui_text(self, item, lang, key):
        """Button labels and note; an emptied field falls back to the default wording."""
        return item.t(lang).get(key) or pick(DEFAULT_TEXTS, lang).get(key, "")

    def declined(self, answer):
        return answer == "0"

    def pool_notice(self, item, lang):
        """The fixed paragraph on the pooled analysis of toolbox constructs (licence.md section 4), shown under the
        consent text and not part of it, so an own or AI text cannot lose it; "" without toolbox constructs.
        A survey that has started with the paragraph of the former standard text inside its text keeps that one:
        participants agreed to what they saw, and the paragraph would stand there twice."""
        names = _construct_names(item.survey, lang)
        if not names:
            return ""
        if item.survey.status != "draft" and LEGACY_POOL.search(item.text("body", lang) or ""):
            return ""
        return pool_text(lang, names)

    def pool_copy(self, item):
        """For the researchers: the pooled analysis of toolbox constructs (licence.md section 4) as a paragraph for
        their own data protection information (institution, ethics application), German and English, beside the
        fixed paragraph of the consent; None without toolbox constructs."""
        names = _construct_names(item.survey)
        if not names:
            return None
        only = _only_standard(item.survey)
        return {"names": _construct_names(item.survey, "en" if english() else "de"),
                "de": privacy_info_text("de", names, only),
                "en": privacy_info_text("en", _construct_names(item.survey, "en"), only)}


def _has_ai_chat(survey):
    return any(i.type == "ai_chat" for b in survey.blocks for i in b.items)


def _ai_chat_fact(survey):
    """What the facts for the consent say about the KI-Chat (None without one)."""
    if not _has_ai_chat(survey):
        return None
    from app import ai_access, ai_chat

    row = ai_chat.access(survey)
    where = f"{ai_access.provider_label(row.provider)}, model {row.model}" if row else "the study's own model access"
    return ("participants chat with an AI assistant; every message and every answer is stored in full as study data; "
            f"the messages, with e-mail addresses, web addresses and phone numbers masked, are sent to {where}")


def _ai_chat_sentence(survey, lang):
    """The sentence of the consent text on the KI-Chat: that an AI answers, that everything is stored, where it goes."""
    from app import ai_access, ai_chat

    row = ai_chat.access(survey)
    service = (f"{ai_access.provider_label(row.provider)} ({row.model})" if row else
               tr(lang, "den KI-Dienst der Studie", "the AI service of the study"))
    return tr(lang, "In dieser Befragung können Sie mit einem KI-Assistenten schreiben. Ihre Nachrichten und seine "
              "Antworten werden vollständig gespeichert und ausgewertet. Zum Antworten gehen Ihre Nachrichten an "
              "{service}; E-Mail-Adressen, Webadressen und Telefonnummern werden vorher unkenntlich gemacht. Die KI "
              "kann sich irren und ist keine Beratung.", "In this survey you can write with an AI assistant. Your "
              "messages and its answers are stored in full and analysed. To answer, your messages are sent to "
              "{service}; e-mail addresses, web addresses and phone numbers are made unrecognisable first. The AI can "
              "be wrong and is no advice.", service=service)


def privacy_info_text(lang, names, only_standard=False):
    """The pooled analysis as researchers copy it into their own data protection information: what licence.md
    section 4 includes, by whom, pseudonymised, legal basis, deletion and the operator's contact. only_standard:
    the survey has no scale, only demographic standard questions, and the first sentence says so (X6)."""
    from app.hosts import url_for  # the platform's privacy page lives on the main address

    texts = ("Standardisierte Fragebögen: Die Befragung enthält standardisierte Fragebögen ({listed}). Nach Ende der "
             "Befragung führt der Betreiber der Befragungsplattform Askurious Ihre Antworten darauf pseudonymisiert mit"
             " Antworten aus anderen Befragungen zusammen und wertet sie für wissenschaftliche Zwecke aus "
             "(Metaanalysen, z. B. zur Zuverlässigkeit und Weiterentwicklung der Fragebögen). Einbezogen werden nur "
             "diese Antworten, gegebenenfalls mit Ihren Antworten auf demografische Standardfragen (z. B. Alter und "
             "Geschlecht), der Sprache und dem Monat Ihrer Teilnahme; keine Freitexte und keine Kennungen aus dem "
             "Aufruf-Link. Veröffentlicht werden nur zusammengefasste Ergebnisse oder Datensätze, die keiner einzelnen"
             " Befragung zugeordnet werden können. Für diese Auswertung ist der Betreiber selbst verantwortlich "
             "(Kontakt: {url}). Grundlage ist Ihre Einwilligung (Art. 6 Abs. 1 lit. a DSGVO); löschen Sie Ihre "
             "Antworten, gilt das auch für diese Auswertung.",
             "Standardised questionnaires: The survey contains standardised questionnaires ({listed}). After the "
             "survey has ended, the provider of Askurious (see legal notice) combines your answers to them, "
             "pseudonymised, with answers from other surveys and analyses them for scientific purposes (meta-analyses,"
             " e.g. on the reliability and further development of the questionnaires). Only these answers are "
             "included, where applicable with your answers to standard demographic questions (e.g. age and gender), "
             "the language and the month of your participation; no free-text answers and no identifiers from the link "
             "you used. Only summarised results or data sets that cannot be traced to a single survey are published. "
             "The provider of Askurious is itself responsible for this analysis (contact: {url}). The legal basis is your consent "
             "(Art. 6(1)(a) GDPR); if you delete your answers, this applies to this analysis as well.")
    # both languages at once beside each other, whatever the researcher's own language: not tr() or de_en()
    text = texts[lang != "de"]
    if only_standard:  # age and gender are no questionnaires (UI/UX audit 2026-10-09, X6)
        text = text.replace("Die Befragung enthält standardisierte Fragebögen ({listed})",
                            "Die Befragung enthält demografische Standardfragen ({listed})").replace(
            "The survey contains standardised questionnaires ({listed})",
            "The survey contains standard demographic questions ({listed})")
    return text.format(listed=", ".join(names), url=url_for("public.privacy", _external=True))


# the paragraph of the former standard text (up to 2026-10-04), also in the compact form "**Label:** text"
LEGACY_POOL = re.compile(r"(?:^|\n\n)\*\*(?:Standardisierte Fragebögen|Standardised questionnaires):?\*\*.*?(?=\n\n|\Z)", re.S)


def strip_legacy_pool(body):
    """The consent text without the paragraph of the former standard text on pooled constructs."""
    return LEGACY_POOL.sub("", body or "").strip("\n")


def pool_text(lang, names):
    """The fixed paragraph on pooled constructs (Consent.pool_notice) in the formatting of the consent text: only
    what consent needs (what, by whom, pseudonymised, legal basis, deletion); the details stand on the privacy page
    of the survey (participant/privacy.html)."""
    return tr(lang, "**Standardisierte Fragebögen**\n"
              "Der Fragebogen enthält standardisierte Elemente ({listed}). Ihre Antworten darauf kann der Betreiber der "
              "Plattform nach Ende der Befragung zusammen mit anderen Befragungen auswerten (Metaanalysen), stets "
              "pseudonymisiert. Grundlage ist Ihre Einwilligung (Art. 6 Abs. 1 lit. a DSGVO); löschen Sie Ihre Antworten, "
              "gilt das auch für diese Auswertung.",
              "**Standardised questionnaires**\n"
              "The questionnaire contains standardised elements ({listed}). After the survey has ended, the platform "
              "operator may analyse your answers to them together with other surveys (meta-analyses), always "
              "pseudonymised. The legal basis is your consent (Art. 6(1)(a) GDPR); if you delete your answers, this "
              "applies to this analysis as well.", listed=", ".join(names))


# The former standard sentence said answers could never be deleted; participants can, in the same browser for 15 minutes
# (UI audit 2026-10-04, R48). Drafts get the new sentence, started surveys keep what participants agreed to.
NO_DELETION = {
    "Weil die Daten anonym sind, können einzelne Antworten nach dem Absenden nicht mehr Ihnen zugeordnet und daher "
    "auch nicht gelöscht werden.":
        "In dem Browser, in dem Sie teilnehmen, können Sie Ihre Antworten bis 15 Minuten nach der Teilnahme über die "
        "Datenschutzseite dieser Befragung selbst löschen. Danach lassen sie sich Ihnen nicht mehr zuordnen und deshalb "
        "nicht mehr einzeln löschen.",
    "Because the data are anonymous, individual answers cannot be linked to you after submission and therefore "
    "cannot be deleted.":
        "In the browser you take part with, you can delete your answers yourself on the privacy page of this survey "
        "for up to 15 minutes after taking part. After that they can no longer be linked to you and therefore cannot be "
        "deleted individually.",
}


def fix_no_deletion(body):
    for old, new in NO_DELETION.items():
        body = body.replace(old, new)
    return body


def upgrade_drafts():
    """At the start of the server (create_app): the paragraph of the former standard text on pooled constructs
    leaves the consent texts of drafts, Consent.pool_notice shows the new one under the text; drafts get the
    paragraph on data processing and rights and the careful wording on anonymity (Consent.upgrade_body). Started
    surveys keep their text (participants agreed to it). Never raises; returns how many consent elements changed."""
    from flask import current_app

    from app.extensions import db
    from app.models import Block, Item, Survey

    changed = 0
    try:
        items = list(db.session.scalars(db.select(Item).join(Block, Item.block_id == Block.id)
                                        .join(Survey, Block.survey_id == Survey.id)
                                        .filter(Item.type == "consent", Survey.status == Survey.DRAFT)))
        for item in items:
            texts = {lang: ({**t, "body": strip_legacy_pool(t.get("body"))}
                            if isinstance(t, dict) and LEGACY_POOL.search(t.get("body") or "") else t)
                     for lang, t in (item.texts or {}).items()}
            texts = {lang: ({**t, "body": fix_no_deletion(t.get("body"))}
                            if isinstance(t, dict) and t.get("body") else t) for lang, t in texts.items()}
            texts = {lang: ({**t, "body": item.kind.upgrade_body(item, lang, t["body"])}
                            if isinstance(t, dict) and t.get("body") else t) for lang, t in texts.items()}
            if texts != (item.texts or {}):
                item.texts = texts
                changed += 1
        if changed:
            db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("consent.upgrade_drafts: the consent texts were left as they are")
        return 0
    return changed


def ensure_consent_block(survey, researcher=None):
    """Every survey has exactly one consent element, alone in its own block.

    The block may sit anywhere (e.g. after a short screening), but it cannot be deleted.
    A missing consent is created at the top (drafts only). Returns True if something changed.
    """
    from app.models import Block, Item

    if any(i.type == "consent" for b in survey.blocks for i in b.items) or not survey.is_editable:
        return False
    from app import item_texts

    kind = Consent()
    config = kind.default_config()
    if researcher is not None and getattr(researcher, "id", None):
        config["researchers"] = user_refs(researcher)  # the account that creates the survey (a User)
    used = {i.code for b in survey.blocks for i in b.items}
    consent = Item(type="consent", code="consent" if "consent" not in used else "consent_1", config=config,
                   # the standard wordings come in each language when shown (app/item_texts.py, S9)
                   texts=item_texts.new(kind, survey.languages, survey.default_language), answer_mode="required")
    block = Block(titles={l: ("Einwilligung" if l == "de" else "Consent") for l in survey.languages})
    block.items = [consent]
    blocks = [block] + list(survey.blocks)
    survey.blocks = blocks
    for n, b in enumerate(blocks):
        b.position = n
    return True


def _has_linked(survey):
    """True if a question of the survey is a linked element (app/linked.py): its answers are analysed together with
    those of other surveys, which the consent says."""
    from app import constructs

    return any(constructs.is_linked_key(((i.config or {}).get("construct") or {}).get("key")) for b in survey.blocks for i in b.items)


def _pooled(survey):
    """The toolbox constructs of a survey in the order of its elements as (key, name, standard question?) per element
    with a construct, one query for all keys with their tags (performance audit 2026-10-09b, P4/V3). Kept per
    request in flask.g under the keys, so the consent editor (pool_copy asks three times), the settings page and the
    participants' consent page ask the database once."""
    from flask import g, has_request_context
    from sqlalchemy.orm import selectinload

    from app import constructs
    from app.models import Construct

    # linked elements are not pooled, nor the RatSWD standard questions (constructs.pooled)
    keys = tuple(k for b in survey.blocks for i in b.items if (k := constructs.toolbox_key(i)) and constructs.pooled(k))
    if not keys:
        return []
    memo = g.setdefault("_pooled_constructs", {}) if has_request_context() else {}
    if keys not in memo:
        found = {c.key: (c.key, c.name, any(t.slug == "status:standardfrage" for t in c.tags))
                 for c in db.session.scalars(db.select(Construct).where(Construct.key.in_(set(keys)))
                                             .options(selectinload(Construct.tags)))}
        memo[keys] = [found[k] for k in keys if k in found]
    return memo[keys]


def _construct_names(survey, lang="de"):
    """The names of the toolbox constructs of a survey whose answers go into the pooled analysis, in the language of
    the text that names them (German for "de", the English name of a seed construct else). Demographic standard
    questions (age, gender) are named only while there is no scale: every new survey has them, and the paragraph
    names them separately as "demografische Standardfragen" (UI/UX audit 2026-10-09, X6)."""
    from app import constructs

    scales, standard = [], []
    for key, name, is_standard in _pooled(survey):
        seed = constructs._SEED_BY_KEY.get(key)
        if not (lang or "de").startswith("de") and seed is not None and name == seed["name"]:
            name = constructs.SEEDS_EN.get(key, {}).get("name", name)
        kind = standard if is_standard else scales
        if name not in kind:
            kind.append(name)
    return scales or standard


def _only_standard(survey):
    """True if the pooled analysis takes only standard questions (age, gender) of this survey, no scale."""
    found = _pooled(survey)
    return bool(found) and all(is_standard for _key, _name, is_standard in found)
