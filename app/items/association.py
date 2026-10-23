"""Free associations with evaluation (affective imagery): participants write down up to n words or
short phrases that spontaneously come to mind for a stimulus (e.g. "Künstliche Intelligenz") and
then rate each of their own associations from very negative (-2) to very positive (+2).

The method follows the affective imagery technique of risk research (Slovic, Flynn & Layman, 1991;
Peters & Slovic, 1996; Leiserowitz, 2006), building on word association research (Szalay & Deese, 1978).
Two steps on one page: first only the words; the page's "Next" then shows each own word with its
rating (participant.js), "Next" again sends the page. Without JavaScript both steps show at once.

Answer: [{"t": text, "v": -2..2 or None}] in the order given. Export: <code>_<k> (text) and
<code>_<k>_val per slot, plus <code>_mean (mean evaluation of the own associations)."""
import re
import statistics
import unicodedata

from app import units
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_int  # rules for survey files, see base.py
from app.items.base import AnswerError, ItemType, in_lang, whole
from app.lang import Both, de_en
from app.ui_i18n import tr

MAX_SLOTS = 5
MAX_CHARS = 60
VALUES = (-2, -1, 0, 1, 2)
SLOVIC_FLYNN_LAYMAN = ("Slovic, P., Flynn, J. H., & Layman, M. (1991). Perceived risk, trust, and the politics of nuclear "
                       "waste. Science, 254(5038), 1603–1607. https://doi.org/10.1126/science.254.5038.1603")
PETERS_SLOVIC = ("Peters, E., & Slovic, P. (1996). The role of affect and worldviews as orienting dispositions in the "
                 "perception and acceptance of nuclear power. Journal of Applied Social Psychology, 26(16), 1427–1453. "
                 "https://doi.org/10.1111/j.1559-1816.1996.tb00079.x")
LEISEROWITZ = ("Leiserowitz, A. (2006). Climate change risk perception and policy preferences: The role of affect, "
               "imagery, and values. Climatic Change, 77(1–2), 45–72. https://doi.org/10.1007/s10584-006-9059-9")


