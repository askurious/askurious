"""KI-Chat: participants chat with an AI assistant on a page of the questionnaire, and every message on both sides is
kept as study data (experimental "ai_chat"; app/ai_chat.py holds the access, the calls, the limits and the log).

The texts: "question" (what the chat is for, above it), "greeting" (the first bubble of the assistant, optional) and
"system" (the instructions the model gets, per language; participants never see them). The config: max_messages,
min_messages (how many messages a person writes before "Weiter" goes on; only for a required element) and max_chars.

The answer of the element is worked out on the server from the stored messages when the page is sent:
  {"n": messages of the participant, "chars": their characters, "replies": answers, "crisis": 0/1} or None."""
from flask import g, has_request_context

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import AnswerError, ItemType, r_int, whole
from app.lang import Both, de_en
from app.ui_i18n import tr

MAX_MESSAGES, DEFAULT_MESSAGES = 50, 10
MAX_CHARS, DEFAULT_CHARS = 4000, 1000
MAX_SYSTEM = 8000

EU_AI_ACT = ("Europäische Union. (2024). Verordnung (EU) 2024/1689 zur Festlegung harmonisierter Vorschriften für "
             "künstliche Intelligenz (Verordnung über künstliche Intelligenz). Amtsblatt der Europäischen Union, "
             "L 2024/1689. https://eur-lex.europa.eu/eli/reg/2024/1689/oj")


