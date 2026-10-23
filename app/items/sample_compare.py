"""Live sample comparison ("Real-time Delphi", single round): shows how all participants so
far answered selected earlier items, with the participant's own answer highlighted.

Group values are shown only from MIN_N answers on (per item/statement), so no individual
answer can be inferred. Only completed real participations count; the preview also counts
test and dummy data. The number of answers each participant saw is exported (<code>_n_<source>)
so that analyses can account for the feedback."""
import re
import statistics

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en, english
from app.items.base import r_flag, r_int, r_list, r_one  # rules for survey files, see base.py
from app.items.base import PLACEHOLDER, ItemType, Unfit, plain, prepared, whole
from app.ui_i18n import tr
from app.units import fixed

MIN_N = 5
# the source of the percentile rank (mid-rank) and of the normal curve with the sample's M and SD, folded under the
# live feedback (audit 2026-10-05; the results page has its own fold)
CROCKER_ALGINA = "Crocker, L., & Algina, J. (1986). Introduction to classical and modern test theory. Holt, Rinehart and Winston."


def scale_score(src, answer, subscale=None):
    """The scale mean of a matrix source for one answer, with the rule of the results page (app/scales.py): reverse
    coding applied, without attention checks, over the statements shown (-77 is no answer), None if a shown
    statement is unanswered or fewer than two were shown. An instrument with subscales: over the chosen subscale
    (the first one if none is chosen), never over all of them (scales.feedback_codes)."""
    from app import scales

    return scales.answer_score(src, answer, subscale)


# a placeholder left as "…", with the quotes around it: „…“ reads as a gap, „“ would not
_QUOTED_GAP = re.compile(r"[„“”\"'‚‘’«»‹›]\s*…\s*[“”\"'‘’«»‹›]")


def label_text(text):
    """A question text as the label of a value of everyone (axis, profile row, bell curve): plain, a placeholder
    ({{anwendung}}, {{pid}}, an earlier answer) as its text for an empty answer, else as "…". The label stands for
    all passes of a loop and all participants, so no single value belongs in it."""
    def gap(m):
        return (m.group(2) or "").strip() or "…"

    return _QUOTED_GAP.sub("…", PLACEHOLDER.sub(gap, plain(text) or "")).strip()


def _subscale_rule(value):
    """A subscale chosen for a source in a survey file: a name, or None (the first one)."""
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 60:
        raise Unfit
    return value


# exactly one view per element (more views may follow; researchers add elements to combine them)
DISPLAYS = {"items": "Verteilung je Aussage (mit Median)",
            "bell": "Mittelwert als Glockenkurve (nur Matrixfragen)"}
DISPLAYS_EN = {"items": "Distribution per statement (with median)", "bell": "Mean as a bell curve (matrix questions only)"}
NUMERIC = ("likert", "semantic", "rating")  # usable as an axis (single item value or scale mean)
ORDINAL = ("likert", "semantic", "rating")
SUPPORTED = ORDINAL + ("single_choice",)