@register
class Association(ItemType):
    key = "association"
    import_rules = {"slots": r_int(1, MAX_SLOTS), "min": r_int(1, MAX_SLOTS)}
    label = Both("Freie Assoziationen", "Free associations")

    def source_groups(self, item):
        return [(de_en("Methode: affective imagery", "Method: affective imagery"), [SLOVIC_FLYNN_LAYMAN, PETERS_SLOVIC, LEISEROWITZ])]
    short = Both("Begriffe nennen und bewerten", "Name terms and rate them")
    description = Both("Spontane Begriffe zu einem Reiz nennen und jeden eigenen Begriff von negativ bis positiv bewerten",
                       "Name spontaneous terms for a stimulus and rate each of one's own terms from negative to positive")
    order = 34
    fact_sheet = FactSheet(
        "tasks",
        keywords=["Wortassoziation", "Word association", "Assoziationen", "Associations", "Wortwolke", "Word cloud"],
        can=[("Spontane Begriffe nennen und jeden eigenen Begriff von negativ bis positiv bewerten.",
              "Name spontaneous terms and rate each of your own terms from negative to positive.")],
        good_for=[("Erste Gedanken und Bilder zu einem Thema, bevor Fragen sie lenken",
                   "First thoughts and images about a topic before questions steer them"),
                  ("Mentale Modelle und Bewertung neuer Technologien",
                   "Mental models and evaluation of new technologies")],
        not_for=[("Bewertung vorgegebener Begriffe (dann Likert-Skala oder 2D-Positionierung)",
                  "Evaluation of given terms (then Likert scale or 2D positioning)")],
        designs=["perception", "cross"],
        examples=[("Was fällt Ihnen spontan zu „Wasserstoff“ ein?", "What comes to mind about “hydrogen”?")],
    )

    def default_config(self):
        return {"slots": 3, "min": 1}

    def read_shape(self, item):
        """The number of fields: fewer would leave words of earlier answers out of the export (only more is
        allowed once real answers exist; audit 2026-10-12, N-4)."""
        return {"slots": len(self.slots(item))}

    def default_texts(self, lang):
        return {"question": "", "rate": tr(lang, "Wie bewerten Sie diesen Begriff?", "How do you rate this word?"),
                "low": tr(lang, "sehr negativ", "very negative"), "high": tr(lang, "sehr positiv", "very positive")}

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        texts[lang] = {**texts.get(lang, {}), "question": form.get("question", "").strip(),
                       **{k: form.get(k, "").strip()[:80] for k in ("rate", "low", "high")}}
        item.texts = texts
        if structural:
            def num(name, default):
                v = form.get(name, "").strip()
                return whole(v) if whole(v) is not None else default
            slots = min(max(num("slots", 3), 1), MAX_SLOTS)
            item.config = {**item.config, "slots": slots, "min": min(max(num("min", 1), 1), slots)}

    def validate(self, item):
        return [] if item.text("question") else [de_en("Reiz bzw. Frage fehlt", "stimulus or question is missing")]

    def slots(self, item):
        return range(1, item.config.get("slots", 3) + 1)

    def _read(self, item, form):
        out = []
        for k in self.slots(item):
            text = re.sub(r"\s+", " ", form.get(f"i{item.id}_{k}", "")).strip()[:MAX_CHARS]
            v = form.get(f"i{item.id}_{k}_val", "")
            out.append({"t": text, "v": next((x for x in VALUES if str(x) == v), None)})
        return out

    def parse_answer(self, item, form, lang="de", meta=None):
        given = [a for a in self._read(item, form) if a["t"]]
        need = item.config.get("min", 1)
        if item.required and len(given) < need:
            raise AnswerError(tr(lang, "Bitte nennen Sie mindestens {n} Begriffe.", "Please give at least {n} words.", n=need)
                              if need > 1 else tr(lang, "Bitte nennen Sie mindestens einen Begriff.", "Please give at least one word."))
        if item.required and any(a["v"] is None for a in given):
            raise AnswerError(tr(lang, "Bitte bewerten Sie jeden genannten Begriff.", "Please rate every word you gave."))
        return given or None

    def display_value(self, item, form, meta=None):
        return [a for a in self._read(item, form) if a["t"]] or None

    def is_missing(self, item, answer):
        given = answer or []
        return len(given) < item.config.get("min", 1) or any(a.get("v") is None for a in given)

    def open_columns(self, item):
        return [c for c, _ in self.export_columns(item) if not c.endswith(("_val", "_mean"))]  # the words, not their ratings

    def export_columns(self, item):
        cols = []
        for k in self.slots(item):
            cols += [(f"{item.code}_{k}", f"Assoziation {k} (Text)"),
                     (f"{item.code}_{k}_val", f"Bewertung von Assoziation {k} (-2 bis +2)")]
        return cols + [(f"{item.code}_mean", "Mittlere Bewertung der eigenen Assoziationen (-2 bis +2)")]

    def export_values(self, item, answer):
        given = answer or []
        out = {}
        for k in self.slots(item):
            a = given[k - 1] if k <= len(given) else {}
            out[f"{item.code}_{k}"] = a.get("t") or None
            out[f"{item.code}_{k}_val"] = a.get("v")
        vals = [a["v"] for a in given if a.get("v") is not None]
        out[f"{item.code}_mean"] = units.round_half_up(statistics.fmean(vals), 3) if vals else None
        return out

    def codebook_info(self, item, column, lang, ui=None):
        low, high = item.text("low", lang) or "sehr negativ", item.text("high", lang) or "sehr positiv"
        if column.endswith("_mean"):
            return "Zahl", in_lang(ui, "-2 bis 2", "-2 to 2"), [in_lang(
                ui, "Mittelwert über die bewerteten eigenen Assoziationen; leer ohne Bewertung",
                "mean over the person's own associations rated; empty without a rating")]
        if column.endswith("_val"):
            return "Skala", f"-2 = {low}; -1; 0; 1; 2 = {high}", [in_lang(ui, "leer = kein Begriff oder nicht bewertet",
                                                                          "empty = no term or not rated")]
        return "Text", "", [in_lang(ui, "höchstens {n} Zeichen; in der Reihenfolge der Nennung",
                                    "at most {n} characters; in the order given", n=MAX_CHARS)]

    def result_column(self, item, column):
        if column.endswith("_val") or column.endswith("_mean"):
            return "auto", "", None
        return None, "", None  # the texts are summarised as a word list (terms), not per column

    @staticmethod
    def normalize(text):
        # composed characters ("ü" as one letter, however it was typed) and single spaces, so the same word counts once
        text = unicodedata.normalize("NFC", text or "").lower()
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", "", text)).strip()

    def terms(self, item, table, top=30, passes=()):
        """The most frequent associations over the export rows: [(word, count, mean evaluation or None, persons)].
        count is the number of mentions; persons counts each person (export row) once, however often they named
        the word. In a loop the words of all passes count together."""
        found = {}
        for i, r in enumerate(table):
            for k, s in ((k, s) for k in self.slots(item) for s in ([f"_{n}" for n in passes] or [""])):
                word = self.normalize(r.get(f"{item.code}_{k}{s}"))
                if word:
                    entry = found.setdefault(word, [0, [], set()])
                    entry[0] += 1
                    entry[2].add(i)
                    v = r.get(f"{item.code}_{k}_val{s}")
                    if isinstance(v, (int, float)):
                        entry[1].append(v)
        ranked = sorted(found.items(), key=lambda kv: (-kv[1][0], kv[0]))[:top]
        return [(w, n, statistics.fmean(vs) if vs else None, len(who)) for w, (n, vs, who) in ranked]
