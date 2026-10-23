"""Feedback on the topics of a loop (Philipp, 2026-10-07; experimental until 2026-10-11): micro
scenarios, loops over a table or vignettes present one topic per pass and rate it on the same scales (risk, benefit,
value, ...). After the loop these elements show the participant their own evaluations of all topics, and on request
the sample's next to them.

- Topic map (`topic_map`): every topic at the place of its two chosen evaluations (X, Y), one's own as filled points
  with the topic's name; with "show_sample" also the sample's mean per topic, hollow, joined to one's own by a line.
- Topic profile (`topic_profile`): per chosen variable one's own mean over the topics one rated; with "show_sample"
  the sample's mean of these personal means (each participant counts once, whatever number of topics they rated).

The sources are questions inside one loop (scales count as the mean per person and pass, as in the other live
comparisons), the element stands after the loop. Like all live comparisons, sample values only from MIN_N
participants on (per topic on the map); only completed real participations count, the preview also test and dummy
data. The export holds the number of participants the sample values rested on (<code>_n, 0: none shown)."""
import statistics

from markupsafe import Markup, escape

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_int, r_list, whole  # rules for survey files, see base.py
from app.items.sample_compare import MIN_N, NUMERIC, SampleCompare, _short, _subscale_rule, _ticks
from app.lang import Both, de_en
from app.ui_i18n import tr

MAX_VARIABLES = 8
MAX_TOPICS = 60  # topics drawn on the map; a loop with more rows shows the first ones the person rated


class _TopicFeedback(SampleCompare):
    """What both types share: sources inside one loop, the topics of that loop, everyone's answers per pass."""
    category = "feedback"
    displays = {}
    result_kind = None
    has_answer_mode = False
    max_sources = 2

    def default_config(self):
        return {"sources": [], "subscales": [], "show_sample": True, "lock_answers": True}

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        texts[lang] = {**texts.get(lang, {}), "question": form.get("question", "").strip()}
        item.texts = texts
        if structural:
            ids, subs = self.read_sources(self.form_sources(form))
            item.config = {**item.config, "sources": ids[:self.max_sources], "subscales": subs[:self.max_sources],
                           "show_sample": bool(form.get("show_sample"))}

    # --- sources: numeric questions inside a loop before this element --------------------------

    @staticmethod
    def loop_node(block):
        from app import loops

        return loops.node_of(block) if loops.active(block) else None

    def candidates(self, item):
        """Numeric questions in loop blocks before this element; none if the element itself is inside a loop."""
        if self.loop_node(item.block) is not None:
            return []
        out = []
        for block in item.survey.blocks:
            if block.id == item.block_id:
                return out
            if self.loop_node(block) is not None:
                out += [i for i in block.items if i.type in NUMERIC]
        return out

    def loop_of(self, item):
        """The loop node of the sources (the first one's), or None."""
        srcs = self.sources(item)
        return self.loop_node(srcs[0].block) if srcs else None

    def validate(self, item):
        problems = []
        srcs = self.sources(item)
        if self.loop_node(item.block) is not None:
            problems.append(de_en("das Element gehört hinter die Schleife, nicht in sie",
                                  "the element belongs after the loop, not inside it"))
        if len(srcs) < self.min_sources:
            problems.append(self.too_few_text())
        allowed = {i.id for i in self.candidates(item)}
        if any(s.id not in allowed for s in srcs):
            problems.append(de_en("die Variablen müssen in einer Schleife vor diesem Element stehen und Skalen, Likert, "
                                  "semantisches Differential oder Bewertung sein",
                                  "the variables must be in a loop before this element and be scales, Likert, "
                                  "semantic differential or rating"))
        node = self.loop_of(item)
        if node is not None and any((self.loop_node(s.block) or {}).get("id") != node.get("id") for s in srcs):
            problems.append(de_en("alle Variablen müssen aus derselben Schleife stammen", "all variables must come from the same loop"))
        missing = len(item.config.get("sources", [])) - len(srcs)
        if missing:
            problems.append(de_en("{n} ausgewählte Frage(n) gibt es nicht mehr", "{n} chosen question(s) no longer exist", n=missing))
        return problems

    # --- topics and answers ------------------------------------------------------------------

    def topics(self, item, lang):
        """[(row id, name)] of the loop's topics: the values of its visible columns in `lang`."""
        from app import loops

        node = self.loop_of(item)
        if node is None:
            return []
        default = item.survey.default_language
        shown = [c for c in node.get("columns", []) if not c.get("hidden")] or node.get("columns", [])
        out = []
        for n, row in enumerate(node.get("rows", []), start=1):
            name = " · ".join(v for v in (str(loops.value(node, row, c, lang, default)).strip() for c in shown) if v)
            out.append((row["id"], _short(name, 40) or tr(lang, "Thema {n}", "Topic {n}", n=n)))
        return out

    def everyone(self, item, preview=False):
        """[{key: answer}] of the completed participations (real; in the preview also test and dummy), with only the
        answers of the sources in every pass ("id#row"); what quality assurance marks is left out."""
        from app import balance
        from app.extensions import db
        from app.models import Response

        ids = {str(s.id) for s in self.sources(item)}
        survey = item.survey
        sources = ("real", "test", "dummy") if preview else ("real",)

        def load():
            query = db.select(Response.id, Response.answers).filter(
                Response.survey_id == survey.id, Response.status == Response.COMPLETED, Response.source.in_(sources))
            if not preview:
                query = query.filter(Response.is_preview.is_(False))
            drop = balance.marked(survey, sources, fresh=preview)
            return [{k: v for k, v in (given or {}).items() if k.split("#")[0] in ids}
                    for rid, given in db.session.execute(query.order_by(Response.id)) if rid not in drop]

        if preview:
            return load()
        return balance.cached(("topics", survey.id, tuple(sorted(ids))), load)

    def values(self, item, answers, row):
        """The person's value of every source in pass `row` (None where missing)."""
        out = []
        for index, src in enumerate(self.sources(item)):
            answer = answers.get(f"{src.id}#{row}")
            out.append(self.value(src, answer, self.effective_subscale(item, src, index)) if answer is not None else None)
        return out

    @staticmethod
    def own_view(answers):
        """The participant's answers with string keys, as the sample's are stored."""
        return {str(k): v for k, v in (answers or {}).items()}

    # --- answer: the number of participants the sample values rested on -------------------------

    def parse_answer(self, item, form, lang="de", meta=None):
        from flask import g, has_request_context

        n = whole(form.get(f"i{item.id}_n", ""))
        if n is None:
            return None  # the element showed nothing (no sources)
        response = g.get("participation") if has_request_context() else None
        if response is not None:
            # a participation: the hidden field only says that the page showed the element; the number is worked out
            # here again from the stored answers, a browser can send any (code review 2026-10-08, N7)
            shown = self.shown(item, lang, response.answers or {}, preview=bool(getattr(response, "is_preview", False)))
            return {"n": shown["n"] if shown else 0}
        return {"n": n}  # the dummy runs of the researchers: as the page sent it

    def shown(self, item, lang, answers, preview=False):
        """What the element shows a person with these answers (map() or profile()), or None."""
        raise NotImplementedError

    def is_missing(self, item, answer):
        return False

    def export_columns(self, item):
        return [(f"{item.code}_n", f"Teilnehmende im Vergleich der Themen (0 = unter {MIN_N}, nicht gezeigt)")]

    def export_values(self, item, answer):
        return {f"{item.code}_n": (answer or {}).get("n")}