@register
class SampleCompare(ItemType):
    key = "sample_compare"
    rank_sources = (CROCKER_ALGINA,)  # percentile rank and normal curve (items/_live_source.html)
    # "sources" names questions of the file; transfer.create puts those of the new survey in their place
    import_rules = {"sources": r_list(2, r_int(1, 10 ** 12)), "subscales": r_list(2, _subscale_rule), "show_own": r_flag,
                    "display": r_one(*DISPLAYS), "lock_answers": r_flag}
    result_kind = None
    label = Both("Stichprobenvergleich", "Sample comparison")
    short = Both("Antworten der anderen zeigen", "Shows the others' answers")
    description = Both(f"Zeigt, wie alle bisherigen Teilnehmenden ausgewählte frühere Fragen beantwortet haben (ab {MIN_N} Antworten)",
                       f"Shows how all participants so far have answered chosen earlier questions (from {MIN_N} answers)")
    order = 55
    fact_sheet = FactSheet(
        "feedback",
        keywords=["Vergleich", "Comparison", "Rückmeldung", "Feedback", "Andere", "Others"],
        can=[("Verteilung je Aussage mit Median oder Mittelwert als Glockenkurve.",
              "Distribution per statement with median or mean as a bell curve."),
             ("Ab 5 Antworten.", "From 5 answers.")],
        good_for=[("Rückmeldung, wo man im Vergleich zu den anderen steht",
                   "Feedback on where one stands compared with the others"),
                  ("Real-Time-Delphi: die Antworten der anderen vor dem Überarbeiten",
                   "Real-time Delphi: the others’ answers before revising")],
        not_for=[("Ergebnisse, die die späteren Antworten nicht beeinflussen sollen",
                  "Results that must not influence later answers")],
        designs=["delphi", "perception"],
        examples=[("Delphi: So haben die bisherigen Expertinnen und Experten geschätzt.",
                   "Delphi: this is how the experts so far have estimated.")],
    )
    has_answer_mode = False  # stores only the group size that was shown
    category = "feedback"
    displays = DISPLAYS

    def display_names(self):
        """The views of the editor's select, labelled in the language of the page (sub-types have none: {})."""
        return {k: DISPLAYS_EN.get(k, v) for k, v in self.displays.items()} if english() else self.displays

    def default_config(self):
        # "sources" holds exactly one item id (a list for compatibility); display: one key of DISPLAYS
        return {"sources": [], "show_own": True, "display": "items", "lock_answers": True}

    def default_texts(self, lang):
        return {"question": tr(lang, "So haben andere Teilnehmende bisher geantwortet", "How other participants have answered so far")}

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        if structural:
            display = form.get("display") if form.get("display") in self.displays else "items"
            ids, subs = self.read_sources([form.get("source", ""), form.get("source_y", "")])
            item.config = {**item.config, "sources": ids, "subscales": subs,
                           "show_own": bool(form.get("show_own")), "display": display}

    # --- sources -----------------------------------------------------------------
    # An instrument with subscales (BFI-10, IE-4, ...) is offered once per subscale: the form value of a source is
    # "<item id>:<subscale>", else "<item id>". config["subscales"] holds the chosen subscale per source (None: the
    # first one); a mean over all subscales would mix what they measure.

    @staticmethod
    def read_sources(values):
        """(item ids, subscales) from the form values of the source fields; empty fields are left out."""
        ids, subs = [], []
        for value in values:
            head, _, sub = (value or "").partition(":")
            if whole(head) is not None:
                ids.append(whole(head))
                subs.append(sub.strip()[:60] or None)
        return ids, subs

    @staticmethod
    def source_choices(src):
        """[(form value, subscale or None)] under which a candidate is offered in the editor."""
        from app import scales

        return [(f"{src.id}:{name}", name) for name in scales.subscale_names(src)] or [(str(src.id), None)]

    def subscale(self, item, index=0):
        """The subscale chosen for the source at `index` (None: none chosen, the first one counts)."""
        subs = (item.config or {}).get("subscales") or []
        return subs[index] if index < len(subs) and isinstance(subs[index], str) else None

    def effective_subscale(self, item, src, index=0):
        """The subscale the feedback uses for this source: the chosen one, else the first; None without."""
        from app import scales

        names = scales.subscale_names(src) if src is not None else []
        if not names:
            return None
        sub = self.subscale(item, index)
        return sub if sub in names else names[0]

    def chosen_value(self, item, index=0):
        """The form value of the source at `index` as the editor preselects it: with the subscale that counts."""
        ids = (item.config or {}).get("sources") or []
        if index >= len(ids):
            return ""
        src = next((s for s in self.sources(item) if s.id == ids[index]), None)
        sub = self.effective_subscale(item, src, index)
        return f"{ids[index]}:{sub}" if sub else str(ids[index])

    def candidates(self, item):
        """Items that may be shown: supported types before this element; items of a loop block only
        from within the same block (the comparison then uses the answers of the same pass)."""
        from app import loops

        out = []
        for block in item.survey.blocks:
            for other in block.items:
                if other.id == item.id:
                    return out
                if other.type in SUPPORTED and (not loops.active(block) or block.id == item.block_id):
                    out.append(other)
        return out

    @staticmethod
    def in_loop(item, src):
        from app import loops

        return loops.active(src.block) and src.block_id == item.block_id

    def pool_key(self, item, src, row):
        """Where others' answers to `src` are stored: per pass inside a loop."""
        return f"{src.id}#{row}" if row and self.in_loop(item, src) else str(src.id)

    def source_label(self, src, lang, sub=None):
        """What the bell curve is about, for participants: the name of the construct the source was made from, else
        the instruction of the source question, else (last resort) its variable name; with the subscale it is about.
        A construct's instruction ("Die folgenden Aussagen können mehr oder weniger auf Sie zutreffen …") read as a
        misplaced question above the curve (pre-test 2026-10-20), its name says what is compared."""
        from app import constructs

        info = constructs.info(src)
        text = constructs.SEEDS_EN.get(info["key"], {}).get("name", info["label"]) if info and not lang.startswith("de")             else info["label"] if info else label_text(src.text("question", lang)) or src.code
        return f"{text} ({sub})" if sub else text

    def sources(self, item):
        by_id = {i.id: i for b in item.survey.blocks for i in b.items}
        return [by_id[s] for s in item.config.get("sources", []) if s in by_id]

    def validate(self, item):
        problems = []
        if not item.config.get("sources"):
            problems.append(de_en("keine Frage für den Vergleich ausgewählt", "no question chosen for the comparison"))
        src = next(iter(self.sources(item)), None)
        if src is not None and item.config.get("display") == "bell" and not self.is_matrix(src):
            problems.append(de_en("Glockenkurve nur für Matrixfragen (mehrere Aussagen oder Gegensatzpaare)",
                                  "bell curve only for matrix questions (several statements or pairs of opposites)"))

        allowed = {i.id for i in self.candidates(item)}
        late = [s.code for s in self.sources(item) if s.id not in allowed]
        if late:
            problems.append(de_en("Vergleich zeigt Fragen, die erst danach kommen oder in einer anderen Schleife stehen: {codes}",
                                  "the comparison shows questions that only come later or are inside another loop: {codes}",
                                  codes=", ".join(late)))
        missing = len(item.config.get("sources", [])) - len(self.sources(item))
        if missing:
            problems.append(de_en("{n} ausgewählte Frage(n) gibt es nicht mehr", "{n} chosen question(s) no longer exist", n=missing))
        return problems

    # --- group values ------------------------------------------------------------

    def pool(self, item, preview=False, row=None):
        """Answers of completed participations (real; in the preview also test and dummy), keyed by
        item id; inside a loop the answers of pass `row` (the same table row) stand under the id."""
        return self.pool_of(item, self.answer_lists(item, preview, row))

    def answer_lists(self, item, preview=False, row=None):
        """Per source the list of balance.answers: per shown question only its answers, read without the rest of the
        participations and kept for a few seconds (app/balance.py): every page view used to load all participations
        with all their answers."""
        from app import balance

        return [balance.answers(item.survey, self.pool_key(item, src, row), preview) for src in self.sources(item)]

    def pool_of(self, item, lists):
        """pool() from the lists of answer_lists, joined by participation id: each list is read and kept on its own,
        so one can hold a participation completed in between, and not always at the end (ids follow the start, not
        the completion; audit 2026-10-12, M-5: joined by place, x of one person met y of another). Only the
        participations in every list, in the order of the first."""
        srcs = list(self.sources(item))
        lists = list(lists)
        if not srcs or not lists:
            return []
        common = set.intersection(*({rid for rid, _ in given} for given in lists))
        by_rid = [{rid: answer for rid, answer in given} for given in lists]
        return [{str(src.id): found[rid] for src, found in zip(srcs, by_rid)}
                for rid, _ in lists[0] if rid in common]

    @staticmethod
    def is_matrix(src):
        c = src.config or {}
        return (src.type == "likert" and len(c.get("statements", [])) > 1) or \
            (src.type == "semantic" and len(c.get("pairs", [])) > 1)

    def bell(self, item, lang, own_answers, preview=False, row=None):
        """Distribution of per-person scale means (reverse coding applied) for a matrix source:
        {"n", "enough", "mean", "sd", "own", "below_share", "points", "labels", "svg"} or None."""
        src = next(iter(self.sources(item)), None)
        if src is None or not self.is_matrix(src):
            return None
        kind = src.kind

        sub = self.effective_subscale(item, src)
        means = [m for m in (scale_score(src, a.get(str(src.id)), sub) for a in self.pool(item, preview, row)) if m is not None]
        own = scale_score(src, own_answers.get(src.id), sub) if own_answers and own_answers.get(src.id) else None
        points = src.config["points"]
        if src.type == "likert":
            labels = kind.labels(src, lang)
            ends = (labels[0], labels[-1])
        else:
            ends = ("", "")  # pairs differ; the axis shows numbers only
        out = {"n": len(means), "enough": len(means) >= MIN_N, "points": points, "ends": ends, "own": own,
               "source": src, "label": self.source_label(src, lang, sub)}
        if not out["enough"]:
            return out
        mean = statistics.fmean(means)
        sd = statistics.stdev(means)  # of the sample (n - 1), as on the results page; MIN_N >= 2
        comma = tr(lang, ",", ".")
        out.update(mean=mean, sd=sd, mean_text=fixed(mean, 2).replace(".", comma), sd_text=fixed(sd, 2).replace(".", comma),
                   below_share=(sum(1 for m in means if m < own) / len(means)) if own is not None else None)
        # the curve is the normal distribution with this M and SD, not the observed one (the caption says so);
        # with SD 0 (everyone alike) a narrow curve stands for the single value
        out["svg"] = _bell_svg(mean, sd or 0.25, own if item.config.get("show_own", True) else None, points, lang)
        return out

    # --- 2D map ------------------------------------------------------------------

    def value(self, src, answer, sub=None):
        """One number per person: the scale mean for matrix items (scale_score; of the subscale `sub`, the first
        one if None), the value for single items and ratings, reverse coding applied as in the export; None if
        missing."""
        if src.type == "rating":
            if not isinstance(answer, (int, float)) or isinstance(answer, bool):
                return None
            value = src.kind.export_values(src, answer).get(src.code)
            return float(value) if isinstance(value, (int, float)) else None
        return scale_score(src, answer, sub)

    def values_of(self, src, answers, sub=None):
        """[value(src, answer, sub) for answer in answers], with the statements and their missing codes looked up once
        for all persons instead of once per person (performance audit 2026-10-09, N-5)."""
        if src.type == "rating":
            return [self.value(src, answer, sub) for answer in answers]
        from app import scales

        codes = scales.feedback_codes(src, sub)
        if not codes:
            return [None] * len(answers)
        hidden = scales._missing(src, codes)
        return [scales.row_score(src, codes, src.kind.export_values(src, answer), hidden=hidden)
                if isinstance(answer, dict) else None for answer in answers]

    def axis(self, src, lang, sub=None):
        """(low, high, name, low label, high label) of a numeric source; sub: the subscale it shows."""
        if src.type == "rating":
            lo, hi = src.kind.bounds(src)
            t = src.t(lang)
            ends = (t.get("right", ""), t.get("left", "")) if src.config.get("reverse") else (t.get("left", ""), t.get("right", ""))
            return float(lo), float(hi), label_text(src.text("question", lang)), *ends  # value() is recoded, so the ends swap
        points = src.config["points"]
        construct = (src.config or {}).get("construct")
        name = src.code
        if construct:
            from app import constructs

            info = constructs.info(src)
            name = info["label"] if info else src.code
        elif src.type == "likert" and len(src.config.get("statements", [])) == 1:
            name = label_text(src.kind.statements(src, lang)[0]["text"]) or src.code
        else:
            name = label_text(src.text("question", lang)) or src.code
        if sub:
            name = f"{name} ({sub})"
        if src.type == "likert":
            labels = src.kind.labels(src, lang)
            stmts = src.config.get("statements", [])
            flipped = len(stmts) == 1 and stmts[0].get("reverse")  # value() is recoded, so the ends swap
            return 1.0, float(points), name, *((labels[-1], labels[0]) if flipped else (labels[0], labels[-1]))
        pairs = src.kind.pairs(src, lang)
        if len(pairs) != 1:
            return 1.0, float(points), name, "", ""
        p = pairs[0]
        return 1.0, float(points), name, *((p["right"], p["left"]) if p.get("reverse") else (p["left"], p["right"]))

    def scatter(self, item, lang, own_answers, preview=False, row=None):
        srcs = self.sources(item)
        if len(srcs) != 2 or any(s.type not in NUMERIC for s in srcs):
            return None
        sx, sy = srcs
        bx, by = self.effective_subscale(item, sx, 0), self.effective_subscale(item, sy, 1)
        pairs = []
        for a in self.pool(item, preview, row):
            vx, vy = self.value(sx, a.get(str(sx.id)), bx), self.value(sy, a.get(str(sy.id)), by)
            if vx is not None and vy is not None:
                pairs.append((vx, vy))
        own = None
        if own_answers:
            ox, oy = self.value(sx, own_answers.get(sx.id), bx), self.value(sy, own_answers.get(sy.id), by)
            own = (ox, oy) if ox is not None and oy is not None else None
        out = {"n": len(pairs), "enough": len(pairs) >= MIN_N, "sources": srcs,
               "x": self.axis(sx, lang, bx), "y": self.axis(sy, lang, by), "own": own}
        if out["enough"]:
            out["svg"] = _scatter_svg(pairs, own if item.config.get("show_own", True) else None, out["x"], out["y"], lang)
            # the own point in words with the average lines, not only as a coloured dot (accessibility D8)
            if own is not None:
                from app.units import number

                mx, my = statistics.fmean(p[0] for p in pairs), statistics.fmean(p[1] for p in pairs)
                out["own_text"] = tr(lang, "waagerecht {ox} (Durchschnitt {mx}), senkrecht {oy} (Durchschnitt {my})",
                                     "horizontal {ox} (average {mx}), vertical {oy} (average {my})",
                                     ox=number(own[0], 1, lang), mx=number(mx, 1, lang), oy=number(own[1], 1, lang), my=number(my, 1, lang))
        return out

    def rows(self, item, lang, own_answers, preview=False, row=None):
        """One row per statement/pair/item: label, categories, counts, n, median, own, enough."""
        from app import scales

        pool = self.pool(item, preview, row)
        out = []
        for index, src in enumerate(self.sources(item)):
            answers = [a.get(str(src.id)) for a in pool]
            own = own_answers.get(src.id) if own_answers else None
            kind = src.kind
            # a subscale chosen in the editor: its statements only (older elements without a choice show all)
            sub = self.subscale(item, index) if src.type in ("likert", "semantic") else None
            keep = set(dict(scales.subscale_groups(src)).get(sub) or []) if sub else set()
            if src.type == "likert":
                labels = kind.labels(src, lang)
                checks = kind.checks(src)
                for st in kind.statements(src, lang):
                    if st["id"] in checks or (keep and st["export_code"] not in keep):
                        continue  # an attention check is no statement of the scale
                    values = [a.get(st["id"]) for a in answers if isinstance(a, dict)]
                    out.append(self._ordinal(st["text"], labels, values, (own or {}).get(st["id"]), src))
            elif src.type == "semantic":
                points = src.config["points"]
                for p in kind.pairs(src, lang):
                    if keep and p["export_code"] not in keep:
                        continue
                    values = [a.get(p["id"]) for a in answers if isinstance(a, dict)]
                    labels = [p["left"]] + [""] * (points - 2) + [p["right"]]
                    out.append(self._ordinal(f"{p['left']} – {p['right']}", labels, values, (own or {}).get(p["id"]), src))
            elif src.type == "rating":
                lo, hi = kind.bounds(src)
                values = [a for a in answers if isinstance(a, (int, float))]
                if hi - lo <= 10 and kind.is_integer(src):
                    cats = list(range(int(lo), int(hi) + 1))
                    labels = [str(c) for c in cats]
                    row = self._ordinal(plain(src.text("question", lang)), labels, [v - lo + 1 for v in values],
                                        own - lo + 1 if isinstance(own, (int, float)) else None, src)
                else:  # continuous slider: 10 equal bins
                    from app.units import class_of, decimals, number

                    width = (hi - lo) / 10 or 1
                    places = min(max(decimals(src.config.get("step") or 1), decimals(lo), decimals(width)), 4)
                    labels = [number(lo + i * width, places, lang) for i in range(10)]

                    def bin_of(v):
                        # 0.3 / 0.1 is 2.9999999999999996 in binary: a value on a class edge belongs to the upper class
                        return class_of(v, lo, width, 10) + 1

                    binned = [bin_of(v) for v in values]
                    own_bin = bin_of(own) if isinstance(own, (int, float)) else None
                    row = self._ordinal(plain(src.text("question", lang)), labels, binned, own_bin, src)
                    row["median_label"] = number(statistics.median(values), places + 1, lang) if values else ""
                out.append(row)
            elif src.type == "single_choice":
                out.append(self._choice(src, lang, answers, own))
        for row in out:
            total = max(row["n"], 1)
            row["shares"] = [c / total for c in row["counts"]]
            row["max_share"] = max(row["shares"] + [0.0001])
        return out

    def _choice(self, src, lang, answers, own):
        """Single choice: shares among the answers with content; "weiß nicht"/"keine Angabe" (options marked as
        missing) are counted apart, as on the results page. Nominal questions list the most frequent first, the
        residual answers last (results.by_count); ordinal ones keep the order of the question."""
        from app import results

        kind = src.kind
        own = kind.chosen(own)
        values = [kind.chosen(a) for a in answers if a not in (None, "")]
        opts = kind.options(src, lang)
        gaps = [o for o in opts if o.get("missing")]
        rows = [{"code": o["code"], "label": o["label"], "count": sum(1 for v in values if v == o["code"]),
                 "rest": bool(o.get("text"))} for o in opts if not o.get("missing")]
        if kind.level(src) != "ordinal":
            rows = results.by_count(rows)
        n = sum(r["count"] for r in rows)
        return {"label": plain(src.text("question", lang)), "nominal": True, "labels": [r["label"] for r in rows],
                "counts": [r["count"] for r in rows], "n": n, "enough": n >= MIN_N,
                "missing": [{"label": o["label"], "count": sum(1 for v in values if v == o["code"])} for o in gaps],
                "own": next((i for i, r in enumerate(rows) if r["code"] == own), None), "source": src}

    def _ordinal(self, label, labels, values, own, src):
        values = [int(v) for v in values if isinstance(v, (int, float)) and 1 <= v <= len(labels)]
        counts = [values.count(k) for k in range(1, len(labels) + 1)]
        median = statistics.median(values) if values else None
        # like the results page: a median between two levels names both ("2–3"), not their mean
        lo, hi = (statistics.median_low(values), statistics.median_high(values)) if values else (None, None)
        return {"label": label, "nominal": False, "labels": labels, "counts": counts, "n": len(values),
                "enough": len(values) >= MIN_N, "median": median,
                "median_label": ("" if median is None else str(lo) if lo == hi else f"{lo}–{hi}"),
                "own": int(own) - 1 if isinstance(own, (int, float)) and 1 <= own <= len(labels) else None,
                "source": src}

    # --- answer: the group sizes that were shown -----------------------------------

    def parse_answer(self, item, form, lang="de", meta=None):
        shown = {}
        for src in self.sources(item):
            # a matrix sends one value per statement: the largest group shown (0 only if none was)
            name = f"i{item.id}_n_{src.id}"
            sent = form.getlist(name) if hasattr(form, "getlist") else [form.get(name, "")]
            sizes = [n for n in (whole(v) for v in sent) if n is not None]  # a count: nine digits at most
            if sizes:
                shown[str(src.id)] = max(sizes)
        if not shown:
            return None
        from flask import g, has_request_context

        response = g.get("participation") if has_request_context() else None
        if response is None:
            return shown  # the dummy runs of the researchers: as the page sent it
        # a participation: the hidden fields only say which comparisons the page showed; the group sizes are worked
        # out here again (of the same pass in a loop), a browser can send any (code review 2026-10-08, as N7)
        sizes = self.shown_sizes(item, lang, preview=bool(getattr(response, "is_preview", False)), row=(meta or {}).get("row"))
        return {sid: sizes.get(sid, 0) for sid in shown}

    def shown_sizes(self, item, lang, preview=False, row=None):
        """{source id: the largest group shown for it, 0 if none had MIN_N answers}, as the page shows them."""
        sizes = {}
        if item.config.get("display") == "bell":
            bell = self.bell(item, lang, {}, preview=preview, row=row)
            if bell:
                sizes[str(bell["source"].id)] = bell["n"] if bell["enough"] else 0
            return sizes
        for r in self.rows(item, lang, {}, preview=preview, row=row):
            sid = str(r["source"].id)
            sizes[sid] = max(sizes.get(sid, 0), r["n"] if r["enough"] else 0)
        return sizes

    def is_missing(self, item, answer):
        return False

    def export_columns(self, item):
        return [(f"{item.code}_n_{s.code}", f"Anzahl Antworten im Stichprobenvergleich zu {s.code} "
                 f"(0 = unter {MIN_N}, nicht gezeigt)") for s in self.sources(item)]

    def export_values(self, item, answer):
        # the source questions once per export, not per participation (code review 2026-10-07, R4)
        got = answer or {}
        return {column: got.get(sid) for column, sid in prepared(item, "compare_columns", lambda: [
            (f"{item.code}_n_{s.code}", str(s.id)) for s in self.sources(item)])}