@register
class AiChat(ItemType):
    key = "ai_chat"
    experimental = "ai_chat"
    label = Both("KI-Chat", "AI chat")
    short = Both("Gespräch mit einem KI-Assistenten", "Conversation with an AI assistant")
    description = Both("Teilnehmende chatten mit einem KI-Assistenten; alle Nachrichten werden als Daten der Befragung "
                       "gespeichert", "Participants chat with an AI assistant; every message is stored as survey data")
    order = 96
    alone_on_page = True
    allowed_in_loop = False
    allowed_in_separated = False
    result_kind = None
    has_answer_mode = True
    long_export = "chat"
    long_export_label = Both("eine Zeile je Nachricht des KI-Chats", "one row per message of the AI chat")
    long_export_note = "One row per message of the AI chat: who wrote it, the text, the masked text sent to the model, " \
                       "the status, the model and its version, the time."
    long_private = True  # what people wrote, in full
    fact_sheet = FactSheet(
        "tasks",
        keywords=["KI", "AI", "Chat", "Chatbot", "Assistent", "Assistant", "LLM", "Sprachmodell"],
        can=[("Ein Gespräch mit dem Modell, das die Befragung selbst hinterlegt; Anweisungen je Sprache.",
              "A conversation with the model the survey itself enters; instructions per language."),
             ("Jede Nachricht beider Seiten wird gespeichert und als Langformat exportiert.",
              "Every message of both sides is stored and exported in long format.")],
        good_for=[("Befragungen darüber, wie Menschen KI-Assistenten nutzen", "Surveys of how people use AI assistants")],
        not_for=[("Hilfe beim Ausfüllen des Fragebogens", "Help with filling in the questionnaire")],
        designs=["cross", "panel"],
        examples=[("Tagebuchstudie: Wofür nutzen Sie heute den Assistenten?",
                   "Diary survey: what do you use the assistant for today?")],
    )
    import_rules = {"max_messages": r_int(1, MAX_MESSAGES), "min_messages": r_int(0, MAX_MESSAGES),
                    "max_chars": r_int(50, MAX_CHARS)}

    def default_config(self):
        return {"max_messages": DEFAULT_MESSAGES, "min_messages": 1, "max_chars": DEFAULT_CHARS}

    def default_texts(self, lang):
        return {"question": tr(lang, "Sie können hier mit einem KI-Assistenten schreiben.",
                               "You can write with an AI assistant here."),
                "greeting": tr(lang, "Hallo! Wobei kann ich helfen?", "Hello! How can I help?"),
                "system": ""}

    def max_messages(self, item):
        return int((item.config or {}).get("max_messages") or DEFAULT_MESSAGES)

    def min_messages(self, item):
        return min(int((item.config or {}).get("min_messages") or 0), self.max_messages(item))

    def max_chars(self, item):
        return int((item.config or {}).get("max_chars") or DEFAULT_CHARS)

    def system_prompt(self, item, lang):
        """The instructions in the participant's language, else in the survey's default language."""
        return (item.text("system", lang) or item.text("system") or "").strip()[:MAX_SYSTEM]

    # --- editor ---------------------------------------------------------------------------------------------------

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        texts = dict(item.texts or {})
        texts[lang] = {**texts.get(lang, {}), "greeting": form.get("greeting", "").strip()[:2000],
                       "system": form.get("system", "").strip()[:MAX_SYSTEM]}
        item.texts = texts
        if structural:
            most = whole(form.get("max_messages", "").strip())
            least = whole(form.get("min_messages", "").strip())
            chars = whole(form.get("max_chars", "").strip())
            most = min(max(most, 1), MAX_MESSAGES) if most else DEFAULT_MESSAGES
            item.config = {**(item.config or {}), "max_messages": most,
                           "min_messages": min(least if least is not None else 1, most),
                           "max_chars": min(max(chars, 50), MAX_CHARS) if chars else DEFAULT_CHARS}

    def validate(self, item):
        from app import ai_chat

        survey = item.survey
        out = []
        if ai_chat.access(survey) is None:
            out.append(de_en("KI-Chat: Die Befragung braucht einen eigenen Modellzugang (unter „KI-Chat: Modellzugang“ "
                             "am Element).", "AI chat: the survey needs its own model access (under “AI chat: model "
                             "access” at the element)."))
        if survey.consent_item is None:
            out.append(de_en("KI-Chat: Die Befragung braucht eine Einwilligung am Anfang; sie nennt den KI-Chat und "
                             "dass alle Nachrichten gespeichert werden.", "AI chat: the survey needs a consent at the "
                             "start; it names the AI chat and that every message is stored."))
        if not self.system_prompt(item, survey.default_language):
            out.append(de_en("KI-Chat: Bitte die Anweisungen an das Modell eintragen.",
                             "AI chat: please enter the instructions for the model."))
        return out

    def check_warnings(self, item):
        from app import ai_chat

        row = ai_chat.access(item.survey)
        out = []
        if row is not None and row.valid_until is not None:
            from app.timeutil import local_today

            left = (row.valid_until - local_today()).days
            if left <= 7:
                out.append(de_en("Der Schlüssel des Modellzugangs läuft in {n} Tagen ab; danach antwortet der KI-Chat "
                                 "nicht mehr.", "The key of the model access runs out in {n} days; after that the AI "
                                 "chat no longer answers.", n=max(left, 0)))
        return out

    def source_groups(self, item):
        return [(de_en("Kennzeichnung als KI (Art. 50)", "Labelling as AI (Art. 50)"), [EU_AI_ACT])]

    # --- participant side -----------------------------------------------------------------------------------------

    def _participation(self):
        response = g.get("participation") if has_request_context() else None
        return response if response is not None and getattr(response, "id", None) else None

    uses_answers = True

    def parse_answer(self, item, form, lang="de", meta=None, answers=None):
        from app import ai_chat

        response = self._participation()
        answer = ai_chat.summary(response.id, item.id) if response is not None else None
        least = self.min_messages(item)
        if item.required and least and (answer or {}).get("n", 0) < least:
            raise AnswerError(tr(lang, "Bitte schreiben Sie mindestens {n} Nachricht(en) an den KI-Assistenten.",
                                 "Please write at least {n} message(s) to the AI assistant.", n=least))
        return answer

    def display_value(self, item, form, meta=None):
        return None

    def is_missing(self, item, answer):
        return not (isinstance(answer, dict) and answer.get("n"))

    def turns_shown(self, item, lang):
        """The conversation so far, for a page shown again: [(role, text)] of the current participation."""
        from app import ai_chat

        response = self._participation()
        if response is None:
            return []
        return [(t.role, t.text) for t in ai_chat.turns(response.id, item.id)
                if t.role == "user" or t.status == "ok"]

    def messages_left(self, item):
        from app import ai_chat

        response = self._participation()
        if response is None:
            return self.max_messages(item)
        done = sum(1 for t in ai_chat.turns(response.id, item.id) if t.role == "user")
        return max(self.max_messages(item) - done, 0)

    # --- export ---------------------------------------------------------------------------------------------------

    def open_columns(self, item):
        """None: the data set holds counts only; the messages themselves are in the long format "chat", which a
        public package has only with the tick for open answers (long_private)."""
        return []

    def export_columns(self, item):
        return [(f"{item.code}_n", de_en("Nachrichten an die KI", "Messages to the AI")),
                (f"{item.code}_chars", de_en("Zeichen dieser Nachrichten", "Characters of these messages")),
                (f"{item.code}_replies", de_en("Antworten der KI", "Answers of the AI")),
                (f"{item.code}_crisis", de_en("Hinweis auf Hilfe gezeigt (1 = ja)", "Hint to help shown (1 = yes)"))]

    def export_values(self, item, answer):
        a = answer if isinstance(answer, dict) else {}
        return {f"{item.code}_n": a.get("n", 0) if a else None, f"{item.code}_chars": a.get("chars", 0) if a else None,
                f"{item.code}_replies": a.get("replies", 0) if a else None,
                f"{item.code}_crisis": a.get("crisis", 0) if a else None}

    def column_level(self, item, column):
        return "nominal" if column.endswith("_crisis") else "metric"

    def long_columns(self, item):
        from app import ai_chat

        return list(ai_chat.LONG_COLUMNS)

    def long_rows(self, item, response):
        from app import ai_chat

        return ai_chat.long_rows(item, response)

    def questionnaire_lines(self, item, lang):
        system = self.system_prompt(item, lang)
        lines = [f"*{de_en('KI-Chat, höchstens {n} Nachrichten', 'AI chat, at most {n} messages', n=self.max_messages(item))}*"]
        if system:
            lines += ["", de_en("Anweisungen an das Modell:", "Instructions for the model:"), "", "> " +
                      system.replace("\n", "\n> ")]
        return lines

    def dummy_fields(self, item, rng, meta, answers=None):
        return {}
