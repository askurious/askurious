"""Ranking: participants put 2 or more options in order (drag, or ↑/↓ for touch and keyboard).

The starting order is random by default (fixed once per response, see runtime.build_layout),
so it does not bias the answer. Because the starting order is itself a complete order, an
answer only counts once the participant has moved something or confirmed "Reihenfolge passt so";
that makes "Pflicht" and "Hinweis" meaningful. Export: one column per option with its rank."""
from app import rows
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.choice import CARRY_RULES, LAYOUT_RULE, OPTION_RULES
from app.items.base import r_flag  # rules for survey files, see base.py
from app.items.base import AnswerError, in_lang
from app.items.choice import _Choice
from app.lang import Both
from app.ui_i18n import tr


@register
class Ranking(_Choice):
    key = "ranking"
    closed_lists = ("options",)  # one more option changes the number of ranks (audit 2026-10-12, M-7)
    code_answer = False  # not a code to compare with: a condition only asks "answered or not" (X17)
    import_rules = {"options": OPTION_RULES, "layout": LAYOUT_RULE, "randomize": r_flag, **CARRY_RULES}

    def missing_codes(self, item, column):
        return (rows.NOT_SHOWN,) if column in self.partial_columns(item) else ()
    label = Both("Rangfolge", "Ranking")
    codes_in_columns = True
    short = Both("Optionen in eine Reihenfolge bringen", "Put options in an order")
    description = Both("Zwei oder mehr Optionen in eine Reihenfolge bringen (ziehen oder mit Pfeilen)",
                       "Put two or more options in an order (drag or use arrows)")
    order = 25
    fact_sheet = FactSheet(
        "classic",
        keywords=["Ranking", "Rangordnung", "Reihenfolge", "Order", "Priorisieren", "Prioritise"],
        can=[("Optionen ziehen oder mit Pfeilen ordnen.", "Order the options by dragging or with arrows.")],
        good_for=[("Prioritäten zwischen wenigen Optionen (bis etwa 7)",
                   "Priorities among a few options (up to about 7)")],
        not_for=[("Viele Optionen (dann MaxDiff)", "Many options (then MaxDiff)"),
                 ("Wie groß die Abstände zwischen den Optionen sind (dann Konstantsumme)",
                  "How large the gaps between the options are (then constant sum)")],
        designs=["cross", "perception"],
        examples=[("Bringen Sie diese Ziele der Verkehrspolitik in eine Reihenfolge.",
                   "Put these goals of transport policy in an order.")],
    )
    levels = ("ordinal", "metric")  # ranks; metric shows mean ranks

    def default_config(self):
        return {**super().default_config(), "randomize": True}

    def _update_extra_config(self, config, form):
        config["randomize"] = bool(form.get("randomize"))
        config.pop("layout", None)

    def parse_answer(self, item, form, lang="de", meta=None, answers=None):
        codes = [o["code"] for o in self.visible(item, answers)]  # carried options: the ones this person sees
        order = list(dict.fromkeys(c for c in form.getlist(f"i{item.id}") if c in codes))
        if not form.get(f"i{item.id}_touched") or sorted(order) != sorted(codes):
            if item.required:
                raise AnswerError(tr(lang, "Bitte bringen Sie die Elemente in eine Reihenfolge (oder bestätigen Sie sie).", "Please put the items in order (or confirm the order)."))
            return None
        return order

    def soft_warning(self, item, answer, lang):
        """The given start order, neither moved nor confirmed: asked once instead of losing it as missing (an
        optional question; a required one says so as an error; UI/UX audit 2026-10-05, V37)."""
        if answer is None and not item.required:
            return tr(lang, "Die Reihenfolge ist noch nicht bestätigt. Wenn sie so passt, wählen Sie „Reihenfolge passt so“. "
                            "Mit „Weiter“ geht es ohne Antwort weiter.",
                      "The order has not been confirmed yet. If it is right, choose “This order is fine”. "
                      "“Next” continues without an answer.")
        return None

    def display_value(self, item, form, meta=None):
        codes = [o["code"] for o in item.config.get("options", [])]
        order = list(dict.fromkeys(c for c in form.getlist(f"i{item.id}") if c in codes))
        return order if order and form.get(f"i{item.id}_touched") else None

    def ordered_options(self, item, lang, answer=None, start_order=None, answers=None):
        """Options in the order to show: the given answer, else the (random) start order. answers: see options."""
        opts = self.options(item, lang, answers)
        if isinstance(answer, list) and answer and {o["code"] for o in opts} == set(answer):
            rank = {code: n for n, code in enumerate(answer)}
            return sorted(opts, key=lambda o: rank.get(o["code"], len(rank)))
        if start_order:
            rank = {oid: n for n, oid in enumerate(start_order)}
            return sorted(opts, key=lambda o: rank.get(o["id"], len(rank)))
        return opts

    def own_order(self, item, answer, answers=None):
        """The stored order if it names the options this person sees, else None (not answered)."""
        codes = {o["code"] for o in self.visible(item, answers)}
        return answer if isinstance(answer, list) and answer and set(answer) == codes else None

    # the start order is a complete order: whether it was kept as it was, its own column (UI/UX audit 2026-10-16, 105)
    kept_start_label = "Startreihenfolge unverändert bestätigt (1 = ja, 0 = geändert)"

    def kept_start(self, item, answer, order):
        if not isinstance(answer, list) or not answer:
            return None
        code_of = {o["id"]: o["code"] for o in item.config.get("options", [])}
        seen = set(answer)
        start = ([code_of[i] for i in order if code_of.get(i) in seen] if order
                 else [o["code"] for o in item.config.get("options", []) if o["code"] in seen])
        return int(answer == start)

    def order_codes(self, item):
        if not item.config.get("randomize", True):  # the start order; keeping it is a valid answer
            return None
        return {o["id"]: f"{item.code}_{o['code']}" for o in item.config.get("options", [])}

    # piping: what stands on a rank (<code>_rank1 = the label of the option put first); the data set holds it
    # the other way round, a rank per option
    def pipeable(self, item):
        return False

    def pipe_parts(self, item, lang):
        return [(f"{item.code}_rank{n}", f"Platz {n}") for n in range(1, len(item.config.get("options", [])) + 1)]

    def pipe_part_text(self, item, answer, name, lang):
        from app.items.base import plain

        prefix = f"{item.code}_rank"
        if not name.startswith(prefix) or not name[len(prefix):].isdigit() or not isinstance(answer, list):
            return ""
        n = int(name[len(prefix):])
        labels = item.text("options", lang) or {}
        chosen = next((o for o in item.config.get("options", []) if 1 <= n <= len(answer) and o["code"] == answer[n - 1]), None)
        return (plain(labels.get(chosen["id"], "")) or chosen["code"]) if chosen else ""

    def export_columns(self, item):
        labels = item.text("options") or {}
        return [(f"{item.code}_{o['code']}", f"Rang von „{labels.get(o['id'], o['code'])}“ (1 = oben)")
                for o in item.config.get("options", [])]

    def codebook_info(self, item, column, lang, ui=None):
        n = len(item.config.get("options", []))
        notes = [in_lang(ui, "1 = oben", "1 = top")] + ([in_lang(
            ui, "zufällige Startreihenfolge, siehe Spalte {col} (in einer Schleife mit der Nummer des Durchgangs am Ende)",
            "random starting order, see column {col} (in a loop with the number of the pass at the end)",
            col=f"{item.code}_order")] if self.order_codes(item) else [])
        return "Rang", in_lang(ui, "1 bis {n}", "1 to {n}", n=n), notes + self._carry_note(item, column, ui)

    def normalized(self, item, table, passes=()):
        """Ranks made comparable when people rank different numbers of options (carried options, R-6): per
        option the values (k - r) / (k - 1) of everyone who ranked it among k >= 2 options (1 = top, 0 = bottom),
        and how many put it first. {code: ([values], top count)}; in a loop every pass counts."""
        from app import results

        codes = [o["code"] for o in item.config.get("options", [])]
        out = {c: ([], 0) for c in codes}
        for row in table:
            for s in [f"_{n}" for n in passes] or [""]:
                ranks = {}
                for c in codes:
                    r = results.valid_number(row.get(f"{item.code}_{c}{s}"), (str(rows.NOT_SHOWN),))
                    if r is not None and r >= 1:
                        ranks[c] = r
                k = len(ranks)
                if k < 2:
                    continue
                for c, r in ranks.items():
                    values, top = out[c]
                    values.append((k - r) / (k - 1))
                    out[c] = (values, top + (r == 1))
        return out

    def results(self, item, table, lang, passes=()):
        """Results page, only for rankings where not everyone saw every option: per option the mean normalised
        rank and the share put first, among those who saw it; best first. None otherwise (mean ranks compare)."""
        import statistics

        from app.items.base import plain

        if not self.partial_columns(item):
            return None
        found = self.normalized(item, table, passes)
        out = []
        for o in self.options(item, lang):
            values, top = found.get(o["code"], ([], 0))
            out.append({"label": plain(o["label"]) or o["code"], "code": o["code"], "n": len(values),
                        "mean": statistics.fmean(values) if values else None, "top": top / len(values) if values else None})
        return sorted(out, key=lambda r: (r["mean"] is None, -(r["mean"] or 0)))

    def summary(self, item, table, lang, passes=(), persons=None):
        """Results page, rankings everyone saw in full (UI/UX audit 2026-10-05, V56): per option the mean rank with
        its 95 % t-interval and the share put first; best (lowest mean rank) first. In a loop every pass counts, but
        the interval is over persons (cluster-robust, intervals.cluster_mean_ci): passes and the entries of one
        person in a diary study (persons: owner per row) are no independent cases (audit storage and calculations
        2026-10-28, M2). With one value per person it is the plain t interval."""
        import statistics

        from app import intervals
        from app.items.base import plain

        out = []
        for o in self.options(item, lang):
            cols = [f"{item.code}_{o['code']}_{n}" for n in passes] or [f"{item.code}_{o['code']}"]
            values, groups = [], {}
            for n, r in enumerate(table):
                for c in cols:
                    v = r.get(c)
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                        v = float(v)
                    elif isinstance(v, str) and v.isdigit() and int(v) > 0:
                        v = float(v)
                    else:
                        continue
                    values.append(v)
                    groups.setdefault(persons[n] if persons is not None else n, []).append(v)
            mean = statistics.fmean(values) if values else None
            out.append({"label": plain(o["label"]) or o["code"], "code": o["code"], "n": len(values), "mean": mean,
                        "persons": len(groups),
                        "ci": intervals.cluster_mean_ci(list(groups.values())) if mean is not None else None,
                        "top": sum(1 for v in values if v == 1) / len(values) if values else None})
        return sorted(out, key=lambda r: (r["mean"] is None, r["mean"] or 0))

    def export_values(self, item, answer):
        rank = {code: n for n, code in enumerate(answer or [], start=1)}
        # a ranking holds every option the person saw: a carried option that is not in it was not shown
        return {f"{item.code}_{o['code']}": rank.get(o["code"], rows.NOT_SHOWN if rank and rows.partial(o) else None)
                for o in item.config.get("options", [])}