def _bell_svg(mean, sd, own, points, lang):
    """Normal curve over the scale range with the group mean and (optionally) the own value."""
    import math

    from markupsafe import Markup

    w, h, left, right, top, base = 520, 150, 16, 16, 18, 112
    lo, hi = 1, points

    def x(v):
        return left + (w - left - right) * (v - lo) / (hi - lo)

    def pdf(v):
        return math.exp(-0.5 * ((v - mean) / sd) ** 2)

    steps = 80
    pts = [(x(lo + (hi - lo) * i / steps), base - (base - top) * pdf(lo + (hi - lo) * i / steps)) for i in range(steps + 1)]
    path = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    area = path + f" L{x(hi):.1f},{base} L{x(lo):.1f},{base} Z"
    parts = [f'<path class="p-bell-area" d="{area}"/>', f'<path class="p-bell-line" d="{path}"/>',
             f'<line class="p-bell-axis" x1="{x(lo)}" y1="{base}" x2="{x(hi)}" y2="{base}"/>']
    for v in range(lo, hi + 1):
        parts.append(f'<line class="p-bell-tick" x1="{x(v):.1f}" y1="{base}" x2="{x(v):.1f}" y2="{base + 5}"/>'
                     f'<text class="p-bell-num" x="{x(v):.1f}" y="{base + 18}" text-anchor="middle">{v}</text>')
    mean_label = (tr(lang, "Mittelwert ", "mean ")) + fixed(mean, 1).replace(".", tr(lang, ",", "."))
    # the two labels go to opposite sides of their lines, the own one on the side away from the mean: centred above
    # the mean they lay over each other when both values were close (pre-test 2026-10-20)
    mx, mean_anchor, mean_dx, label_w = x(mean), "middle", 0, 100
    own_anchor, own_dx, own_dy = "start", 10, -8
    if own is not None:
        own_anchor, own_dx = ("end", -10) if own <= mean else ("start", 10)
        mean_anchor, mean_dx = ("start", 6) if own <= mean else ("end", -6)
        if (mean_anchor == "start" and mx + 6 + label_w > w) or (mean_anchor == "end" and mx - 6 - label_w < 0):
            mean_anchor, mean_dx = ("end", -6) if mean_anchor == "start" else ("start", 6)
            own_dy = 22  # the mean label took the own label's side: the own one goes under its dot
        if (own_anchor == "start" and x(own) + 10 + label_w > w) or (own_anchor == "end" and x(own) - 10 - label_w < 0):
            own_anchor, own_dx = ("end", -10) if own_anchor == "start" else ("start", 10)
            own_dy = 22  # no room on its side: under its dot, clear of the mean label
    parts.append(f'<line class="p-bell-mean" x1="{mx:.1f}" y1="{top - 6}" x2="{mx:.1f}" y2="{base}"/>'
                 f'<text class="p-bell-meanlabel" x="{mx + mean_dx:.1f}" y="{top - 8}" text-anchor="{mean_anchor}">{mean_label}</text>')
    if own is not None:
        oy = base - (base - top) * pdf(own)
        own_label = (tr(lang, "Sie: ", "you: ")) + fixed(own, 1).replace(".", tr(lang, ",", "."))
        anchor, dx = own_anchor, own_dx
        parts.append(f'<line class="p-bell-own" x1="{x(own):.1f}" y1="{oy:.1f}" x2="{x(own):.1f}" y2="{base}"/>'
                     f'<circle class="p-bell-dot" cx="{x(own):.1f}" cy="{oy:.1f}" r="6"/>'
                     f'<text class="p-bell-ownlabel" x="{x(own) + dx:.1f}" y="{oy + own_dy:.1f}" text-anchor="{anchor}">{own_label}</text>')
    aria = (tr(lang, "Verteilung der Mittelwerte als Glockenkurve", "Distribution of means as a bell curve"))
    return Markup(f'<svg class="p-bell" viewBox="0 0 {w} {h}" role="img" aria-label="{aria}">' + "".join(parts) + "</svg>")


