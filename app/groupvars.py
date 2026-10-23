"""The ways the results of a survey can be split into groups ("Nach Gruppen aufteilen"; concept
gruppen-aufteilen-konzept.md, decisions E1 to E9 as recommended, Ralf 2026-10-06).

Every way is a GroupVar: a key for the address (?gruppen=...), a label for the choice, a section of the choice, the
groups (code, label) in their order, and which group a row of the results table (export.rows) belongs to. The
results page (app/compare.py) splits by it; the columns are those of the data export, so nothing new is stored.

  section               key                  column of export.rows          groups
  Versuchsbedingungen   rand:<code>          <code> (the drawn element)     one per element of a randomizer drawing one
                        rand:<code>:<node>   <code> (the drawn elements)    "gezeigt" / "nicht gezeigt" for one element of a
                                                                            randomizer drawing several (E2: not the
                                                                            combinations, they get small fast)
  Fragen                <item id>            <item code>                    the answers of a single choice question
                                                                            (2 to 8, outside loops: boards.groupers)
  Quoten                quota:<code>         quota_<code>                   the cells of the quota (2 to 8)
  Link-Parameter        url:<name>           url_<name>                     the allowed values of a stored parameter
                                                                            (2 to 8); never a once-only one (a panel ID)
  Mehrfachauswahl       mc:<item id>:<code>  <item code>_<option code>      "angekreuzt" / "nicht angekreuzt" (E5)
  Erhebungsweg          mode                 mode                           online / Papier, once there are paper sheets
  Zahlen in Klassen     klassen:<item id>    <item code>                    classes between bounds (E4): saved per
                                                                            survey (data_pool["bins"]), else the usual
                                                                            age bands for an age, else round bounds
                                                                            near the tertiles of the data

Splitting numbers into classes loses information; for an association the correlation is the better test (MacCallum,
Zhang, Preacher & Rucker, 2002): the page says so.

A randomizer splits into conditions the participants were assigned to at random: differences between them can be read
as effects of the condition (experiment); every other way compares groups that formed themselves. Questions in loops
and vignettes are answered several times by the same person: a comparison would need tests for repeated measures
and is not offered. A group variable with more than MAX_GROUPS groups is not offered (E3).
"""
from app.items.base import plain
from app.lang import de_en
from app.ui_i18n import tr  # the words of the groups in the language of a dashboard

MAX_GROUPS = 8
SECTIONS = ("rand", "items", "mc", "classes", "quotas", "params", "mode")
NUMBER_TYPES = ("text_input", "slider", "rating", "calc")  # one number per person
AGE_BOUNDS = [18, 30, 45, 60]
MAX_BOUNDS = MAX_GROUPS - 1
MACCALLUM = ("MacCallum, R. C., Zhang, S., Preacher, K. J., & Rucker, D. D. (2002). On the practice of dichotomization "
             "of quantitative variables. Psychological Methods, 7(1), 19–40. https://doi.org/10.1037/1082-989X.7.1.19")
SRM_ALPHA = 0.01  # sample ratio mismatch: warned below this p (concept section 7, E8)
FABIJAN = ("Fabijan, A., Gupchup, J., Gupta, S., Omhover, J., Qin, W., Vermeer, L., & Dmitriev, P. (2019). Diagnosing "
           "sample ratio mismatch in online controlled experiments: A taxonomy and rules of thumb for practitioners. In "
           "Proceedings of the 25th ACM SIGKDD International Conference on Knowledge Discovery & Data Mining "
           "(pp. 2156–2164). https://doi.org/10.1145/3292500.3330722")


