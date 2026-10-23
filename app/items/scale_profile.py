"""Scale profile (Philipp, 2026-10-07; experimental until 2026-10-11): the drawing of the topic
profile (app/items/topic_feedback.py) for ordinary questions. Per chosen variable one line from its low to its high
end, one's own value as a filled dot and on request the sample's mean as a ring.

A variable is a scale (Likert matrix or semantic differential, as the mean of a person), one of its subscales (an
instrument such as the Big Five gives one row per dimension), a single Likert statement or a rating; any number from
one to MAX_VARIABLES, in the order of the questionnaire. Like all live comparisons: questions before the element,
from a loop only in the same block (then the answers of the same pass), sample values per row only from MIN_N
participants on, only completed real participations, the preview also test and dummy data. The export holds the
number of participants the sample values rested on (<code>_n, 0: none shown)."""
import statistics

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_int, r_list, whole  # rules for survey files, see base.py
from app.items.sample_compare import MIN_N, NUMERIC, SampleCompare, _subscale_rule
from app.items.topic_feedback import _profile_svg
from app.lang import Both, de_en
from app.ui_i18n import tr

MAX_VARIABLES = 12


@register
class ScaleProfile(SampleCompare):
    key = "scale_profile"
    import_rules = {"sources": r_list(MAX_VARIABLES, r_int(1, 10 ** 12)),
                    "subscales": r_list(MAX_VARIABLES, _subscale_rule), "show_sample": r_flag,
                    "lock_answers": r_flag}
    label = Both("Skalenprofil", "Scale profile")
    short = Both("Eigene Werte auf mehreren Skalen", "Own values on several scales")
    description = Both("Die eigenen Werte auf mehreren Skalen oder Subskalen untereinander (z. B. die "
                       f"fünf Dimensionen der Big Five), auf Wunsch neben dem Mittel der Stichprobe (ab {MIN_N})",
                       "One's own values on several scales or subscales one below the other (e.g. the "
                       f"five dimensions of the Big Five), if you wish next to the sample's mean (from {MIN_N})")
    order = 57
    fact_sheet = FactSheet(
        "feedback",
        keywords=["Profil", "Profile", "Rückmeldung", "Feedback", "Skalenwerte", "Scale scores"],
        can=[("Zeigt die eigenen Werte auf mehreren Skalen, Subskalen, Likert-Aussagen oder Bewertungen untereinander (bis 12).",
              "Shows one’s own values on several scales, subscales, Likert statements or ratings one below the other (up to 12)."),
             ("Auf Wunsch je Zeile der Mittelwert der Stichprobe als Ring (ab 5 Personen).",
              "If you wish, the sample’s mean per row as a ring (from 5 people).")],
        good_for=[("Rückmeldung zu Persönlichkeit oder Einstellungen als Anreiz zur Teilnahme",
                   "Feedback on personality or attitudes as an incentive to take part")],
        not_for=[("Ergebnisse, die die späteren Antworten nicht beeinflussen sollen",
                  "Results that must not influence later answers")],
        designs=["cross"],
        examples=[("Ihr Profil auf den fünf Dimensionen der Big Five",
                   "Your profile on the five dimensions of the Big Five")],
    )
    category = "feedback"
    displays = {}
    has_answer_mode = False

    def default_config(self):
        return {"sources": [], "subscales": [], "show_sample": True, "lock_answers": True}

    def default_texts(self, lang):
        return {"question": tr(lang, "Ihr Profil", "Your profile")}

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        texts[lang] = {**texts.get(lang, {}), "question": form.get("question", "").strip()}
        item.texts = texts
        if structural:
            values = form.getlist("sources") if hasattr(form, "getlist") else [form.get("sources", "")]
            ids, subs = self.read_sources(values)
            item.config = {**item.config, "sources": ids[:MAX_VARIABLES], "subscales": subs[:MAX_VARIABLES],
                           "show_sample": bool(form.get("show_sample"))}

    def candidates(self, item):
        return [c for c in super().candidates(item) if c.type in NUMERIC]

    def validate(self, item):
        problems = []
        srcs = self.sources(item)
        if not srcs:
            problems.append(de_en("bitte mindestens eine Variable wählen", "please choose at least one variable"))
        allowed = {i.id for i in self.candidates(item)}
        if any(s.id not in allowed for s in srcs):
            problems.append(de_en("die Variablen müssen vor diesem Element stehen und Skalen, Likert, semantisches "
                                  "Differential oder Bewertung sein (aus einer Schleife nur im selben Block)",
                                  "the variables must come before this element and be scales, Likert, semantic "
                                  "differential or rating (from a loop only in the same block)"))
        missing = len(item.config.get("sources", [])) - len(srcs)
        if missing:
            problems.append(de_en("{n} ausgewählte Frage(n) gibt es nicht mehr", "{n} chosen question(s) no longer exist", n=missing))
        return problems

    def profile(self, item, lang, own_answers, preview=False, row=None):
        """{"rows": [{name, lo, hi, low, high, own, sample, own_text, sample_text}], "n", "svg"} or None."""
        from app.units import number

        srcs = self.sources(item)
        if not srcs:
            return None
        show_sample = item.config.get("show_sample", True)
        subs = [self.effective_subscale(item, src, k) for k, src in enumerate(srcs)]
        samples = self.samples(item, srcs, subs, preview, row) if show_sample else [(0, None)] * len(srcs)
        own_answers = own_answers or {}
        out_rows, ns = [], []
        for k, src in enumerate(srcs):
            sub = subs[k]
            lo, hi, name, low, high = self.axis(src, lang, sub)
            given = own_answers.get(src.id, own_answers.get(str(src.id)))
            own = self.value(src, given, sub) if given is not None else None
            n, sample = samples[k]
            ns.append(n)
            out_rows.append({"name": name, "lo": lo, "hi": hi, "low": low, "high": high, "own": own, "sample": sample,
                             "own_text": number(own, 1, lang) if own is not None else "",
                             "sample_text": number(sample, 1, lang) if sample is not None else ""})
        shown = any(r["sample"] is not None for r in out_rows)
        return {"rows": out_rows, "show_sample": show_sample, "sample_shown": shown, "n": max(ns) if shown else 0,
                "few": show_sample and not shown, "svg": _profile_svg(out_rows, lang)}

    def samples(self, item, srcs, subs, preview=False, row=None):
        """[(n, mean or None below MIN_N)] per source: the sample's values. Worked out once for the answers kept by
        app/balance.py, not on every page and every submit (one second each with 2000 participations, performance
        audit 2026-10-09, N-5); the same answers give the same numbers."""
        from app import balance

        lists = self.answer_lists(item, preview, row)

        def build():
            pool = self.pool_of(item, lists)
            out = []
            for src, sub in zip(srcs, subs):
                values = [v for v in self.values_of(src, [a.get(str(src.id)) for a in pool], sub) if v is not None]
                out.append((len(values), statistics.fmean(values) if len(values) >= MIN_N else None))
            return out

        key = ("scale_profile", item.id, item.survey.qversion, row, bool(preview), tuple(s.id for s in srcs), tuple(subs))
        return balance.derived(key, lists, build)

    # --- answer: the number of participants the sample values rested on -------------------------

    def parse_answer(self, item, form, lang="de", meta=None):
        from flask import g, has_request_context

        n = whole(form.get(f"i{item.id}_n", ""))
        if n is None:
            return None  # the element showed nothing (no sources)
        response = g.get("participation") if has_request_context() else None
        if response is not None:
            # a participation: the hidden field only says that the page showed the element; the number is worked out
            # here again from the stored answers (of the same pass in a loop), a browser can send any (code review
            # 2026-10-08, N7)
            row = (meta or {}).get("row")
            stored = response.answers or {}
            own = {str(src.id): stored.get(self.pool_key(item, src, row)) for src in self.sources(item)}
            shown = self.profile(item, lang, own, preview=bool(getattr(response, "is_preview", False)), row=row)
            return {"n": shown["n"] if shown else 0}
        return {"n": n}  # the dummy runs of the researchers: as the page sent it

    def is_missing(self, item, answer):
        return False

    def export_columns(self, item):
        return [(f"{item.code}_n", f"Teilnehmende im Skalenprofil (0 = unter {MIN_N}, nicht gezeigt)")]

    def export_values(self, item, answer):
        return {f"{item.code}_n": (answer or {}).get("n")}
