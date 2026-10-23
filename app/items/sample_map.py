"""2D scatter (feedback): all participants so far as points on two variables (scale means or single
items), the own position highlighted. Split off the live sample comparison so each feedback
element has one purpose."""
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en
from app.items.base import r_flag  # rules for survey files, see base.py
from app.items.sample_compare import MIN_N, NUMERIC, SampleCompare
from app.ui_i18n import tr


@register
class SampleMap(SampleCompare):
    key = "sample_map"
    import_rules = {"sources": SampleCompare.import_rules["sources"], "subscales": SampleCompare.import_rules["subscales"],
                    "show_own": r_flag, "lock_answers": r_flag}
    label = Both("2D-Streubild", "2D scatter")
    short = Both("Eigene Position unter allen", "Own position among all")
    description = Both(f"Zwei Variablen gegeneinander: bisherige Teilnehmende als Punkte, eigene Position hervorgehoben (ab {MIN_N})",
                       f"Two variables against each other: participants so far as dots, one's own position highlighted "
                       f"(from {MIN_N})")
    order = 56
    fact_sheet = FactSheet(
        "feedback",
        keywords=["Streudiagramm", "Scatter plot", "Rückmeldung", "Feedback", "Vergleich", "Comparison"],
        can=[("Zwei Variablen gegeneinander, die eigene Position hervorgehoben.",
              "Two variables against each other, one’s own position highlighted."),
             ("Ab 5 Antworten.", "From 5 answers.")],
        good_for=[("Rückmeldung zu zwei Bewertungen zugleich, z. B. Risiko und Nutzen",
                   "Feedback on two evaluations at once, e.g. risk and benefit")],
        not_for=[("Eine einzelne Variable (dann Stichprobenvergleich)", "A single variable (then sample comparison)")],
        designs=["perception", "delphi"],
        examples=[("Ihre Einschätzung von Risiko und Nutzen neben der aller anderen",
                   "Your estimate of risk and benefit next to everyone else’s")],
    )
    displays = {}

    def default_config(self):
        return {"sources": [], "show_own": True, "lock_answers": True}

    def default_texts(self, lang):
        return {"question": tr(lang, "So ordnen Sie sich im Vergleich ein", "Where you stand compared to others")}

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        texts[lang] = {**texts.get(lang, {}), "question": form.get("question", "").strip()}
        item.texts = texts
        if structural:
            ids, subs = self.read_sources([form.get("source", ""), form.get("source_y", "")])
            item.config = {**item.config, "sources": ids, "subscales": subs, "show_own": bool(form.get("show_own"))}

    def candidates(self, item):
        return [c for c in super().candidates(item) if c.type in NUMERIC]

    def validate(self, item):
        problems = []
        srcs = self.sources(item)
        allowed = {i.id for i in self.candidates(item)}
        same = len(srcs) == 2 and srcs[0].id == srcs[1].id and \
            self.effective_subscale(item, srcs[0], 0) == self.effective_subscale(item, srcs[1], 1)
        if len(srcs) != 2 or same:  # two subscales of one instrument are two variables
            problems.append(de_en("bitte zwei verschiedene Fragen für X und Y wählen", "please choose two different questions for X and Y"))
        elif any(s.id not in allowed for s in srcs):
            problems.append(de_en("X und Y müssen vor diesem Element stehen und Skalen, Likert, semantisches Differential "
                                  "oder Bewertung sein (keine Einfachauswahl; aus einer Schleife nur im selben Block)",
                                  "X and Y must come before this element and be scales, Likert, semantic differential or "
                                  "rating (no single choice; from a loop only in the same block)"))
        return problems