class GroupVar:
    def __init__(self, key, code, label, title, section, groups, value, item=None, experiment=False, node=None):
        self.id = key            # in the address and in the key of the kept comparison
        self.code = code         # the column of the data export it is read from
        self.label = label       # in the choice
        self.title = title       # above the comparison of a question ("Nach Gruppen: …")
        self.section = section
        self.groups = groups     # [(code, label)]
        self._value = value      # row -> group code or None
        self.item = item         # the grouping question itself (not compared with itself)
        self.experiment = experiment
        self.node = node         # the randomizer (for the check of the assignment)
        self.stamp = key         # what the kept comparison is keyed by (classes: with their bounds)
        self.note = ""           # said under the choice

    def prepare(self, table):
        """Groups that depend on the data are set here, before the comparison (classes without saved bounds)."""

    def member(self, row):
        """The group of a row of the results table, None if it belongs to none of them."""
        found = self._value(row)
        return found if found in {c for c, _ in self.groups} else None


def _text(value):
    if value in (None, ""):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _child_label(survey, node, lang):
    if node["t"] == "block" and lang:
        block = next((b for b in survey.blocks if b.id == node["block"]), None)
        if block is not None and block.title(lang):
            return block.title(lang)
    from app import flow

    return flow.child_label(survey, node)


def _randomizers(survey, lang=None):
    from app import export, flow

    out = {}
    for node in export._randomizers(survey):
        code, children = node.get("code"), node.get("children") or []
        if not code or len(children) < 2:
            continue
        labels = [flow.child_label(survey, c) for c in children]
        if len(set(labels)) < len(labels):  # two elements of the same name: the column cannot tell them apart
            continue
        name = node.get("name") or code
        k = node.get("k") or 1
        shown = [_child_label(survey, c, lang) for c in children]  # the code stays the value of the export column
        if k == 1:
            if len(labels) <= MAX_GROUPS:
                label = de_en("{code} · Randomisierung „{name}“", "{code} · randomisation “{name}”", code=code, name=name)
                out[f"rand:{code}"] = GroupVar(
                    f"rand:{code}", code, label, label, "rand", list(zip(labels, shown)), lambda r, c=code: _text(r.get(c)),
                    experiment=True, node=node)
        elif k < len(children):  # several of them drawn: per element shown or not (E2)
            for child, label in zip(children, labels):
                key = f"rand:{code}:{flow.node_id(child)}"

                def value(r, c=code, l=label):
                    drawn = _text(r.get(c))
                    return None if drawn is None else ("1" if l in drawn.split("|") else "0")

                out[key] = GroupVar(key, code, de_en("{code} · „{label}“ gezeigt (Randomisierung „{name}“)",
                                                     "{code} · “{label}” shown (randomisation “{name}”)",
                                                     code=code, label=label, name=name),
                                    de_en("{code} · „{label}“ gezeigt", "{code} · “{label}” shown", code=code, label=label),
                                    "rand",
                                    [("1", tr(lang or "de", "gezeigt", "shown")), ("0", tr(lang or "de", "nicht gezeigt", "not shown"))],
                                    value, experiment=True)
    return out


def _questions(survey, lang=None):
    from app import boards, compare

    lang = lang or survey.default_language
    out = {}
    for item in boards.groupers(survey).values():
        label = f"{item.code}: {plain(item.text('question', lang))[:70]}"
        out[str(item.id)] = GroupVar(str(item.id), item.code, label, plain(item.text("question", lang)) or item.code,
                                     "items", compare.groups_of(item), lambda r, c=item.code: _text(r.get(c)),
                                     item=item)
    return out


def _quotas(survey, lang=None):
    out = {}
    for quota in survey.quotas:
        cells = [(c.code, c.name or c.code) for c in quota.cells if c.code]
        if 2 <= len(cells) <= MAX_GROUPS:
            key, column = f"quota:{quota.code}", f"quota_{quota.code}"
            label = de_en("Quote „{name}“", "quota “{name}”", name=quota.name)
            out[key] = GroupVar(key, column, label, label, "quotas", cells,
                                lambda r, c=column: _text(r.get(c)))
    return out