@register
class TopicMap(_TopicFeedback):
    key = "topic_map"
    import_rules = {"sources": r_list(2, r_int(1, 10 ** 12)), "subscales": r_list(2, _subscale_rule), "show_sample": r_flag,
                    "lock_answers": r_flag}
    label = Both("Themenkarte", "Topic map")
    short = Both("Themen auf zwei Bewertungen", "Topics on two evaluations")
    description = Both("Die Themen einer Schleife auf zwei Bewertungen (z. B. Risiko und Nutzen), "
                       f"die eigenen und auf Wunsch die der Stichprobe (ab {MIN_N})",
                       "The topics of a loop on two evaluations (e.g. risk and benefit), one's own and, "
                       f"if you wish, the sample's (from {MIN_N})")
    order = 58
    fact_sheet = FactSheet(
        "feedback",
        keywords=["Themen", "Topics", "Rückmeldung", "Feedback", "Karte", "Map"],
        can=[("Steht hinter einer Schleife, in der jedes Thema auf denselben Skalen bewertet wird.",
              "Stands after a loop in which each topic is rated on the same scales."),
             ("Setzt jedes Thema auf zwei gewählte Bewertungen, zum Beispiel Risiko und Nutzen.",
              "Places each topic on two chosen evaluations, for example risk and benefit."),
             ("Auf Wunsch je Thema der Mittelwert der Stichprobe als Ring (ab 5 Personen).",
              "If you wish, the sample’s mean per topic as a ring (from 5 people).")],
        good_for=[("Rückmeldung nach der Bewertung vieler Themen, z. B. Technologien",
                   "Feedback after rating many topics, e.g. technologies")],
        not_for=[("Befragungen ohne Schleife über Themen", "Surveys without a loop over topics")],
        designs=["within", "perception"],
        examples=[("Ihre Karte der Technologien nach Risiko und Nutzen",
                   "Your map of the technologies by risk and benefit")],
    )
    min_sources = max_sources = 2

    def default_texts(self, lang):
        return {"question": tr(lang, "So haben Sie die Themen eingeschätzt", "How you rated the topics")}

    def form_sources(self, form):
        return [form.get("source", ""), form.get("source_y", "")]

    def too_few_text(self):
        return de_en("bitte zwei verschiedene Variablen für X und Y wählen", "please choose two different variables for X and Y")

    def validate(self, item):
        problems = super().validate(item)
        srcs = self.sources(item)
        if len(srcs) == 2 and srcs[0].id == srcs[1].id and \
                self.effective_subscale(item, srcs[0], 0) == self.effective_subscale(item, srcs[1], 1):
            problems.append(self.too_few_text())
        return problems

    def shown(self, item, lang, answers, preview=False):
        return self.map(item, lang, answers, preview)

    def map(self, item, lang, own_answers, preview=False):
        """{"points": [{name, own, sample, n}], "x", "y", "n", "svg", "show_sample"} or None without two sources."""
        srcs = self.sources(item)
        if len(srcs) != 2:
            return None
        show_sample = item.config.get("show_sample", True)
        mine = self.own_view(own_answers)
        pool = self.everyone(item, preview) if show_sample else []
        points, people = [], set()
        for row, name in self.topics(item, lang):
            own = self.values(item, mine, row)
            pairs = []
            for n, a in enumerate(pool):
                v = self.values(item, a, row)
                if None not in v:
                    pairs.append(v)
                    people.add(n)
            enough = len(pairs) >= MIN_N
            sample = (statistics.fmean(p[0] for p in pairs), statistics.fmean(p[1] for p in pairs)) if enough else None
            if None in own and sample is None:
                continue
            own = None if None in own else tuple(own)
            points.append({"name": name, "own": own, "sample": sample, "n": len(pairs),
                           "own_text": _pair(own, lang), "sample_text": _pair(sample, lang)})
        points = points[:MAX_TOPICS]
        xa = self.axis(srcs[0], lang, self.effective_subscale(item, srcs[0], 0))
        ya = self.axis(srcs[1], lang, self.effective_subscale(item, srcs[1], 1))
        shown = any(p["sample"] for p in points)
        return {"points": points, "x": xa, "y": ya, "show_sample": show_sample, "sample_shown": shown,
                "n": len(people) if shown else 0, "few": show_sample and not shown,
                "svg": _map_svg(points, xa, ya, lang) if points else None}


