"""IAT-Ergebnis (feedback): after an Implicit Association Test the participant
sees their own result in plain words: which pairing came more readily ("Sie haben *Menschen* eher mit *gut* verbunden als
Roboter"), how strong the difference is (none, slight, moderate, strong from |D| 0.15, 0.35, 0.65: a convention
widespread in IAT feedback, not a result of the literature; the D score itself after Greenwald, Nosek & Banaji,
2003), the D score small beside it and marked on a zero-centred axis from -1 to +1 (the bars of the others on the
same axis), where they stand among the previous participants
(from MIN_N completed participations on; can be switched off) and what the result means and does not mean.

Every text is prefilled and editable (Philipp, 2026-10-05: what fits depends on the study): heading, the sentences
for the two directions and for no difference ({A}, {B}, {X}, {Y} are the labels of the IAT's categories), the names
of the four strengths, one explanation (what it means and what not, with **bold** lead-ins; Philipp, 2026-10-06: one
text instead of two headed sections; older items keep their "meaning" and "caveat" texts, joined) and the note for a
run that cannot be scored.

The palette offers it only when the survey has an IAT (`requires`, editor.palette_types), in the group
"Rückmeldung" like the other live feedback; the IAT itself shows no result (Philipp, 2026-10-06: no button for it in
the IAT's editor). Without a chosen source it uses the last IAT before it.

Stored and exported: the D score shown and the strength (1 = no difference … 4 = strong), computed again on the
server from the stored IAT answer, as the Einordnung does (app/items/score_band.py)."""
import math
import re

from markupsafe import Markup, escape

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en
from app.items.base import MAX_NUMBER, in_lang, r_flag, whole
from app.items.sample_compare import MIN_N, SampleCompare
from app.ui_i18n import tr

CUTS = (0.15, 0.35, 0.65)  # |D| from which a difference counts as slight, moderate, strong (a convention, see above)
STRENGTHS = ("none", "slight", "moderate", "strong")
PLACEHOLDER = re.compile(r"\{([ABXY])\}")  # the categories of the IAT in the result sentences
BIN = 0.1  # width of the bars of the distribution, on the same axis as the own result (beyond it: the outer bars)
SCALE = 1.0  # the own result is marked on an axis from -SCALE to +SCALE (beyond it: at the end), ticks at the CUTS


def strength_of(d):
    """0 (no difference) to 3 (strong) for a D score."""
    return sum(1 for cut in CUTS if abs(d) >= cut)