def _parameters(survey):
    from app import panel

    out = {}
    own = panel.value_labels(survey)  # links per person ("Eigene Links" on "Datenerhebung"): said so (X56)
    for p in panel.config(survey)["params"]:
        allowed = [str(v) for v in (p.get("allowed") or []) if str(v)]
        if p.get("mode") != "store" or p.get("once") or not 2 <= len(allowed) <= MAX_GROUPS:
            continue
        name = p["name"]
        key, column = f"url:{name}", f"url_{name}"
        if name in own:
            label = de_en("Eigene Links", "Own links") if len(own) == 1 else de_en("Eigene Links ({name})", "Own links ({name})", name=name)
            title = label
        else:
            label = de_en("{name} · Link-Parameter", "{name} · link parameter", name=name)
            title = de_en("Link-Parameter {name}", "link parameter {name}", name=name)
        out[key] = GroupVar(key, column, label, title, "params",
                            [(v, v) for v in dict.fromkeys(allowed)], lambda r, c=column: _text(r.get(c)))
    return out


def _plain_items(survey):
    from app import export

    return [item for block in survey.blocks for item in block.items if not export._loop_rows(item)]


def _multiple(survey, lang=None):
    lang = lang or survey.default_language
    out = {}
    for item in _plain_items(survey):
        if item.type != "multiple_choice":
            continue
        labels = item.text("options", lang) or {}
        gone = {str(m) for m in item.kind.missing_codes(item, item.code)}
        options = item.config.get("options", [])
        columns = tuple(f"{item.code}_{o['code']}" for o in options)
        # nothing ticked at all is ambiguous where the question may be skipped and has no "keine davon": skipped or
        # "none of them" (Smyth et al., 2006); such a row belongs to no group (audit 2026-10-06, E-7, decision 5). With
        # a "keine davon" nothing ticked is plainly skipped: no group either (audit 2026-10-10, B-23)
        unsure = not item.required
        for o in options:
            code = str(o["code"])
            if code in gone:
                continue
            option = plain(labels.get(o["id"], "")) or code
            key, column = f"mc:{item.id}:{code}", f"{item.code}_{code}"

            def value(r, c=column, every=columns, unsure=unsure):
                found = _text(r.get(c))
                if unsure and found == "0" and not any(_text(r.get(x)) == "1" for x in every):
                    return None
                return found

            var = GroupVar(key, column, de_en("{code}: „{option}“ angekreuzt", "{code}: “{option}” ticked",
                                              code=item.code, option=option[:50]),
                           f"{item.code}: „{option}“", "mc", [("1", tr(lang or "de", "angekreuzt", "ticked")),
                                                              ("0", tr(lang or "de", "nicht angekreuzt", "not ticked"))],
                           value, item=item)
            var.untouched = unsure  # the page says why such rows are without a group
            out[key] = var
    return out


def number_items(survey):
    """The questions with one number per person that can be split into classes."""
    out = []
    for item in _plain_items(survey):
        if item.type not in NUMBER_TYPES or [c for c, _ in item.kind.export_columns(item)] != [item.code]:
            continue
        if item.type == "text_input" and (item.config or {}).get("validation") not in ("integer", "decimal"):
            continue
        if item.type == "rating" and item.kind.level(item) != "metric":
            continue
        out.append(item)
    return out


def clean_bounds(values, known=None):
    """Ascending, distinct bounds (at most MAX_BOUNDS) from numbers or a text like "18; 30; 45; 60" or "18 30 45 60";
    None if there is none. A text is read in the language of the page (units.read_entry: "2,5" and "1.000" in
    German, "2.5" and "1,000" in English; Mathe-Audit 2026-10-06, Z-4: "1.000; 2.000" was [1; 2]); "1,000" in German
    is read as 1 with a note, unless it is one of the bounds `known` (stored so far)."""
    import re

    from app import units

    if isinstance(values, str):
        tokens = []
        for token in re.split(r"[;\s]+", values.replace(", ", ";")):
            tokens += token.split(",") if token.count(",") > 1 else [token]
        values = [t for t in tokens if t]
    out = []
    for v in values or []:
        try:
            x = float(v) if isinstance(v, (int, float)) else units.read_entry(v, known=known)
        except ValueError:
            continue
        if x is None:
            continue
        if x == x and abs(x) < 1e12:
            out.append(int(x) if float(x).is_integer() else x)
    return sorted(set(out))[:MAX_BOUNDS] or None


