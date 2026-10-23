"""Einordnung in Wertebereiche (feedback): the participant's value on one variable (scale mean or single item)
is placed into 2 to 7 categories defined by the researcher, e.g. "< 25 unterdurchschnittlich,
25 bis < 75 im Rahmen, ab 75 überdurchschnittlich". Limits refer either to the value itself or
to the percentile rank among completed participations so far (from MIN_N on)."""
import math

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en, english
from app.items.base import r_flag, r_id, r_num, r_one, r_rows  # rules for survey files, see base.py
from app.items.base import MAX_NUMBER, AnswerError, whole  # noqa: F401  (AnswerError kept for symmetry with other item types)
from app.items.base import in_lang
from app.items.base import form_ids, new_id as _nid
from app.items.sample_compare import MIN_N, NUMERIC, SampleCompare
from app import units
from app.ui_i18n import tr

BASES = {"value": "Wert selbst (z. B. Skalenmittelwert 1 bis 5)",
         "percentile": "Prozentrang in der bisherigen Stichprobe (0 bis 100)"}
BASES_EN = {"value": "The value itself (e.g. scale mean 1 to 5)",
            "percentile": "Percentile rank in the sample so far (0 to 100)"}
MIN_BANDS, MAX_BANDS = 2, 7


@register
class ScoreBand(SampleCompare):
    key = "score_band"
    import_rules = {"sources": SampleCompare.import_rules["sources"], "subscales": SampleCompare.import_rules["subscales"],
                    "basis": r_one(*BASES),
                    "bands": r_rows(7, id=r_id, upper=r_num(-1e9, 1e9, none=True)), "lock_answers": r_flag}

    def import_problems(self, item):
        bands = item.config["bands"]
        if any(b["upper"] is None for b in bands[:-1]) or (bands and bands[-1]["upper"] is not None):
            return [de_en("Nur die letzte Kategorie ist nach oben offen.", "Only the last category is open at the top.")]
        return []
    label = Both("Einordnung in Wertebereiche", "Score bands")
    short = Both("Eigenen Wert einordnen", "Place one's own value")
    description = Both("Ordnet den eigenen Wert in 2 bis 7 selbst definierte Kategorien ein (z. B. unter-, über­durchschnittlich)",
                       "Places one's own value in 2 to 7 self-defined categories (e.g. below, above average)")

    def basis_names(self):
        """{key: name} of what the bounds refer to, in the language of the page (the editor's select)."""
        return BASES_EN if english() else BASES
    order = 57
    fact_sheet = FactSheet(
        "feedback",
        keywords=["Einordnung", "Wertebereiche", "Score bands", "Kategorien", "Bands", "Rückmeldung", "Feedback"],
        can=[("Der eigene Wert in 2 bis 7 selbst definierten Kategorien.",
              "One’s own value in 2 to 7 self-defined categories."),
             ("Nach dem Wert selbst oder nach dem Prozentrang in der bisherigen Stichprobe.",
              "By the value itself or by the percentile rank in the sample so far.")],
        good_for=[("Eine verständliche Einordnung eines Skalenwerts, z. B. niedrig, mittel, hoch",
                   "An understandable placing of a scale score, e.g. low, medium, high")],
        not_for=[("Diagnosen ohne geprüfte Normen", "Diagnoses without tested norms")],
        designs=["cross"],
        examples=[("Ihre Technikaffinität liegt im oberen Drittel.",
                   "Your affinity for technology is in the top third.")],
    )
    displays = {}

    def default_config(self):
        return {"sources": [], "basis": "percentile",
                "bands": [{"id": "b1", "upper": 25}, {"id": "b2", "upper": 75}, {"id": "b3", "upper": None}],
                "lock_answers": True}

    def default_texts(self, lang):
        return {"question": tr(lang, "Ihre Einordnung", "Where you stand"),
                "bands": {"b1": {"label": tr(lang, "unterdurchschnittlich", "below average"), "text": ""},
                          "b2": {"label": tr(lang, "im mittleren Bereich", "average"), "text": ""},
                          "b3": {"label": tr(lang, "überdurchschnittlich", "above average"), "text": ""}}}

    @staticmethod
    def upper_text(value):
        """An upper bound for the field of the editor, German in every tab as it is read back ("1000", "2,5"; "%g"
        gave "2.5", read back as 2,5 but "1.125" as 1125, and "1.23457e+06")."""
        return units.shown_plain(value, "de")

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt["question"] = form.get("question", "").strip()
        btexts = dict(lt.get("bands", {}))
        ids, uppers = form_ids(form.getlist("band_id")), form.getlist("band_upper")
        labels, descs = form.getlist("band_label"), form.getlist("band_text")
        if structural:
            bands = []
            stored = [b.get("upper") for b in item.config.get("bands", [])]
            for bid, upper, label, desc in zip(ids, uppers, labels, descs):
                if not label.strip():
                    continue
                bid = bid or _nid()
                # read like the values of conditions (German) in every tab: "1.000" is 1000, "1,000" is read as 1
                # with a note after saving (Mathe-Audit 2026-10-06, Z-6)
                value = units.read_entry(upper, "de", known=stored)
                bands.append({"id": bid, "upper": value})
                btexts[bid] = {"label": label.strip()[:100], "text": desc.strip()[:500]}
            if bands:
                bands[-1]["upper"] = None  # the last category is open upwards
            ids, subs = self.read_sources([form.get("source", "")])
            basis = form.get("basis") if form.get("basis") in BASES else "value"
            item.config = {**item.config, "sources": ids, "subscales": subs, "basis": basis, "bands": bands}
        else:
            known = {b["id"] for b in item.config.get("bands", [])}
            for bid, label, desc in zip(ids, labels, descs):
                if bid in known:
                    btexts[bid] = {"label": label.strip()[:100], "text": desc.strip()[:500]}
        lt["bands"] = btexts
        texts[lang] = lt
        item.texts = texts

    def candidates(self, item):
        return [c for c in super().candidates(item) if c.type in NUMERIC]

    def bands(self, item, lang):
        bt = item.t(lang).get("bands", {})
        return [{**b, "label": bt.get(b["id"], {}).get("label", ""), "text": bt.get(b["id"], {}).get("text", "")}
                for b in item.config.get("bands", [])]

    def validate(self, item):
        problems = []
        src = next(iter(self.sources(item)), None)
        if src is None:
            problems.append(de_en("keine Frage für die Einordnung ausgewählt", "no question chosen for the score bands"))
        elif src.id not in {c.id for c in self.candidates(item)}:
            problems.append(de_en("die Frage muss vor diesem Element stehen und eine Skala, Likert, semantisches Differential "
                                  "oder Bewertung sein",
                                  "the question must come before this element and be a scale, Likert, semantic "
                                  "differential or rating"))
        bands = item.config.get("bands", [])
        if not MIN_BANDS <= len(bands) <= MAX_BANDS:
            problems.append(de_en("{lo} bis {hi} Kategorien anlegen", "add {lo} to {hi} categories", lo=MIN_BANDS, hi=MAX_BANDS))
        uppers = [b["upper"] for b in bands[:-1]]
        if any(u is None for u in uppers):
            problems.append(de_en("jede Kategorie außer der letzten braucht eine Obergrenze", "every category except the last needs an upper bound"))
        elif any(a >= b for a, b in zip(uppers, uppers[1:])):
            problems.append(de_en("die Grenzen müssen aufsteigen", "the bounds must rise"))
        if any(not t.get("label") for t in (item.text("bands") or {}).values()) and bands:
            problems.append(de_en("jede Kategorie braucht eine Bezeichnung", "every category needs a name"))
        return problems

    def result(self, item, lang, own_answers, preview=False, row=None):
        """{"value", "shown" (value on the band scale), "lo", "hi", "band" (index), "bands", "n", "enough", ...}."""
        src = next(iter(self.sources(item)), None)
        if src is None:
            return None
        bands = self.bands(item, lang)
        sub = self.effective_subscale(item, src)  # an instrument with subscales: one of them, not all mixed
        own = self.value(src, own_answers.get(src.id), sub) if own_answers else None
        out = {"source": src, "subscale": sub, "bands": bands, "own": own, "basis": item.config.get("basis", "value"), "band": None,
               "shown": None, "n": 0, "enough": True}
        if out["basis"] == "percentile":
            values = [v for v in (self.value(src, a.get(str(src.id)), sub) for a in self.pool(item, preview, row)) if v is not None]
            out.update(n=len(values), enough=len(values) >= MIN_N, lo=0.0, hi=100.0)
            if own is not None and out["enough"]:
                below = sum(1 for v in values if v < own) + 0.5 * sum(1 for v in values if v == own)
                out["shown"] = 100.0 * below / len(values)
        else:
            lo, hi, *_ = self.axis(src, lang)
            out.update(lo=lo, hi=hi, shown=own)
        if out["shown"] is not None:
            out["band"] = next((i for i, b in enumerate(bands) if b["upper"] is None or out["shown"] < b["upper"]),
                               len(bands) - 1)
        # segment widths on the band bar
        edges = [out["lo"]] + [min(max(b["upper"], out["lo"]), out["hi"]) for b in bands[:-1]] + [out["hi"]]
        span = (out["hi"] - out["lo"]) or 1
        out["segments"] = [max(edges[i + 1] - edges[i], 0) / span * 100 for i in range(len(bands))]
        # the axis under the bar: the scale values at both ends and at every bound, so that the number the page
        # names ("Ihr Wert: 3,3") can be found on the bar (pre-test 2026-10-20: the category numbers looked like values)
        out["ticks"] = [(e, (e - out["lo"]) / span * 100) for i, e in enumerate(edges) if i in (0, len(edges) - 1)
                        or out["lo"] < e < out["hi"]]
        out["marker"] = None if out["shown"] is None else (min(max(out["shown"], out["lo"]), out["hi"]) - out["lo"]) / span * 100
        return out

    # export: the value that was placed and the category (1-based)
    def parse_answer(self, item, form, lang="de", meta=None):
        from flask import g, has_request_context

        value, band = form.get(f"i{item.id}_value", ""), form.get(f"i{item.id}_band", "")
        response = g.get("participation") if has_request_context() else None
        if response is not None:
            # a participation (app/participate.py): the hidden fields only say that the page placed a value. What
            # is stored is computed here again from the stored answers; a browser can send any number
            src = next(iter(self.sources(item)), None)
            if not (value and band) or src is None:
                return None
            row = (meta or {}).get("row")
            own = {src.id: (response.answers or {}).get(self.pool_key(item, src, row))}
            r = self.result(item, lang, own, preview=bool(getattr(response, "is_preview", False)), row=row)
            return {"value": round(r["shown"], 3), "band": r["band"] + 1} if r and r["band"] is not None else None
        # the preview and dummy runs of the researchers: as the page sent it
        try:
            answer = {"value": round(float(value), 3), "band": whole(band) or 0} if value and band else None
        except (ValueError, OverflowError):
            return None
        if answer and not (math.isfinite(answer["value"]) and abs(answer["value"]) <= MAX_NUMBER
                           and 1 <= answer["band"] <= len(item.config.get("bands", []))):
            return None
        return answer

    def codebook_info(self, item, column, lang, ui=None):
        if column.endswith("_band"):
            labels = "; ".join(f"{n} = {b['label'].replace(';', ',')}" for n, b in enumerate(self.bands(item, lang), start=1))
            return "Kategorie", labels, [in_lang(ui, "wie angezeigt", "as shown")]
        percentile = item.config.get("basis") == "percentile"
        return "Zahl", in_lang(ui, "0 bis 100", "0 to 100") if percentile else "", [
            in_lang(ui, "wie angezeigt; leer, wenn nichts eingeordnet werden konnte (Frage offen oder zu wenige Vergleichsdaten)",
                    "as shown; empty if nothing could be placed (question open or too little data to compare)")
            + (in_lang(ui, "; berechnet beim Absenden der Seite: Wurden seit der Anzeige weitere Teilnahmen abgeschlossen, "
                           "kann der Wert leicht vom angezeigten abweichen",
                       "; computed when the page was sent: if further participations were completed since it was shown, "
                       "the value can differ slightly from the one shown") if percentile else "")]

    def export_columns(self, item):
        what = "Prozentrang" if item.config.get("basis") == "percentile" else "Wert"
        return [(f"{item.code}_value", f"Einordnung: gezeigter {what}"),
                (f"{item.code}_band", "Einordnung: gezeigte Kategorie (1 = erste)")]

    def export_values(self, item, answer):
        answer = answer or {}
        return {f"{item.code}_value": answer.get("value"), f"{item.code}_band": answer.get("band")}