@register
class IatFeedback(SampleCompare):
    key = "iat_feedback"
    label = Both("IAT-Ergebnis", "IAT result")
    short = Both("Eigenes IAT-Ergebnis in Worten", "Own IAT result in words")
    description = Both("Zeigt nach einem IAT das eigene Ergebnis in Worten, wie stark es ist, wo die "
                       "anderen stehen und was es bedeutet; alle Texte änderbar",
                       "Shows after an IAT one's own result in words, how strong it is, where the others "
                       "stand and what it means; all texts can be changed")
    requires = "iat"  # offered in the palette only when the survey has an IAT
    order = 57
    fact_sheet = FactSheet(
        "feedback",
        keywords=["IAT", "Rückmeldung", "Feedback", "Ergebnis", "Result"],
        can=[("Zeigt nach einem IAT das eigene Ergebnis in Worten.", "Shows one’s own result in words after an IAT."),
             ("Stärke in vier Stufen mit dem D-Wert daneben.", "Strength in four steps with the D score next to it."),
             ("Ab 5 Teilnahmen die Verteilung der bisherigen Teilnehmenden mit der eigenen Position.",
              "From 5 participations, the distribution of the participants so far with one’s own position."),
             ("Erklärt, was das Ergebnis bedeutet und was nicht; alle Texte vorausgefüllt und änderbar.",
              "Explains what the result means and what it does not; all texts filled in and changeable.")],
        good_for=[("Eine faire Rückmeldung nach einem IAT", "Fair feedback after an IAT")],
        not_for=[("Befragungen ohne IAT", "Surveys without an IAT")],
        designs=["cross"],
        examples=[("Sie haben Menschen leichter mit gut verbunden als Roboter.",
                   "You linked humans with good more easily than robots.")],
    )
    displays = {}
    import_rules = {"sources": SampleCompare.import_rules["sources"], "subscales": SampleCompare.import_rules["subscales"],
                    "compare": r_flag, "lock_answers": r_flag}

    TEXT_KEYS = ("result_pos", "result_neg", "result_none", "s_none", "s_slight", "s_moderate", "s_strong",
                 "explanation", "invalid", "others")

    def default_config(self):
        return {"sources": [], "compare": True, "lock_answers": True}

    def default_texts(self, lang):
        return {
            "question": tr(lang, "Ihr IAT-Ergebnis", "Your IAT result"),
            "result_pos": tr(lang, "Sie haben {A} eher mit {X} verbunden als {B}.",
                             "You associated {A} with {X} more easily than {B}."),
            "result_neg": tr(lang, "Sie haben {B} eher mit {X} verbunden als {A}.",
                             "You associated {B} with {X} more easily than {A}."),
            "result_none": tr(lang, "Sie haben {A} und {B} etwa gleich stark mit {X} verbunden.",
                              "You associated {A} and {B} with {X} about equally easily."),
            "s_none": tr(lang, "kein deutlicher Unterschied", "no clear difference"),
            "s_slight": tr(lang, "ein leichter Unterschied", "a slight difference"),
            "s_moderate": tr(lang, "ein mittlerer Unterschied", "a moderate difference"),
            "s_strong": tr(lang, "ein starker Unterschied", "a strong difference"),
            "explanation": tr(lang, "**Was das bedeutet:** Der Test misst, wie schnell Sie Begriffe gemeinsam sortieren. "
                                    "Fällt eine Kombination leichter, sind die Begriffe in Ihrem Gedächtnis enger "
                                    "miteinander verknüpft. Solche Verknüpfungen entstehen durch Erfahrungen, Medien und "
                                    "Kultur, oft ohne dass man es merkt.\n\n"
                                    "**Was es nicht bedeutet:** Das Ergebnis ist keine Aussage über Ihre bewussten "
                                    "Überzeugungen und kein Urteil über Sie als Person. Ein einzelner Durchgang schwankt: "
                                    "Wiederholt man den Test, fällt das Ergebnis oft etwas anders aus. Aussagekräftig wird "
                                    "der Test erst im Vergleich vieler Menschen.",
                              "**What it means:** The test measures how quickly you sort words together. If one "
                              "combination is easier, the words are more closely linked in your memory. Such links come "
                              "from experience, media and culture, often without noticing.\n\n"
                              "**What it does not mean:** The result says nothing about your conscious beliefs and is no "
                              "judgement of you as a person. A single run varies: taking the test again often gives a "
                              "somewhat different result. The test becomes meaningful only when many people are "
                              "compared."),
            "invalid": tr(lang, "Für diesen Durchgang können wir kein Ergebnis berechnen, weil viele Antworten sehr "
                                "schnell gegeben wurden. Das ist kein Fehler von Ihnen.",
                          "We cannot compute a result for this run because many answers were given very quickly. "
                          "That is not your fault."),
            "others": tr(lang, "So verteilen sich die bisherigen Teilnehmenden", "How the previous participants are spread"),
        }

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt["question"] = form.get("question", "").strip()[:300]
        for key in self.TEXT_KEYS:
            if key in form:
                lt[key] = form.get(key, "").strip()[:1500]
        texts[lang] = lt
        item.texts = texts
        if structural:
            ids, subs = self.read_sources([form.get("source", "")])
            item.config = {**item.config, "sources": ids, "subscales": subs, "compare": bool(form.get("compare"))}

    def candidates(self, item):
        """The IATs before this element (in a loop block only from the same block)."""
        from app import loops

        out = []
        for block in item.survey.blocks:
            for other in block.items:
                if other.id == item.id:
                    return out
                if other.type == "iat" and (not loops.active(block) or block.id == item.block_id):
                    out.append(other)
        return out

    def sources(self, item):
        """The chosen IAT, else the last one before this element."""
        chosen = super().sources(item)
        if chosen:
            return chosen
        found = self.candidates(item)
        return found[-1:] if found else []

    def value(self, src, answer, sub=None):
        return src.kind.scores(answer).get("d") if isinstance(answer, dict) else None

    def validate(self, item):
        return [] if self.sources(item) else [de_en("vor diesem Element steht kein IAT, dessen Ergebnis es zeigen könnte",
                                                    "there is no IAT before this element whose result it could show")]

    def labels(self, src, lang):
        cats = src.kind.categories(src, lang)
        return {c: (cats.get(c) or {}).get("label") or c for c in ("A", "B", "X", "Y")}

    def text(self, item, key, lang):
        own = item.t(lang) or {}
        stored = (item.texts or {}).get(lang) or {}
        if key == "explanation" and stored.get(key) is None and (own.get("meaning") or own.get("caveat")):
            # before 2026-10-06: two texts under the headings "Was bedeutet das?" and "Was es nicht bedeutet"
            heads = (tr(lang, "Was das bedeutet:", "What it means:"), tr(lang, "Was es nicht bedeutet:", "What it does not mean:"))
            return "\n\n".join(f"**{h}** {own[k].strip()}" for h, k in zip(heads, ("meaning", "caveat")) if own.get(k))
        return own[key] if own.get(key) is not None else self.default_texts(lang).get(key, "")

    def sentence(self, item, key, lang, names):
        """A result sentence with the names of the IAT's categories in italics for {A}, {B}, {X}, {Y}; any other
        brace stays as written (str.format failed on a stray "{Name}")."""
        return Markup(PLACEHOLDER.sub(lambda m: str(Markup("<em>%s</em>") % names[m.group(1)]),
                                      str(escape(self.text(item, key, lang)))))

    def result(self, item, lang, own_answers, preview=False, row=None):
        """{"d", "valid", "sentence", "strength" (0..3), "strength_label", "pos" (0..100 % on the axis from -1 to +1),
        "ends", "bars", "own_bar", "n", "enough", ...};
        None without an IAT before it."""
        src = next(iter(self.sources(item)), None)
        if src is None:
            return None
        names = self.labels(src, lang)
        answer = (own_answers or {}).get(src.id)
        scores = src.kind.scores(answer) if isinstance(answer, dict) else {}
        d = scores.get("d")
        out = {"source": src, "d": d, "valid": bool(scores.get("valid")), "answered": bool(scores), "strength": None,
               "sentence": "", "strength_label": "", "bars": [], "own_bar": None, "n": 0, "enough": False, "pos": None,
               "ends": (self.sentence(item, "result_neg", lang, names), self.sentence(item, "result_pos", lang, names))}
        if d is not None:
            s = strength_of(d)
            key = "result_none" if s == 0 else ("result_pos" if d > 0 else "result_neg")
            out.update(strength=s, sentence=self.sentence(item, key, lang, names),
                       strength_label=self.text(item, f"s_{STRENGTHS[s]}", lang),
                       pos=self.pos(d))
        if not item.config.get("compare", True):
            return out
        values = [v for v in (self.value(src, a.get(str(src.id))) for a in self.pool(item, preview, row)) if v is not None]
        out.update(n=len(values), enough=len(values) >= MIN_N)
        if out["enough"]:
            edges = [round(-SCALE + BIN * k, 2) for k in range(round(2 * SCALE / BIN) + 1)]
            counts = [0] * (len(edges) - 1)

            def bin_of(v):
                return min(max(int((min(max(v, -SCALE), SCALE - 1e-9) + SCALE) / BIN + 1e-9), 0), len(counts) - 1)

            for v in values:
                counts[bin_of(v)] += 1
            top = max(counts) or 1
            out["bars"] = [{"lo": edges[k], "hi": edges[k + 1], "share": c / len(values), "height": 100 * c / top}
                           for k, c in enumerate(counts)]
            out["own_bar"] = bin_of(d) if d is not None else None
        return out

    @staticmethod
    def pos(d):
        """Where a D score sits on the axis from -SCALE to +SCALE, in % from the left (beyond the ends: at the end)."""
        return 50 * (min(max(d, -SCALE), SCALE) / SCALE + 1)

    def ticks(self, lang):
        """The marks of the axis: 0, the cut-offs of the strengths on both sides and the ends, [(%, label, is_zero)]."""
        comma = tr(lang, ",", ".")
        marks = [0.0] + [sign * c for c in CUTS for sign in (-1, 1)] + [-SCALE, SCALE]
        return [(self.pos(v), ("0" if v == 0 else ("+" if v > 0 else "−") + f"{abs(v):g}".replace(".", comma)), v == 0)
                for v in sorted(marks)]

    # stored: the D score shown and the strength (1 = no difference … 4 = strong)
    def parse_answer(self, item, form, lang="de", meta=None):
        from flask import g, has_request_context

        value, band = form.get(f"i{item.id}_value", ""), form.get(f"i{item.id}_band", "")
        response = g.get("participation") if has_request_context() else None
        if response is not None:  # a participation: computed again from the stored IAT answer, never as sent
            src = next(iter(self.sources(item)), None)
            if not (value and band) or src is None:
                return None
            row = (meta or {}).get("row")
            d = self.value(src, (response.answers or {}).get(self.pool_key(item, src, row)))
            return {"value": round(d, 3), "band": strength_of(d) + 1} if d is not None else None
        try:  # the preview and dummy runs of the researchers: as the page sent it
            answer = {"value": round(float(value), 3), "band": whole(band) or 0} if value and band else None
        except (ValueError, OverflowError):
            return None
        if answer and not (math.isfinite(answer["value"]) and abs(answer["value"]) <= MAX_NUMBER and 1 <= answer["band"] <= 4):
            return None
        return answer

    def is_missing(self, item, answer):
        return False  # only shows something

    def codebook_info(self, item, column, lang, ui=None):
        if column.endswith("_band"):
            return "Kategorie", "1 = kein deutlicher Unterschied; 2 = leicht; 3 = mittel; 4 = stark", [
                in_lang(ui, "wie angezeigt; Grenzen |D| 0,15, 0,35 und 0,65", "as shown; limits |D| 0.15, 0.35 and 0.65")]
        return "Zahl", in_lang(ui, "etwa −2 bis 2", "about −2 to 2"), [
            in_lang(ui, "der gezeigte D-Wert; leer, wenn der Durchgang nicht auswertbar war",
                    "the D score shown; empty if the run could not be scored")]

    def export_columns(self, item):
        return [(f"{item.code}_value", "IAT-Ergebnis: gezeigter D-Wert"),
                (f"{item.code}_band", "IAT-Ergebnis: gezeigte Stärke (1 = kein Unterschied … 4 = stark)")]

    def export_values(self, item, answer):
        answer = answer or {}
        return {f"{item.code}_value": answer.get("value"), f"{item.code}_band": answer.get("band")}