def _scatter_svg(pairs, own, xa, ya, lang):
    """Bubbles at rounded positions (size = number of people), group mean crosshair, own point."""
    from collections import Counter

    from markupsafe import Markup, escape

    w, h, left, right, top, bottom = 520, 400, 58, 18, 16, 58
    (xlo, xhi, *_), (ylo, yhi, *_) = xa, ya

    def x(v):
        return left + (w - left - right) * (v - xlo) / ((xhi - xlo) or 1)

    def y(v):
        return h - bottom - (h - top - bottom) * (v - ylo) / ((yhi - ylo) or 1)

    step_x = 0.25 if (xhi - xlo) <= 10 else (xhi - xlo) / 20
    step_y = 0.25 if (yhi - ylo) <= 10 else (yhi - ylo) / 20
    cells = Counter((round(px / step_x) * step_x, round(py / step_y) * step_y) for px, py in pairs)
    biggest = max(cells.values())
    parts = [f'<rect class="p-map-bg" x="{left}" y="{top}" width="{w - left - right}" height="{h - top - bottom}" rx="6"/>']
    for axis_v, is_x in ((v, True) for v in _ticks(xlo, xhi)):
        parts.append(f'<line class="p-map-grid" x1="{x(axis_v):.1f}" y1="{top}" x2="{x(axis_v):.1f}" y2="{h - bottom}"/>'
                     f'<text class="p-map-num" x="{x(axis_v):.1f}" y="{h - bottom + 16}" text-anchor="middle">{axis_v:g}</text>')
    for axis_v in _ticks(ylo, yhi):
        parts.append(f'<line class="p-map-grid" x1="{left}" y1="{y(axis_v):.1f}" x2="{w - right}" y2="{y(axis_v):.1f}"/>'
                     f'<text class="p-map-num" x="{left - 8}" y="{y(axis_v) + 4:.1f}" text-anchor="end">{axis_v:g}</text>')
    mx = sum(p[0] for p in pairs) / len(pairs)
    my = sum(p[1] for p in pairs) / len(pairs)
    parts.append(f'<line class="p-map-mean" x1="{x(mx):.1f}" y1="{top}" x2="{x(mx):.1f}" y2="{h - bottom}"/>'
                 f'<line class="p-map-mean" x1="{left}" y1="{y(my):.1f}" x2="{w - right}" y2="{y(my):.1f}"/>')
    for (cx, cy), n in sorted(cells.items(), key=lambda c: -c[1]):
        r = (4 + 10 * (n / biggest) ** 0.5) * 2 / 3  # the others small, so the own point stands out
        parts.append(f'<circle class="p-map-dot" cx="{x(cx):.1f}" cy="{y(cy):.1f}" r="{r:.1f}"><title>{n}</title></circle>')
    if own is not None:
        label = tr(lang, "Sie", "you")
        anchor = "end" if x(own[0]) > w - 90 else "start"
        dx = -20 if anchor == "end" else 20  # beside the own point (r 14)
        parts.append(f'<circle class="p-map-own" cx="{x(own[0]):.1f}" cy="{y(own[1]):.1f}" r="14"/>'
                     f'<text class="p-map-ownlabel" x="{x(own[0]) + dx:.1f}" y="{y(own[1]) + 4:.1f}" text-anchor="{anchor}">{label}</text>')
    parts.append(f'<text class="p-map-axislabel" x="{left + (w - left - right) / 2:.1f}" y="{h - 14}" text-anchor="middle">'
                 f'{escape(_short(xa[2]))}</text>')
    parts.append(f'<text class="p-map-axislabel" transform="translate(14 {top + (h - top - bottom) / 2:.1f}) rotate(-90)" '
                 f'text-anchor="middle">{escape(_short(ya[2]))}</text>')
    aria = tr(lang, "Karte: zwei Variablen, jede Blase steht für Teilnehmende", "Map of two variables")
    return Markup(f'<svg class="p-map" viewBox="0 0 {w} {h}" role="img" aria-label="{aria}">' + "".join(parts) + "</svg>")


def _ticks(lo, hi):
    span = hi - lo
    step = 1 if span <= 10 else 10 if span <= 100 else round(span / 10)
    v, out = lo, []
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += step
    return out


def _short(text, n=60):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"
