"""2D positioning (perception map): participants place several elements (e.g. technologies) in a
field with two labelled axes, e.g. risk (x) and benefit (y).

In the tradition of the risk-benefit maps of the psychometric paradigm (Fischhoff et al., 1978;
Slovic, 1987), but placed directly instead of averaged from rating scales; related to the spatial
arrangement method (Hout, Goldinger & Ferguson, 2013) and, for a single point, the affect grid
(Russell, Weiss & Mendelsohn, 1989).

Interaction: tap an element in the tray, then tap in the field (works on phones); drag to move;
a placed element can be taken back to the tray. Optionally an element may be left out
("kann ich nicht einschätzen"). Coordinates are stored from 0 to 100 on both axes (0 = left or
bottom), with one decimal. Export: <code>_<element>_x and <code>_<element>_y."""
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.choice import LAYOUT_RULE, OPTION_RULES
from app.items.base import r_flag  # rules for survey files, see base.py
from app.items.base import AnswerError, in_lang
from app.items.choice import _Choice
from app.lang import Both, de_en
from app.ui_i18n import tr

AXES = ("x_low", "x_high", "y_low", "y_high")
FISCHHOFF = ("Fischhoff, B., Slovic, P., Lichtenstein, S., Read, S., & Combs, B. (1978). How safe is safe enough? A "
             "psychometric study of attitudes towards technological risks and benefits. Policy Sciences, 9(2), 127–152. "
             "https://doi.org/10.1007/BF00143739")
SLOVIC = "Slovic, P. (1987). Perception of risk. Science, 236(4799), 280–285. https://doi.org/10.1126/science.3563507"
HOUT = ("Hout, M. C., Goldinger, S. D., & Ferguson, R. W. (2013). The versatility of SpAM: A fast, efficient, spatial method "
        "of data collection for multidimensional scaling. Journal of Experimental Psychology: General, 142(1), 256–281. "
        "https://doi.org/10.1037/a0028860")


def _coord(value):
    """'x,y' from the form -> (x, y) within 0..100, or None."""
    try:
        x, y = (float(v) for v in (value or "").split(","))
    except ValueError:
        return None
    if not (0 <= x <= 100 and 0 <= y <= 100):
        return None
    return round(x, 1), round(y, 1)


