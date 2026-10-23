"""Semantic differential: bipolar adjective pairs ("unfriendly - friendly") rated on a 5- or
7-point scale. Reverse coding per pair is recoded in the export (points + 1 - value), so the
positive pole can be shown on either side to avoid response sets."""

from app import rows, scales
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_id, r_int, r_maybe, r_rows, r_text  # rules for survey files, see base.py
from app.items.base import CODE_RE, AnswerError, ItemType, whole, read_cfg
from app.items.base import form_ids, new_id as _nid
from app.lang import Both, de_en
from app.ui_i18n import tr

POINTS = (5, 6, 7)  # offered in the editor; a survey file may bring 2 to 11 (import_rules), which stay (audit 2026-10-12, M-11)
ALL_POINTS = range(2, 12)


@register
class SemanticDifferential(ItemType):
    key = "semantic"
    scale_keys = ("points",)  # fixed once real answers exist (ItemType.answer_shape)
    import_rules = {"points": r_int(2, 11), "numbers": r_flag, "randomize": r_flag,
                    "pairs": r_rows(300, id=r_id, code=r_text(64), reverse=r_maybe(r_flag), subscale=r_maybe(r_text(100)),
                                    when=rows.r_when)}
    uses_answers = True  # which pairs a person sees can depend on earlier answers (config "when", app/rows.py)
    result_kind = 'scale'
    levels = ("ordinal", "metric")
    label = Both("Semantisches Differential", "Semantic differential")
    short = Both("Gegensatzpaare auf einer Skala", "Opposite pairs on a scale")
    description = Both("Gegensatzpaare (z. B. unfreundlich – freundlich) auf einer 5- bis 7-stufigen Skala",
                       "Opposite pairs (e.g. unfriendly – friendly) on a 5- to 7-point scale")
    order = 32
    fact_sheet = FactSheet(
        "scales",
        keywords=["Polaritätsprofil", "Polarity profile", "Gegensatzpaare", "Bipolar", "Osgood"],
        can=[("Gegensatzpaare auf 5 bis 7 Stufen.", "Opposite pairs on 5 to 7 points."),
             ("Mehrdimensional: jedes Gegensatzpaar einer Subskala zuordnen.",
              "Multidimensional: assign each opposite pair to a subscale.")],
        good_for=[("Das Bild von etwas: einer Marke, einem Roboter, einer Person",
                   "The image of something: a brand, a robot, a person"),
                  ("Wahrnehmung von Stimuli im Experiment", "Perception of stimuli in an experiment")],
        not_for=[("Zustimmung zu Aussagen (dann Likert-Skala)", "Agreement with statements (then Likert scale)")],
        designs=["cross", "perception", "framing", "within"],
        examples=[("Der Roboter wirkt: kalt – warm, unfreundlich – freundlich",
                   "The robot seems: cold – warm, unfriendly – friendly")],
    )

    def default_level(self, item):
        # validated constructs are scored as means (quasi-interval), everything else counts as ordinal
        from app import constructs

        return "metric" if constructs.toolbox_key(item) else "ordinal"

    def default_config(self):
        return {"points": 7, "numbers": False, "randomize": False,
                "pairs": [{"id": _nid(), "code": "", "reverse": False}]}

    def default_texts(self, lang):
        return {"question": "", "pairs": {}}

    def one_click(self, item):
        return True

    def update_from_form(self, item, form, lang, structural):
        config, texts = dict(item.config), dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt["question"] = form.get("question", "").strip()
        ptexts = dict(lt.get("pairs", {}))
        ids, codes = form_ids(form.getlist("pair_id")), form.getlist("pair_code")
        lefts, rights = form.getlist("pair_left"), form.getlist("pair_right")
        reverse = set(form.getlist("pair_reverse"))
        if structural:
            pairs = []
            before = {p["id"]: p for p in config.get("pairs", [])}
            for pid, code, left, right in zip(ids, codes, lefts, rights):
                if not (code.strip() or left.strip() or right.strip()):
                    continue
                pair = {"id": pid or _nid(), "code": code.strip(), "reverse": pid in reverse}
                subscale = scales.subscale_from_form(form, "pair", pid, before.get(pid))
                if subscale:  # an instrument with several dimensions (constructs, or ticked in the editor)
                    pair["subscale"] = subscale
                pairs.append(pair)
                ptexts[pid] = {"left": left.strip(), "right": right.strip()}
            rows.from_form(item, pairs, form, before, lang)  # shown only to some: the condition of each pair
            points, kept = whole(form.get("points", "")), config.get("points") if config.get("points") in ALL_POINTS else 7
            config.update(pairs=pairs, points=points if points in ALL_POINTS else kept,
                          numbers=bool(form.get("numbers")), randomize=bool(form.get("randomize")))
        else:
            known = {p["id"] for p in config.get("pairs", [])}
            for pid, left, right in zip(ids, lefts, rights):
                if pid in known:
                    ptexts[pid] = {"left": left.strip(), "right": right.strip()}
        lt["pairs"] = ptexts
        texts[lang] = lt
        item.config, item.texts = config, texts

    def order_codes(self, item):
        if not read_cfg(item).get("randomize"):
            return None
        return {p["id"]: self.pair_code(item, i, p) for i, p in enumerate(read_cfg(item).get("pairs", []))}

    def pair_code(self, item, index, pair):
        return pair.get("code") or f"{item.code}_{index + 1}"

    def result_column(self, item, column):
        """Results page: every scale point with the poles at the ends (swapped for a reversed pair)."""
        points = read_cfg(item)["points"]
        pair = next((p for p in self.pairs(item, item.survey.default_language) if p["export_code"] == column), None)
        labels = {str(n): "" for n in range(1, points + 1)}
        if pair:
            left, right = (pair["right"], pair["left"]) if pair.get("reverse") else (pair["left"], pair["right"])
            labels["1"], labels[str(points)] = left, right
        return self.result_kind, "", labels

    def profile(self, item, columns, passes, captions, lang):
        """Results page: the polarity profile, one row per pair between its poles (swapped for a reversed pair, as
        the recoded values are). Per pass of a loop one series: its location on every pair (median with the
        quartiles when ordinal, the mean with one SD when metric) and a line through them (Kennlinie). x is in
        percent of the track, 1 at 5 %, the last point at 95 %. None without a single answered pair."""
        from app.items.base import plain

        points = read_cfg(item).get("points", 7)
        if points < 2:
            return None

        def x(v):
            return round(5 + 90 * (min(max(float(v), 1), points) - 1) / (points - 1), 2)

        def location(c):
            if c.get("type") == "ord" and c.get("median"):
                lo, hi = float(c["q1"]["value"]), float(c["q3"]["value"])
                mid = float(c["median"]["value"])
                if c.get("median_hi"):  # between two levels: the marker between them
                    mid = (mid + float(c["median_hi"]["value"])) / 2
                return {"mid": x(mid), "lo": x(lo), "hi": x(hi), "n": c["n"], "kind": "ord", "median": mid, "q1": lo, "q3": hi}
            if c.get("type") == "num" and c.get("n"):
                m, sd = c["mean"], c.get("sd") or 0
                return {"mid": x(m), "lo": x(m - sd), "hi": x(m + sd), "n": c["n"], "kind": "num", "mean": m, "sd": c.get("sd")}
            return None

        by_code = {c["code"]: c for c in columns}
        # "label": the whole caption "Pass 1: dem Auto" for legend and tooltip, as the bars below (X91)
        series = [{"pass": n, "caption": (captions[n].split(": ", 1)[1] if ": " in captions[n] else captions[n]) if n else "",
                   "label": captions[n] if n else ""}
                  for n in (passes or [None])]
        rows_out = []
        for p in self.pairs(item, lang):
            left, right = plain(p["left"]), plain(p["right"])
            if p.get("reverse"):
                left, right = right, left
            cells = [location(by_code.get(f"{p['export_code']}_{s['pass']}" if s["pass"] else p["export_code"], {}))
                     for s in series]
            rows_out.append({"code": p["export_code"], "left": left, "right": right, "cells": cells})
        if not any(cell for r in rows_out for cell in r["cells"]):
            return None
        for i, s in enumerate(series):  # the line of a series, in track units: x in percent, y in rows (0.5 = row 1)
            s["line"] = " ".join(f"{r['cells'][i]['mid']},{n + 0.5}" for n, r in enumerate(rows_out) if r["cells"][i])
        return {"points": points, "ticks": [{"value": v, "x": x(v)} for v in range(1, points + 1)],
                "rows": rows_out, "series": series, "metric": item.kind.level(item) == "metric"}

    # piping: each pair by its column; the text is the chosen point as a number (1 = the left pole)
    def pipe_parts(self, item, lang):
        from app.items.base import plain

        return [(p["export_code"], f"{plain(p['left'])} – {plain(p['right'])}") for p in self.pairs(item, lang)]

    def pipe_part_text(self, item, answer, name, lang):
        for i, p in enumerate(read_cfg(item).get("pairs", [])):
            if self.pair_code(item, i, p) == name:
                v = (answer or {}).get(p["id"]) if isinstance(answer, dict) else None
                return str(v) if isinstance(v, int) else ""
        return ""

    def pairs(self, item, lang, order=None, answers=None):
        """answers: the answers so far of the person who sees the page; the pairs not shown to them are left
        out (app/rows.py). Without them every pair (editor, export, results)."""
        ptexts = item.t(lang).get("pairs", {})
        gone = rows.hidden(read_cfg(item).get("pairs", []), answers, item.survey)
        found = [{**p, "left": ptexts.get(p["id"], {}).get("left", ""), "right": ptexts.get(p["id"], {}).get("right", ""),
                  "export_code": self.pair_code(item, i, p)} for i, p in enumerate(read_cfg(item).get("pairs", []))
                 if p["id"] not in gone]
        if order:
            rank = {pid: n for n, pid in enumerate(order)}
            found.sort(key=lambda r: rank.get(r["id"], len(rank)))
        return found

    def row_conditions(self, item, lang):
        """The editor's part "Gegensatzpaare nur manchen zeigen"."""
        from app.items.base import plain

        return rows.editor_view(item, [(p, f"{plain(p['left'])} – {plain(p['right'])}", p["export_code"])
                                       for p in self.pairs(item, lang)], lang)

    def applies(self, item, answers):
        pairs = read_cfg(item).get("pairs", [])
        return not pairs or len(rows.hidden(pairs, answers, item.survey)) < len(pairs)

    def missing_codes(self, item, column):
        """-77 (not shown) for the column of a pair that only some people see."""
        for i, p in enumerate(read_cfg(item).get("pairs", [])):
            if p.get("when") and self.pair_code(item, i, p) == column:
                return (rows.NOT_SHOWN,)
        return ()

    def remap_config(self, config, twin):
        if not rows.conditional(config.get("pairs", [])):
            return config
        return {**config, "pairs": rows.remap(config["pairs"], twin)}

    def check_warnings(self, item):
        return super().check_warnings(item) + scales.subscale_warnings(item)

    def validate(self, item):
        problems = []
        pairs = read_cfg(item).get("pairs", [])
        if not pairs:
            problems.append(de_en("mindestens ein Gegensatzpaar", "at least one opposite pair"))
        ptexts = item.text("pairs") or {}
        if any(not (ptexts.get(p["id"], {}).get("left") and ptexts.get(p["id"], {}).get("right")) for p in pairs):
            problems.append(de_en("Gegensatzpaar ohne beide Begriffe", "opposite pair without both terms"))
        codes = [self.pair_code(item, i, p) for i, p in enumerate(pairs)]
        if len(set(codes)) != len(codes):
            problems.append(de_en("Variablennamen der Paare sind nicht eindeutig", "variable names of the pairs are not unique"))
        bad = [c for c in codes if not CODE_RE.match(c)]
        if bad:
            problems.append(de_en("Variablenname „{name}“ muss mit einem Buchstaben beginnen", "variable name “{name}” must start with a letter",
                                  name=bad[0]))
        return problems + rows.problems(item, pairs, lambda i, p: self.pair_code(item, i, p), "Zeile")

    def parse_answer(self, item, form, lang="de", meta=None, answers=None):
        points, answer, missing = read_cfg(item)["points"], {}, 0
        gone = rows.hidden(read_cfg(item).get("pairs", []), answers, item.survey)
        if gone:
            answer[rows.HIDDEN] = sorted(gone)  # not shown is not the same as left out (export: -77)
        for p in read_cfg(item).get("pairs", []):
            if p["id"] in gone:
                continue
            v = form.get(f"i{item.id}_{p['id']}", "")
            if whole(v) is not None and 1 <= whole(v) <= points:
                answer[p["id"]] = whole(v)
            else:
                missing += 1
        if item.required and missing:
            raise AnswerError(tr(lang, "Bitte bewerten Sie alle Begriffspaare (noch offen: {n}).",
                                 "Please rate all pairs ({n} still open).", n=missing))
        return answer

    def display_value(self, item, form, meta=None):
        out = {}
        for p in read_cfg(item).get("pairs", []):
            v = form.get(f"i{item.id}_{p['id']}", "")
            if whole(v) is not None:
                out[p["id"]] = whole(v)
        return out

    def is_missing(self, item, answer):
        gone = rows.hidden_in(answer)
        return any((answer or {}).get(p["id"]) is None for p in read_cfg(item).get("pairs", []) if p["id"] not in gone)

    def export_columns(self, item):
        ptexts = item.text("pairs") or {}
        return [(self.pair_code(item, i, p),
                 f"{ptexts.get(p['id'], {}).get('left', '')} – {ptexts.get(p['id'], {}).get('right', '')}"
                 + (" (rekodiert)" if p.get("reverse") else "")
                 + (f" [nur gezeigt, wenn {rows.text(item, p)}; {rows.NOT_SHOWN} = nicht gezeigt]" if p.get("when") else ""))
                for i, p in enumerate(read_cfg(item).get("pairs", []))]

    def export_values(self, item, answer):
        points, out = read_cfg(item)["points"], {}
        gone = rows.hidden_in(answer)
        for i, p in enumerate(read_cfg(item).get("pairs", [])):
            v = (answer or {}).get(p["id"])
            v = points + 1 - v if v is not None and p.get("reverse") else v
            out[self.pair_code(item, i, p)] = rows.NOT_SHOWN if p["id"] in gone else v
        return out