def saved_bounds(survey, item):
    """The bounds saved for the classes of this question, None without."""
    found = ((survey.data_pool or {}).get("bins") or {}).get(str(item.id))
    return clean_bounds(found) if found else None


def _is_age(item):
    construct = (item.config or {}).get("construct") or {}
    return construct.get("key") == "age" or item.code.lower() in ("age", "alter")


def tertile_bounds(values):
    """Two round bounds near the tertiles of the values (a step of 1, 2 or 5 times a power of ten, about a tenth of
    the range, but at most a quarter of the distance of the tertiles, so one outlier does not round both away), None
    when the values do not spread. Where both tertiles fall on one value (many zeros), that value is a class of its
    own: "unter 1" / "1 und mehr" for counts with most at 0 (audit 2026-10-06, E-6)."""
    import math

    xs = sorted(values)
    if len(xs) < 3 or xs[0] == xs[-1]:
        return None
    t1, t2 = (xs[min(int(q * len(xs)), len(xs) - 1)] for q in (1 / 3, 2 / 3))
    raw = (xs[-1] - xs[0]) / 10
    if t2 > t1:
        raw = min(raw, (t2 - t1) / 4)
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 5, 10) if m * power >= raw)
    bounds = []
    for t in ((t1, t2) if t2 > t1 else ()):
        b = round(round(t / step) * step, 10)
        b = int(b) if float(b).is_integer() else b
        if xs[0] < b <= xs[-1] and b not in bounds:
            bounds.append(b)
    if not bounds:  # the tertiles on one value (or rounded onto the ends): that value apart from the rest
        above = next(x for x in xs if x > t1) if t1 < xs[-1] else None
        whole = all(float(x).is_integer() for x in xs)
        for b in (t1, (t1 + 1 if whole else above) if above is not None else None):
            if b is not None:
                b = int(b) if float(b).is_integer() else b
                if xs[0] < b <= xs[-1] and b not in bounds:
                    bounds.append(b)
    return bounds or None


def _fmt(x, lang=None):
    """A bound in a class label: a decimal point in the languages that write numbers like English (also Arabic;
    Mathe-Audit 2026-10-06, Z-8), else a decimal comma."""
    from app.units import writes_like_english

    return str(x) if writes_like_english(lang or "de") else str(x).replace(".", ",")


def class_groups(bounds, lang=None):
    """[(code, label)] of the classes between the bounds: "unter 18", "18 bis unter 30", …, "60 und mehr"."""
    out = [("k0", tr(lang or "de", "unter {a}", "under {a}", a=_fmt(bounds[0], lang)))]
    for n, low in enumerate(bounds):
        high = bounds[n + 1] if n + 1 < len(bounds) else None
        out.append((f"k{n + 1}", tr(lang or "de", "{a} bis unter {b}", "{a} to under {b}", a=_fmt(low, lang), b=_fmt(high, lang))
                    if high is not None else tr(lang or "de", "{a} und mehr", "{a} and over", a=_fmt(low, lang))))
    return out


def class_of(bounds, value):
    """The class code of a value ("k0" below the first bound), None for no number."""
    try:
        x = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return None if x != x else f"k{sum(1 for b in bounds if x >= b)}"