@register
class TopicProfile(_TopicFeedback):
    key = "topic_profile"
    import_rules = {"sources": r_list(MAX_VARIABLES, r_int(1, 10 ** 12)), "subscales": r_list(MAX_VARIABLES, _subscale_rule),
                    "show_sample": r_flag, "lock_answers": r_flag}
    label = Both("Themenprofil", "Topic profile")
    short = Both("Eigene Bewertung aller Themen", "Own rating of all topics")
    description = Both("Die eigene Bewertung über alle Themen einer Schleife gemittelt (z. B. Risiko, "
                       f"Nutzen, Wert), auf Wunsch neben dem Mittel der Stichprobe (ab {MIN_N})",
                       "One's own evaluation averaged over all topics of a loop (e.g. risk, benefit, value), "
                       f"if you wish next to the sample's mean (from {MIN_N})")
    order = 59
    fact_sheet = FactSheet(
        "feedback",
        keywords=["Themen", "Topics", "Rückmeldung", "Feedback", "Profil", "Profile"],
        can=[("Steht hinter einer Schleife und zeigt je gewählter Variable (bis 8) den eigenen Mittelwert über die bewerteten Themen.",
              "Stands after a loop and shows per chosen variable (up to 8) one’s own mean over the rated topics."),
             ("Auf Wunsch daneben das Mittel der Stichprobe; jede Person zählt einmal (ab 5 Personen).",
              "If you wish, the sample’s mean next to it; each person counts once (from 5 people).")],
        good_for=[("Eine persönliche Zusammenfassung nach einer Schleife über Themen",
                   "A personal summary after a loop over topics")],
        not_for=[("Befragungen ohne Schleife über Themen", "Surveys without a loop over topics")],
        designs=["within", "perception"],
        examples=[("Im Schnitt sehen Sie die Technologien riskanter als die anderen.",
                   "On average you see the technologies as riskier than the others.")],
    )
    min_sources, max_sources = 1, MAX_VARIABLES

    def default_texts(self, lang):
        return {"question": tr(lang, "Ihre Einschätzung über alle Themen", "Your assessment across all topics")}

    def form_sources(self, form):
        return form.getlist("sources") if hasattr(form, "getlist") else [form.get("sources", "")]

    def too_few_text(self):
        return de_en("bitte mindestens eine Variable wählen", "please choose at least one variable")

    def shown(self, item, lang, answers, preview=False):
        return self.profile(item, lang, answers, preview)

    def profile(self, item, lang, own_answers, preview=False):
        """{"rows": [{name, lo, hi, low, high, own, sample, own_text, sample_text}], "n", "svg"} or None."""
        from app.units import number

        srcs = self.sources(item)
        if not srcs:
            return None
        show_sample = item.config.get("show_sample", True)
        rows_ = [r for r, _ in self.topics(item, lang)]
        mine = self.own_view(own_answers)
        pool = self.everyone(item, preview) if show_sample else []

        def person_means(answers):  # per source: the mean over the topics this person rated (None if none)
            per = [[] for _ in srcs]
            for row in rows_:
                for k, v in enumerate(self.values(item, answers, row)):
                    if v is not None:
                        per[k].append(v)
            return [statistics.fmean(v) if v else None for v in per]

        own = person_means(mine)
        everyone = [person_means(a) for a in pool]
        out_rows, ns = [], []
        for k, src in enumerate(srcs):
            lo, hi, name, low, high = self.axis(src, lang, self.effective_subscale(item, src, k))
            means = [m[k] for m in everyone if m[k] is not None]
            ns.append(len(means))
            sample = statistics.fmean(means) if len(means) >= MIN_N else None
            out_rows.append({"name": name, "lo": lo, "hi": hi, "low": low, "high": high, "own": own[k], "sample": sample,
                             "own_text": number(own[k], 1, lang) if own[k] is not None else "",
                             "sample_text": number(sample, 1, lang) if sample is not None else ""})
        shown = any(r["sample"] is not None for r in out_rows)
        return {"rows": out_rows, "show_sample": show_sample, "sample_shown": shown, "n": max(ns) if shown else 0,
                "few": show_sample and not shown, "svg": _profile_svg(out_rows, lang)}