@register
class Position2D(_Choice):
    uses_answers = False  # no options carried from an earlier question here
    key = "position2d"
    read_keys = ("allow_skip",)  # switched off its _skip columns go (only switching on; audit 2026-10-12, M-3)
    code_answer = False  # not a code to compare with: a condition only asks "answered or not" (X17)
    import_rules = {"options": OPTION_RULES, "layout": LAYOUT_RULE, "randomize": r_flag, "allow_skip": r_flag}
    label = Both("2D-Positionierung", "2D positioning")

    def source_groups(self, item):
        return [(de_en("Vorbild: Risiko-Nutzen-Karten", "Model: risk-benefit maps"), [FISCHHOFF, SLOVIC]),
                (de_en("Verwandt: Spatial Arrangement Method", "Related: Spatial Arrangement Method"), [HOUT])]
    codes_in_columns = True
    short = Both("Begriffe auf zwei Achsen setzen", "Place terms on two axes")
    description = Both("Mehrere Begriffe in ein Feld mit zwei beschrifteten Achsen setzen (z. B. Risiko × Nutzen)",
                       "Place several terms in a field with two labelled axes (e.g. risk × benefit)")
    order = 27
    fact_sheet = FactSheet(
        "spatial",
        keywords=["Positionierung", "Perceptual map", "Wahrnehmungskarte", "Achsen", "Axes", "Koordinaten"],
        can=[("Mehrere Begriffe in ein Feld mit zwei beschrifteten Achsen setzen.",
              "Place several terms in a field with two labelled axes."),
             ("Jeder Begriff ergibt zwei Werte, je Achse einen.", "Each term gives two values, one per axis.")],
        good_for=[("Abwägungen auf zwei Achsen, z. B. Risiko und Nutzen von Technologien",
                   "Trade-offs on two axes, e.g. risk and benefit of technologies"),
                  ("Vergleich vieler Begriffe auf einen Blick", "Comparing many terms at a glance")],
        not_for=[("Mehr als zwei Bewertungen je Begriff (dann Side-by-Side-Matrix oder Likert-Skala)",
                  "More than two evaluations per term (then side-by-side matrix or Likert scale)")],
        designs=["perception", "cross"],
        examples=[("Setzen Sie jede Technologie nach Risiko und Nutzen in das Feld.",
                   "Place each technology in the field by risk and benefit.")],
    )

    def default_config(self):
        return {**super().default_config(), "randomize": True, "allow_skip": False}

    def default_texts(self, lang):
        return {**super().default_texts(lang),
                "x_label": tr(lang, "Risiko", "Risk"), "x_low": tr(lang, "gering", "low"), "x_high": tr(lang, "hoch", "high"),
                "y_label": tr(lang, "Nutzen", "Benefit"), "y_low": tr(lang, "gering", "low"), "y_high": tr(lang, "hoch", "high")}

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        texts = dict(item.texts)
        texts[lang] = {**texts.get(lang, {}),
                       **{k: form.get(k, "").strip()[:40] for k in ("x_label", "y_label") + AXES}}
        item.texts = texts

    def _update_extra_config(self, config, form):
        config["randomize"] = bool(form.get("randomize"))
        config["allow_skip"] = bool(form.get("allow_skip"))
        config.pop("layout", None)

    def validate(self, item):
        problems = [p.replace("Antwortoption", "Element").replace("Antwortcodes", "Codes")
                     .replace("answer option", "element").replace("answer codes", "codes") for p in super().validate(item)]
        if not (item.text("x_label") and item.text("y_label")):
            problems.append(de_en("Achsen ohne Bezeichnung", "axes without a label"))
        return problems

    def _values(self, item, form):
        return {o["code"]: _coord(form.get(f"i{item.id}_{o['code']}")) for o in item.config.get("options", [])}

    def parse_answer(self, item, form, lang="de", meta=None):
        values = self._values(item, form)
        skipped = set(form.getlist(f"i{item.id}_skip")) & set(values) if item.config.get("allow_skip") else set()
        # never both: the tick wins over a position sent along with it, as on the page, where ticking it takes the
        # element out of the field (code review 2026-10-15, F8)
        placed = {k: list(v) for k, v in values.items() if v and k not in skipped}
        open_ = [k for k in values if k not in placed and k not in skipped]
        if open_ and item.required:
            raise AnswerError(tr(lang, "Bitte setzen Sie alle Elemente in das Feld.", "Please place all elements in the field."))
        if not placed and not skipped:
            return None
        return {**placed, **({"_skip": sorted(skipped)} if skipped else {})}

    def display_value(self, item, form, meta=None):
        values = {k: list(v) for k, v in self._values(item, form).items() if v}
        return values or None

    def is_missing(self, item, answer):
        if not answer:
            return True
        skipped = set(answer.get("_skip", []))
        return any(o["code"] not in answer and o["code"] not in skipped for o in item.config.get("options", []))

    def ordered_options(self, item, lang, start_order=None):
        opts = self.options(item, lang)
        if start_order:
            rank = {oid: n for n, oid in enumerate(start_order)}
            return sorted(opts, key=lambda o: rank.get(o["id"], len(rank)))
        return opts

    def order_codes(self, item):
        if not item.config.get("randomize", True):  # the order in the tray
            return None
        return {o["id"]: f"{item.code}_{o['code']}" for o in item.config.get("options", [])}

    def export_columns(self, item):
        labels = item.text("options") or {}
        x, y = item.text("x_label") or "x", item.text("y_label") or "y"
        cols = []
        for o in item.config.get("options", []):
            name = labels.get(o["id"], o["code"])
            cols += [(f"{item.code}_{o['code']}_x", f"„{name}“: {x} (0 = {item.text('x_low') or 'links'}, 100 = {item.text('x_high') or 'rechts'})"),
                     (f"{item.code}_{o['code']}_y", f"„{name}“: {y} (0 = {item.text('y_low') or 'unten'}, 100 = {item.text('y_high') or 'oben'})")]
            if item.config.get("allow_skip"):
                cols.append((f"{item.code}_{o['code']}_skip", f"„{name}“: kann ich nicht einschätzen"))
        return cols

    def export_values(self, item, answer):
        out = {}
        skipped = set((answer or {}).get("_skip", []))  # "_skip" is no element: codes never start with "_" (validate)
        for o in item.config.get("options", []):
            xy = None if o["code"] == "_skip" else (answer or {}).get(o["code"])
            out[f"{item.code}_{o['code']}_x"] = xy[0] if xy else None
            out[f"{item.code}_{o['code']}_y"] = xy[1] if xy else None
            if item.config.get("allow_skip"):
                # the deliberate "can't say" apart from "not placed" and "never seen" (empty)
                out[f"{item.code}_{o['code']}_skip"] = None if answer is None else int(o["code"] in skipped)
        return out

    def codebook_info(self, item, column, lang, ui=None):
        if item.config.get("allow_skip") and column in {f"{item.code}_{o['code']}_skip" for o in item.config.get("options", [])}:
            return ("Auswahl", "0 = nicht angekreuzt; 1 = „kann ich nicht einschätzen“",
                    [in_lang(ui, "leer = Frage nicht beantwortet", "empty = question not answered")])
        if item.config.get("allow_skip"):
            skip = [in_lang(ui, "leer = nicht gesetzt oder „kann ich nicht einschätzen“ (siehe Spalte _skip)",
                            "empty = not placed or “cannot judge” (see column _skip)")]
        else:
            skip = [in_lang(ui, "leer = nicht gesetzt", "empty = not placed")]
        return ("Zahl", in_lang(ui, "0 bis 100", "0 to 100"), skip)

    def map_points(self, item, table, lang, passes=()):
        """Mean position per element over the given export rows: [(label, mx, my, sdx, sdy, n)] for the results map.
        In a loop every pass counts as one placement."""
        import statistics

        labels = item.t(lang).get("options", {})
        out = []
        for o in item.config.get("options", []):
            pts = [(r.get(f"{item.code}_{o['code']}_x{s}"), r.get(f"{item.code}_{o['code']}_y{s}"))
                   for r in table for s in ([f"_{n}" for n in passes] or [""])]
            pts = [(x, y) for x, y in pts if isinstance(x, (int, float)) and isinstance(y, (int, float))]
            if not pts:
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.append((labels.get(o["id"], o["code"]), statistics.fmean(xs), statistics.fmean(ys),
                        statistics.stdev(xs) if len(xs) > 1 else None, statistics.stdev(ys) if len(ys) > 1 else None,
                        len(pts)))  # no SD from one placement ("–"), not 0
        return out