class ClassVar(GroupVar):
    """A number split into classes; without saved bounds (and no age) they come from the data (prepare)."""

    def __init__(self, item, bounds, origin, lang):
        question = plain(item.text("question", lang)) or item.code
        self.lang = lang
        label = f"{item.code}: {question[:50]}" + (de_en(" (Grenzen {b})", " (bounds {b})", b="; ".join(str(b) for b in bounds))
                                                   if bounds else "")
        super().__init__(f"klassen:{item.id}", item.code, label, question, "classes",
                         class_groups(bounds, lang) if bounds else [], self._class, item=item)
        # "weiß nicht" (-98) and not shown (-77) are no numbers: in no class and not in the tertiles (audit 2026-10-06, E-1)
        from app.rows import NOT_SHOWN

        kind = getattr(item, "kind", None)
        self.gone = {str(m) for m in (kind.missing_codes(item, item.code) if kind is not None else ())} | {str(NOT_SHOWN)}
        self.bounds, self.origin = bounds, origin  # origin: "saved", "age" or "data"
        self.stamp = f"klassen:{item.id}:{bounds}"
        self.note = de_en("Zahlen in Klassen zu teilen kostet Information: Für einen Zusammenhang ist die Korrelation der "
                          "bessere Test (MacCallum et al., 2002).", "Splitting numbers into classes loses information: "
                          "for an association the correlation is the better test (MacCallum et al., 2002).")

    def _number(self, row):
        """The value of the row, None for no number or a missing code."""
        value = row.get(self.code)
        try:
            x = float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return None
        return None if x != x or (x.is_integer() and str(int(x)) in self.gone) else value

    def _class(self, row):
        return class_of(self.bounds, self._number(row)) if self.bounds else None

    def prepare(self, table):
        if self.bounds:
            return
        values = []
        for r in table:
            try:
                values.append(float(str(self._number(r)).replace(",", ".")))
            except (TypeError, ValueError):
                continue
        self.bounds = tertile_bounds([v for v in values if v == v]) or []
        self.groups = class_groups(self.bounds, self.lang) if self.bounds else []
        self.stamp = f"klassen:{self.item.id}:Daten"


def _classes(survey, lang=None):
    lang = lang or survey.default_language
    out = {}
    for item in number_items(survey):
        saved = saved_bounds(survey, item)
        if saved:
            var = ClassVar(item, saved, "saved", lang)
        else:
            var = ClassVar(item, AGE_BOUNDS, "age", lang) if _is_age(item) else ClassVar(item, None, "data", lang)
        out[var.id] = var
    return out


def _mode(survey, lang=None):
    """Online or on paper (app/paper.py), once the survey holds paper sheets: in a mixed-mode study the mode belongs in
    the analysis (de Leeuw, 2005)."""
    from app import export

    if not export.has_paper(survey):
        return {}
    label = de_en("Erhebungsweg (online / Papier)", "mode (online / paper)")
    groups = [("online", "online"), ("paper", tr(lang, "Papier", "paper") if lang else de_en("Papier", "paper"))]
    return {"mode": GroupVar("mode", "mode", label, label, "mode", groups, lambda r: _text(r.get("mode")))}


def all_of(survey, lang=None, parameters=True):
    """{key: GroupVar} of every way the results of the survey can be split by, in the order of SECTIONS; lang: the
    language of the group labels (a dashboard's); parameters: also link parameters (not on dashboards, E7)."""
    return {**_randomizers(survey, lang), **_questions(survey, lang), **_multiple(survey, lang),
            **_classes(survey, lang), **_quotas(survey, lang), **(_parameters(survey) if parameters else {}),
            **_mode(survey, lang)}


def section_name(key):
    return {"rand": de_en("Versuchsbedingungen", "Experimental conditions"), "items": de_en("Fragen", "Questions"),
            "mc": de_en("Mehrfachauswahl", "Multiple choice"), "classes": de_en("Zahlen in Klassen", "Numbers in classes"),
            "quotas": de_en("Quoten", "Quotas"), "params": de_en("Link-Parameter", "Link parameters"),
            "mode": de_en("Erhebungsweg", "Mode")}[key]


def choices(survey):
    """[(section name, [(key, label)])] for the choice, sections without an entry left out."""
    found = list(all_of(survey).values())
    return [(section_name(sec), [(g.id, g.label) for g in found if g.section == sec]) for sec in SECTIONS
            if any(g.section == sec for g in found)]