# --- drawings --------------------------------------------------------------------------------


def _map_svg(points, xa, ya, lang):
    """The topics on two axes: one's own filled with the name, the sample's hollow, a line between the two."""
    w, h, left, right, top, bottom = 560, 440, 58, 24, 16, 58
    (xlo, xhi, *_), (ylo, yhi, *_) = xa, ya

    def x(v):
        return left + (w - left - right) * (v - xlo) / ((xhi - xlo) or 1)

    def y(v):
        return h - bottom - (h - top - bottom) * (v - ylo) / ((yhi - ylo) or 1)

    parts = [f'<rect class="p-map-bg" x="{left}" y="{top}" width="{w - left - right}" height="{h - top - bottom}" rx="6"/>']
    for v in _ticks(xlo, xhi):
        parts.append(f'<line class="p-map-grid" x1="{x(v):.1f}" y1="{top}" x2="{x(v):.1f}" y2="{h - bottom}"/>'
                     f'<text class="p-map-num" x="{x(v):.1f}" y="{h - bottom + 16}" text-anchor="middle">{v:g}</text>')
    for v in _ticks(ylo, yhi):
        parts.append(f'<line class="p-map-grid" x1="{left}" y1="{y(v):.1f}" x2="{w - right}" y2="{y(v):.1f}"/>'
                     f'<text class="p-map-num" x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v:g}</text>')
    for p in points:  # first the lines, then the sample, then one's own on top
        if p["own"] and p["sample"]:
            parts.append(f'<line class="p-topic-link" x1="{x(p["sample"][0]):.1f}" y1="{y(p["sample"][1]):.1f}" '
                         f'x2="{x(p["own"][0]):.1f}" y2="{y(p["own"][1]):.1f}"/>')
    for p in points:
        if p["sample"]:
            label = "" if p["own"] else _label(x(p["sample"][0]), y(p["sample"][1]), p["name"], w, "p-topic-sample-label", 9)
            parts.append(f'<circle class="p-topic-sample" cx="{x(p["sample"][0]):.1f}" cy="{y(p["sample"][1]):.1f}" r="6">'
                         f'<title>{escape(p["name"])} · n = {p["n"]}</title></circle>{label}')
    for p in points:
        if p["own"]:
            parts.append(f'<circle class="p-topic-own" cx="{x(p["own"][0]):.1f}" cy="{y(p["own"][1]):.1f}" r="8">'
                         f'<title>{escape(p["name"])}</title></circle>'
                         + _label(x(p["own"][0]), y(p["own"][1]), p["name"], w, "p-topic-label", 12))
    parts.append(f'<text class="p-map-axislabel" x="{left + (w - left - right) / 2:.1f}" y="{h - 14}" text-anchor="middle">'
                 f'{escape(_short(xa[2]))}</text>')
    parts.append(f'<text class="p-map-axislabel" transform="translate(14 {top + (h - top - bottom) / 2:.1f}) rotate(-90)" '
                 f'text-anchor="middle">{escape(_short(ya[2]))}</text>')
    aria = tr(lang, "Karte der Themen auf zwei Bewertungen", "Map of the topics on two evaluations")
    return Markup(f'<svg class="p-map p-topic-map" viewBox="0 0 {w} {h}" role="img" aria-label="{aria}">' + "".join(parts) + "</svg>")