def get(survey, key):
    """The group variable of a key of the address, None for anything else (also an old or unknown key)."""
    return all_of(survey).get(str(key)) if key not in (None, "") else None


def _equal_shares(counts):
    """Chi-square of counts against equal shares (sample ratio mismatch), None below 2 per condition."""
    from app import group_tests as gt

    n, k = sum(counts), len(counts)
    if k < 2 or n < 2 * k:
        return None
    expected = n / k
    chi2 = sum((c - expected) ** 2 / expected for c in counts)
    p = gt.chi2_sf(chi2, k - 1)
    return {"chi2": chi2, "df": k - 1, "p": p, "n": n, "warn": p < SRM_ALPHA}


def started_by_condition(var, survey, sources=("real",)):
    """{condition: [started, completed]} of the participations of the pool's sources that reached the randomizer
    (also unfinished ones; the data pool's filters do not apply: the check is about the assignment); declined
    consent is no start."""
    from app import flow
    from app.extensions import db
    from app.models import Response

    node = var.node
    names = {flow.node_id(c): flow.child_label(survey, c) for c in node.get("children") or []}
    out = {c: [0, 0] for c, _ in var.groups}
    query = db.select(Response.status, Response.layout).filter(
        Response.survey_id == survey.id, Response.is_preview.is_(False), Response.source.in_(tuple(sources or ("real",))))
    for status, layout in db.session.execute(query):
        if status == Response.DECLINED:
            continue
        picked = flow.reached_conditions(layout).get(node.get("id")) or []
        found = out.get(names.get(picked[0])) if picked else None
        if found is not None:
            found[0] += 1
            found[1] += status == Response.COMPLETED
    return out


def assignment_check(var, survey, sources=("real",)):
    """Whether the assignment of a randomizer drawing one element looks sound (sample ratio mismatch, Fabijan et al.,
    2019), counted over everybody who reached it, whatever the page shows ("Nur zeigen" leaves it whole, audit
    2026-10-06, E-4). Decision 4 of that audit (E-3):
      completion  the share completed per condition among those who started it: chi-square test of independence
                  k x 2 (exact p where expected counts are small); drop-outs that depend on the condition show here.
                  A balanced randomizer gives each new person the least used condition, so the started are equal
                  by construction and only this check says something.
      ratio       a randomizer drawing by chance alone: also the started against equal shares (chi-square, df = k - 1),
                  where the multinomial distribution holds exactly; a fault in the assignment shows here.
    None where it does not apply (several elements drawn, no randomizer)."""
    from app import group_tests as gt

    node = var.node
    if node is None or (node.get("k") or 1) != 1 or len(var.groups) < 2:
        return None
    counts = started_by_condition(var, survey, sources)
    started = [counts[c][0] for c, _ in var.groups]
    done = [counts[c][1] for c, _ in var.groups]
    balanced = node.get("balanced", True)
    out = {"balanced": balanced, "ratio": None if balanced else _equal_shares(started), "completion": None,
           "rates": [{"label": label, "started": s, "done": d} for (_, label), s, d in zip(var.groups, started, done)]}
    if sum(started) >= 2 * len(started) and all(started):
        if sum(done) == sum(started):
            out["completion"] = {"all_done": True, "n": sum(started), "warn": False}
        else:
            test = gt.chi2_test([[d, s - d] for s, d in zip(started, done)])
            if test is not None:
                exact = test["cochran"] and test.get("exact_p") is not None
                p = test["exact_p"] if exact else test["p"]
                out["completion"] = {"chi2": test["chi2"], "df": test["df"], "n": test["n"], "p": p,
                                     "exact": test["exact"] if exact else None, "warn": p < SRM_ALPHA}
    if out["ratio"] is None and out["completion"] is None:
        return None
    out["warn"] = any((out[k] or {}).get("warn") for k in ("ratio", "completion"))
    return out