def _pair(xy, lang):
    from app.units import number

    return f"{number(xy[0], 1, lang)} / {number(xy[1], 1, lang)}" if xy else ""


def _label(cx, cy, name, w, cls, dx):
    anchor = "end" if cx > w - 150 else "start"
    return (f'<text class="{cls}" x="{cx + (-dx if anchor == "end" else dx):.1f}" y="{cy + 4:.1f}" '
            f'text-anchor="{anchor}">{escape(_short(name, 28))}</text>')


def _anchor(cx, lo, hi, room=40):
    """A label centred on its dot, flush with the dot near either end of the line so it is not cut off."""
    return "start" if cx - lo < room else "end" if hi - cx < room else "middle"


def _profile_svg(rows, lang):
    """One line per variable from its low to its high end: one's own mean as a filled dot, the sample's as a ring."""
    w, left, right, row_h, top = 560, 16, 16, 96, 8
    h = top + row_h * len(rows)
    parts = []
    for k, r in enumerate(rows):
        y0 = top + k * row_h
        line = y0 + 44

        def x(v, r=r):
            return left + (w - left - right) * (v - r["lo"]) / ((r["hi"] - r["lo"]) or 1)

        parts.append(f'<text class="p-profile-name" x="{left}" y="{y0 + 14}">{escape(_short(r["name"], 70))}</text>'
                     f'<line class="p-profile-axis" x1="{x(r["lo"]):.1f}" y1="{line}" x2="{x(r["hi"]):.1f}" y2="{line}"/>')
        for v in _ticks(r["lo"], r["hi"]):
            parts.append(f'<line class="p-profile-tick" x1="{x(v):.1f}" y1="{line - 4}" x2="{x(v):.1f}" y2="{line + 4}"/>')
        if r["low"] or r["high"]:
            parts.append(f'<text class="p-map-num" x="{x(r["lo"]):.1f}" y="{line + 40}">{escape(_short(r["low"], 30))}</text>'
                         f'<text class="p-map-num" x="{x(r["hi"]):.1f}" y="{line + 40}" text-anchor="end">{escape(_short(r["high"], 30))}</text>')
        if r["sample"] is not None:
            parts.append(f'<circle class="p-topic-sample" cx="{x(r["sample"]):.1f}" cy="{line}" r="9"/>'
                         f'<text class="p-topic-sample-label" x="{x(r["sample"]):.1f}" y="{line + 24}" '
                         f'text-anchor="{_anchor(x(r["sample"]), left, w - right)}">'
                         f'{escape(tr(lang, "Stichprobe", "sample"))} {r["sample_text"]}</text>')
        if r["own"] is not None:
            parts.append(f'<circle class="p-topic-own" cx="{x(r["own"]):.1f}" cy="{line}" r="8"/>'
                         f'<text class="p-topic-label" x="{x(r["own"]):.1f}" y="{line - 14}" '
                         f'text-anchor="{_anchor(x(r["own"]), left, w - right)}">{escape(tr(lang, "Sie", "you"))} {r["own_text"]}</text>')
    aria = tr(lang, "Ihre mittlere Einschätzung je Variable", "Your mean assessment per variable")
    return Markup(f'<svg class="p-profile" viewBox="0 0 {w} {h}" role="img" aria-label="{aria}">' + "".join(parts) + "</svg>")
