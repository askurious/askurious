"""Side-by-side matrix: one list of rows (bus, train, bicycle ...) and up to five questions as columns next to each
other, each with its own kind of answer (concept side-by-side-konzept.md, decisions E1 to E15).

A column is one of
    single    one answer per row as radio buttons: 2 to 10 points with the answer scales of the Likert matrix or own
              labels ("ja"/"nein"); codes 1..n; "weiß nicht" possible (-98)
    multi     several answers per row (tick boxes); one 0/1 column per answer
    dropdown  one answer per row from a list; codes 1..n
    number    a whole or decimal number with an optional range and unit
    text      a short text

Settings (config): "rows" [{id, code, when?, src?}] as the statements of the Likert matrix (shown only to some,
app/rows.py; carried from an earlier choice, app/carry.py), "randomize", and "columns" [{id, code, kind, points,
anchors, labeling, level, required, dont_know, number, min, max, when?}]; "when" {"col": id of a choice column to
its left, "codes": [answers]} shows the column in a row only after one of these answers there (E10). Texts per language: "question", "rows" {row id:
text}, "titles" {column id: question of the column}, "labels" {column id: [label of each answer]}, "units" {column
id: unit after a number}.

The answer is {row id: {column id: value}} (and rows.HIDDEN: the rows not shown). The export names a value
<code>_<column>_<row>, column by column (E8): all rows of the first question, then all of the second. A row's code
left empty is its number, a column's is s1, s2, ...
"""

from app import carry, rows
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_dict, r_flag, r_id, r_int, r_list, r_maybe, r_num, r_one, r_rows, r_text  # rules for survey files, see base.py
from app.items.base import DONT_KNOW, DONT_KNOW_CODE, ID_RE, MAX_NUMBER, AnswerError, ItemType, in_lang, plain, prepared, read_cfg, whole
from app.items.base import new_id as _new_id
from app.lang import Both, de_en, english
from app.ui_i18n import tr

KINDS = ("single", "multi", "dropdown", "number", "text")
KIND_NAMES = {"single": ("Auswahl (eine Antwort)", "Choice (one answer)"),
              "multi": ("Mehrfachauswahl", "Multiple choice"),
              "dropdown": ("Dropdown-Liste", "Drop-down list"),
              "number": ("Zahl", "Number"),
              "text": ("Kurztext", "Short text")}
LEVELS = {"single": ("ordinal", "nominal", "metric"), "dropdown": ("nominal", "ordinal"), "multi": ("nominal",),
          "number": ("metric",), "text": ("nominal",)}
MAX_COLUMNS = 5   # E4: at most five questions side by side
MAX_ROWS = 40
WARN_COLUMNS = 4  # the pre-launch check warns from four columns, more than twelve answer fields per row, 15 rows (E4)
WARN_FIELDS = 12
WARN_ROWS = 15
SINGLE_POINTS = tuple(range(2, 11))
LIST_POINTS = tuple(range(2, 21))  # answers of a drop-down list or a multiple choice column
MAX_TEXT = 200
PART_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,30}\Z")  # the part of a variable name a row or column adds
r_when_col = r_maybe(r_dict(col=r_id, codes=r_list(LIST_POINTS[-1], r_int(1, LIST_POINTS[-1]), low=1)))


def column_defaults(kind="single"):
    return {"id": _new_id(), "code": "", "kind": kind, "points": 5 if kind == "single" else 3,
            "anchors": "agree" if kind == "single" else "custom", "labeling": "full", "level": LEVELS[kind][0],
            "required": False, "dont_know": False, "number": "integer", "min": None, "max": None}


def kind_names():
    return [(k, KIND_NAMES[k][1] if english() else KIND_NAMES[k][0]) for k in KINDS]


def has_labels(col):
    return col.get("kind") in ("single", "multi", "dropdown")


def points_of(col):
    allowed = SINGLE_POINTS if col.get("kind") == "single" else LIST_POINTS
    p = col.get("points")
    return p if isinstance(p, int) and p in allowed else (5 if col.get("kind") == "single" else 3)


def column_code(index, col):
    return col.get("code") or f"s{index + 1}"


def row_code(index, row):
    return row.get("code") or str(index + 1)


def cell_applies(col, cells):
    """Whether a column is asked in a row with these answers so far (E10): always without a condition, otherwise once
    the column it depends on has one of its answers. A column whose own condition fails has no value, so a chain of
    conditions falls with its first link."""
    when = col.get("when")
    if not when:
        return True
    return (cells or {}).get(when.get("col")) in (when.get("codes") or [])


def clean_when(col, earlier):
    """The condition of a column as stored: only on a choice or drop-down column to its left (earlier: the columns
    before it) and only with answers that column has; None otherwise."""
    when = col.get("when")
    if not isinstance(when, dict):
        return None
    src = next((c for c in earlier if c.get("id") == when.get("col") and c.get("kind") in ("single", "dropdown")), None)
    if src is None:
        return None
    codes = sorted({n for n in when.get("codes") or [] if isinstance(n, int) and 1 <= n <= points_of(src)})
    return {"col": src["id"], "codes": codes} if codes else None


@register
class SideBySide(ItemType):
    key = "side_by_side"
    label = Both("Side-by-Side-Matrix", "Side-by-side matrix")
    # pre-test 2026-10-20, 24: say what rows and columns are, with an example
    short = Both("Mehrere Fragen nebeneinander je Zeile", "Several questions side by side per row")
    description = Both("Eine Tabelle: Die Zeilen sind, was bewertet wird; jede Spalte ist eine eigene Frage dazu mit eigener "
                       "Antwortart, z. B. „Wie wichtig?“ und „Wie zufrieden?“ nebeneinander",
                       "A table: the rows are what is rated; each column is a question of its own about them with its own "
                       "kind of answer, e.g. “How important?” and “How satisfied?” side by side")
    order = 32
    fact_sheet = FactSheet(
        "scales",
        keywords=["Side by side", "Matrix", "Grid", "Mehrere Spalten", "Several columns", "Tabelle", "Table"],
        can=[("Zeilen (z. B. Verkehrsmittel) mit bis zu fünf Fragen als Spalten nebeneinander.",
              "Rows (e.g. means of transport) with up to five questions as columns side by side."),
             ("Jede Spalte mit eigener Antwortart: Auswahl auf einer Antwortskala oder mit eigenen Antworten, Mehrfachauswahl, Dropdown-Liste, Zahl mit Einheit und Bereich, Kurztext.",
              "Each column with its own kind of answer: choice on an answer scale or with answers of your own, multiple choice, drop-down list, number with unit and range, short text."),
             ("Pflicht je Spalte, „weiß nicht“ je Auswahl-Spalte, Messniveau je Spalte.",
              "Required per column, “don’t know” per choice column, measurement level per column."),
             ("Am Rechner eine Tabelle, am Handy eine Karte je Zeile.",
              "A table on a computer, a card per row on a phone."),
             ("Zeilen in Zufallsreihenfolge, nur manchen zeigen oder aus einer früheren Frage übernehmen.",
              "Rows in random order, shown only to some or carried over from an earlier question.")],
        good_for=[("Dieselben Fragen zu mehreren Dingen: Nutzung, Häufigkeit und Bewertung je Produkt",
                   "The same questions about several things: use, frequency and evaluation per product")],
        not_for=[("Mehrere Aussagen auf einer einzigen Skala (dann Likert-Skala)",
                  "Several statements on one single scale (then Likert scale)")],
        designs=["cross", "perception"],
        examples=[("Je Verkehrsmittel: genutzt (ja/nein), wie oft, wie zufrieden",
                   "Per means of transport: used (yes/no), how often, how satisfied")],
    )
    result_kind = "auto"
    uses_answers = True  # which rows a person sees can depend on earlier answers (config "when", app/rows.py)
    levels = None  # per column (column_level)
    MAX_TEXT = MAX_TEXT
    import_rules = {
        "rows": r_rows(MAX_ROWS, id=r_id, code=r_text(30), when=rows.r_when, src=carry.r_src),
        "columns": r_rows(MAX_COLUMNS, id=r_id, code=r_text(30), kind=r_one(*KINDS), points=r_int(2, LIST_POINTS[-1]),
                          anchors=r_text(20), labeling=r_one("full", "endpoints"),
                          level=r_one("nominal", "ordinal", "metric"), required=r_flag, dont_know=r_flag,
                          number=r_one("integer", "decimal"), min=r_maybe(r_num(-MAX_NUMBER, MAX_NUMBER, none=True)),
                          max=r_maybe(r_num(-MAX_NUMBER, MAX_NUMBER, none=True)), when=r_when_col),
        "randomize": r_flag, "carry": carry.r_carry,
    }

    # --- settings -----------------------------------------------------------------------------------------------

    def default_config(self):
        first, second = column_defaults("single"), column_defaults("single")
        first.update(points=2, anchors="custom", level="nominal")
        return {"rows": [{"id": _new_id(), "code": ""}], "columns": [first, second], "randomize": False}

    def default_texts(self, lang):
        return {"question": "", "rows": {}, "titles": {}, "labels": {}, "units": {}}

    def columns(self, item):
        return [c for c in read_cfg(item).get("columns", []) if c.get("kind") in KINDS]

    def column(self, item, cid):
        return next((c for c in self.columns(item) if c["id"] == cid), None)

    def labels(self, item, col, lang):
        """The labels of the answers of a column in `lang`, as many as it has points; with "nur Endpunkte" the ones in
        between are empty."""
        from app.items.likert import anchor_labels, shown_labels

        n = points_of(col)
        found = list((item.t(lang).get("labels") or {}).get(col["id"]) or [])
        if not found and col.get("kind") == "single" and col.get("anchors") not in (None, "custom"):
            found = anchor_labels(lang or "de", n, col["anchors"])
        found = (found + [""] * n)[:n]
        return shown_labels(found, col.get("labeling")) if col.get("kind") == "single" else found

    def title(self, item, col, lang=None):
        return (item.t(lang).get("titles") or {}).get(col["id"], "")

    def when_text(self, item, col, lang=None, ui=None):
        """The condition of a column in words: „Genutzt?“ = ja (several answers with " / "); the quotation marks of
        the reader's language ui ("de"/"en"; None: the page's), “Used?” = yes on an English page (X86)."""
        from app.items.base import plain
        from app.lang import english

        when = col.get("when") or {}
        src = self.column(item, when.get("col"))
        if src is None:
            return ""
        labels = self.labels(item, src, lang)
        said = " / ".join(plain(labels[n - 1]) or str(n) if n <= len(labels) else str(n) for n in when.get("codes") or [])
        title = plain(self.title(item, src, lang))
        en = ui == "en" if ui is not None else english()
        return f"“{title}” = {said}" if en else f"„{title}“ = {said}"

    def unit(self, item, col, lang=None):
        return (item.t(lang).get("units") or {}).get(col["id"], "")

    def anchor_names(self):
        from app.items.likert import Likert

        return Likert().anchor_names()

    def anchor_presets(self, lang):
        from app.items.likert import Likert

        return Likert().anchor_presets(lang)

    def kind_names(self):
        return kind_names()

    def level_names(self, col):
        from app.items.base import level_labels

        names = level_labels()
        return [(lv, names[lv]) for lv in LEVELS[col.get("kind", "single")]]

    def preview_empty(self, item):
        if any(c.get("id") for c in self.columns(item)) and read_cfg(item).get("rows"):
            texts = item.text("rows") or {}
            if any(texts.get(r["id"]) for r in read_cfg(item).get("rows", [])):
                return None
        return de_en("Die Vorschau erscheint, sobald die erste Zeile eingetragen ist.",
                     "The preview appears as soon as the first row is entered."), None

    def update_from_form(self, item, form, lang, structural):
        config = dict(item.config or {})
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt["question"] = form.get("question", "").strip()
        row_texts = dict(lt.get("rows") or {})
        titles, labels, units = dict(lt.get("titles") or {}), dict(lt.get("labels") or {}), dict(lt.get("units") or {})
        before_cols = {c["id"]: c for c in config.get("columns", [])}
        relabelled = set()  # columns whose labels follow a new number of points or answer scale

        ids, codes, values = form.getlist("row_id"), form.getlist("row_code"), form.getlist("row_text")
        if structural:
            before = {r["id"]: r for r in config.get("rows", [])}
            new_rows = [r for r in config.get("rows", []) if r.get("src")]  # carried rows stay as carry.sync wrote them
            for rid, code, text in zip(ids, codes, values):
                if not (code.strip() or text.strip()):
                    continue
                rid = rid if rid in before or ID_RE.match(rid or "") else _new_id()  # ids end up in selectors and field names
                new_rows.append({"id": rid, "code": code.strip()})
                row_texts[rid] = text.strip()
            rows.from_form(item, [r for r in new_rows if not r.get("src")], form, before, lang)
            config["rows"] = new_rows[:MAX_ROWS]
            config["randomize"] = bool(form.get("randomize"))

            columns = []
            for cid in form.getlist("col_id")[:MAX_COLUMNS + 1]:
                title = form.get(f"col_title_{cid}", "").strip()
                code = form.get(f"col_code_{cid}", "").strip()
                if cid not in before_cols and not (title or code):
                    continue  # the empty card for a new column
                if cid not in before_cols and not ID_RE.match(cid):
                    continue  # ids end up in selectors and field names (the editor makes them with new_id)
                if form.get(f"col_remove_{cid}"):
                    continue
                kind = form.get(f"col_kind_{cid}", "single")
                kind = kind if kind in KINDS else "single"
                old = before_cols.get(cid) or {}
                col = {**column_defaults(kind), **{k: v for k, v in old.items() if k != "kind"}, "id": cid, "code": code, "kind": kind}
                allowed = SINGLE_POINTS if kind == "single" else LIST_POINTS
                points = whole(form.get(f"col_points_{cid}", "")) or col.get("points")
                points = points if points in allowed else (5 if kind == "single" else 3)
                anchors = form.get(f"col_anchors_{cid}", col.get("anchors") or "custom")
                from app.items.likert import ANCHOR_CHOICES

                anchors = anchors if kind == "single" and anchors in dict(ANCHOR_CHOICES) else "custom"
                level = form.get(f"col_level_{cid}", col.get("level"))
                col.update(points=points, anchors=anchors,
                           labeling="endpoints" if kind == "single" and form.get(f"col_labeling_{cid}") == "endpoints" else "full",
                           level=level if level in LEVELS[kind] else LEVELS[kind][0],
                           required=bool(form.get(f"col_required_{cid}")),
                           dont_know=kind == "single" and bool(form.get(f"col_dk_{cid}")),
                           number="decimal" if form.get(f"col_number_{cid}") == "decimal" else "integer")
                for bound in ("min", "max"):
                    raw = form.get(f"col_{bound}_{cid}", "").strip()
                    try:
                        from app.items.text import _num

                        col[bound] = _num(raw, lang) if raw else None
                    except ValueError:
                        col[bound] = old.get(bound)
                if kind != "number":
                    col["min"] = col["max"] = None
                picked = [v.split(":", 1) for v in form.getlist(f"col_when_{cid}") if ":" in v]
                src = picked[0][0] if picked else None  # one column it depends on: the first one ticked
                col["when"] = {"col": src, "codes": [whole(code) for c, code in picked if c == src]} if src else None
                col["when"] = clean_when(col, columns)
                if not col["when"]:
                    col.pop("when")
                # a new number of points or answer scale: the labels of every language follow (as in the Likert matrix)
                changed = bool(old) and (points != old.get("points") or anchors != old.get("anchors") or kind != old.get("kind"))
                sent = form.getlist(f"col_label_{cid}")
                if has_labels(col) and (changed or (not old and len(sent) != points)):
                    labels[cid] = self._relabel(texts, lang, col, labels.get(cid))
                    relabelled.add(cid)
                columns.append(col)
                titles[cid] = title
            config["columns"] = columns[:MAX_COLUMNS]
            gone = set(before_cols) - {c["id"] for c in config["columns"]}
            for group in (titles, labels, units):
                for cid in gone:
                    group.pop(cid, None)
        else:
            known = {r["id"] for r in config.get("rows", [])}
            for rid, text in zip(ids, values):
                if rid in known:
                    row_texts[rid] = text.strip()
            for c in config.get("columns", []):
                if f"col_title_{c['id']}" in form:
                    titles[c["id"]] = form.get(f"col_title_{c['id']}", "").strip()

        for c in config.get("columns", []):
            got = [v.strip() for v in form.getlist(f"col_label_{c['id']}")]
            if c["id"] not in relabelled and got and len(got) == points_of(c):
                labels[c["id"]] = got
            if f"col_unit_{c['id']}" in form:
                units[c["id"]] = form.get(f"col_unit_{c['id']}", "").strip()[:30]
        lt.update(rows=row_texts, titles=titles, labels=labels, units=units)
        texts[lang] = lt
        item.config, item.texts = config, texts
        if structural:
            carry.from_form(item, form)
        carry.sync(item)

    def _relabel(self, texts, lang, col, current):
        """New labels for a column whose number of points, answer scale or kind changed: the standard labels of the
        answer scale in every language, or own labels resized (both ends stay). Writes the other languages into
        `texts` and returns those of `lang` (current: its labels so far)."""
        from app.items.likert import anchor_labels, resize_labels

        n = points_of(col)

        def fitted(code_lang, labels):
            if col["kind"] == "single" and col.get("anchors") not in (None, "custom"):
                return anchor_labels(code_lang, n, col["anchors"])
            return resize_labels(labels, n) if labels else [""] * n

        for code_lang, per in list(texts.items()):
            if code_lang != lang and isinstance(per, dict):
                found = (per.get("labels") or {}).get(col["id"])
                texts[code_lang] = {**per, "labels": {**(per.get("labels") or {}), col["id"]: fitted(code_lang, found)}}
        return fitted(lang, current)

    def carry_view(self, item, lang):
        return carry.editor_view(item, lang)

    def row_conditions(self, item, lang):
        """The editor's part "Zeilen nur manchen zeigen" (app/rows.py)."""
        from app.items.base import plain

        texts = item.text("rows", lang) or {}
        return rows.editor_view(item, [(r, plain(texts.get(r["id"], "")), f"{item.code}_…_{row_code(i, r)}")
                                       for i, r in enumerate(read_cfg(item).get("rows", [])) if not r.get("src")], lang)

    def remap_config(self, config, twin):
        config = carry.remap(self.key, config, twin)
        if not any(r.get("when") for r in config.get("rows", [])):
            return config
        return {**config, "rows": rows.remap(config["rows"], twin)}

    def answer_shape(self, item):
        """Codes of rows and columns, and per column its kind, points and number type: once real answers exist they
        stay as they are (ItemType.answer_shape)."""
        shape = super().answer_shape(item)
        shape["scale"] = {"columns": [(c["id"], c.get("kind"), points_of(c) if has_labels(c) else c.get("number"))
                                      for c in self.columns(item)]}
        return shape

    def read_shape(self, item):
        """Per column "weiß nicht" and its condition: switched off, -98 would count as a value; a condition set or
        changed later would export answered cells as -77 "nicht gefragt", as the condition is read from the answer
        and not stored per cell (audit 2026-10-10, N-3; decision E10 of the side-by-side matrix)."""
        out = {}
        for c in self.columns(item):
            out[f"dont_know|{c['id']}"] = bool(c.get("dont_know"))
            out[f"when|{c['id']}"] = c.get("when") or None
        return out

    # --- check ----------------------------------------------------------------------------------------------------

    def validate(self, item):
        problems = []
        c = read_cfg(item)
        all_rows, cols = c.get("rows", []), self.columns(item)
        if not all_rows:
            problems.append(de_en("mindestens eine Zeile", "at least one row"))
        if not cols:
            problems.append(de_en("mindestens eine Spalte", "at least one column"))
        rtexts = item.text("rows") or {}
        if any(not rtexts.get(r["id"]) for r in all_rows):
            problems.append(de_en("Zeile ohne Text", "row without text"))
        titles = item.text("titles") or {}
        if len(cols) > 1 and any(not titles.get(col["id"]) for col in cols):
            problems.append(de_en("Spalte ohne Frage", "column without a question"))
        for what, found, name in ((de_en("Zeile", "row"), all_rows, row_code), (de_en("Spalte", "column"), cols, column_code)):
            parts = [name(i, x) for i, x in enumerate(found)]
            if len(set(parts)) != len(parts):
                problems.append(de_en("Codes der {what}n sind nicht eindeutig", "the codes of the {what}s are not unique", what=what))
            bad = [p for p in parts if not PART_RE.match(p)]
            if bad:
                problems.append(de_en("Code „{bad}“ einer {what}: nur Buchstaben, Ziffern und _", "code “{bad}” of a {what}: letters, digits and _ only",
                                      bad=bad[0], what=what))
        for col in cols:
            if col.get("kind") in ("multi", "dropdown") and not any(self.labels(item, col, None)):
                problems.append(de_en("Spalte „{t}“ hat keine Antworten", "column “{t}” has no answers", t=self.title(item, col) or col["id"]))
            if col.get("kind") == "number" and col.get("min") is not None and col.get("max") is not None and col["min"] > col["max"]:
                problems.append(de_en("Spalte „{t}“: Minimum größer als Maximum", "column “{t}”: minimum above maximum", t=self.title(item, col)))
        for i, col in enumerate(cols):
            if col.get("when") and clean_when(col, cols[:i]) != col["when"]:
                problems.append(de_en("Spalte „{t}“: Die Bedingung braucht eine Auswahl-Spalte links davon",
                                      "column “{t}”: the condition needs a choice column to its left", t=self.title(item, col) or col["id"]))
        names = [n for n, _ in self.export_columns(item)]
        if len(set(names)) != len(names):
            problems.append(de_en("Variablennamen der Zellen sind nicht eindeutig", "the variable names of the cells are not unique"))
        return problems + rows.problems(item, all_rows, lambda i, r: row_code(i, r), "Zeile") + carry.problems(item)

    def check_warnings(self, item):
        """E4: hints that do not block the start."""
        out = super().check_warnings(item)
        cols = self.columns(item)
        fields = sum(points_of(c) + (1 if c.get("dont_know") else 0) if c.get("kind") in ("single", "multi") else 1 for c in cols)
        if len(cols) >= WARN_COLUMNS:
            out.append(de_en("{code}: {n} Spalten nebeneinander; je mehr Fragen in einer Matrix, desto mehr wird ausgelassen oder durchgeklickt (Couper et al., 2013)",
                             "{code}: {n} columns side by side; the more questions in one grid, the more is skipped or clicked through (Couper et al., 2013)",
                             code=item.code, n=len(cols)))
        if fields > WARN_FIELDS:
            out.append(de_en("{code}: {n} Antwortfelder je Zeile; am Rechner wird die Tabelle breit, am Handy die Karte lang",
                             "{code}: {n} answer fields per row; on a computer the table gets wide, on a phone the card gets long",
                             code=item.code, n=fields))
        if len(read_cfg(item).get("rows", [])) >= WARN_ROWS:
            out.append(de_en("{code}: {n} Zeilen; lange Matrizen ermüden, eine Aufteilung auf zwei Seiten hilft",
                             "{code}: {n} rows; long grids tire people out, splitting them across two pages helps",
                             code=item.code, n=len(read_cfg(item)["rows"])))
        return out

    # --- page -----------------------------------------------------------------------------------------------------

    def statements(self, item, lang, order=None, answers=None):
        """The rows this person sees, in the order shown (as Likert.statements)."""
        rtexts = item.t(lang).get("rows", {})
        all_rows = read_cfg(item).get("rows", [])
        gone = rows.hidden(all_rows, answers, item.survey)
        found = [{**r, "text": carry.label(r, answers, rtexts.get(r["id"], "")), "export_code": row_code(i, r)}
                 for i, r in enumerate(all_rows) if r["id"] not in gone]
        if order:
            rank = {rid: n for n, rid in enumerate(order)}
            found.sort(key=lambda r: rank.get(r["id"], len(rank)))
        return found

    def order_codes(self, item):
        if not read_cfg(item).get("randomize"):
            return None
        return {r["id"]: f"{item.code}_{row_code(i, r)}" for i, r in enumerate(read_cfg(item).get("rows", []))}

    def layout(self, item, lang):
        """What the page needs per column: kind, points, labels, title, unit, and the width the table takes on a
        computer (below it the rows show as cards, E6)."""
        out, width = [], 160
        for i, col in enumerate(self.columns(item)):
            kind = col["kind"]
            labels = self.labels(item, col, lang) if has_labels(col) else []
            n = points_of(col) if kind in ("single", "multi") else 0
            dk = kind == "single" and bool(col.get("dont_know"))
            # 64 px a point: room for a word like "überhaupt" without breaking it in the middle (X50)
            cw = {"single": 64 * (n + (1 if dk else 0)), "multi": 64 * n, "dropdown": 190, "number": 140, "text": 190}[kind]
            width += max(cw, 100) + 20
            out.append({"col": col, "id": col["id"], "kind": kind, "labels": labels, "title": self.title(item, col, lang),
                        "unit": self.unit(item, col, lang), "points": n, "dk": dk, "width": max(cw, 100),
                        "ends": kind == "single" and col.get("labeling") == "endpoints",
                        "required": bool(col.get("required")), "decimal": col.get("number") == "decimal",
                        "when": col.get("when")})
        return {"columns": out, "width": width}

    def applies(self, item, answers):
        all_rows = read_cfg(item).get("rows", [])
        return (not all_rows or len(rows.hidden(all_rows, answers, item.survey)) < len(all_rows)) and carry.applies(item, answers)

    def one_click(self, item):
        return False  # several columns: the page waits for "Next"

    def _cell(self, item, col, raw, lang):
        """The stored value of one cell from the form (raw: a list of the sent values), None if empty; raises
        AnswerError for a value that is no answer of the column."""
        kind = col["kind"]
        values = [v for v in raw if v not in (None, "")]
        if not values:
            return None
        if kind in ("single", "dropdown"):
            v = values[0]
            if v == DONT_KNOW and col.get("dont_know"):
                return DONT_KNOW
            n = whole(v)
            if n is None or not 1 <= n <= points_of(col):
                raise AnswerError(tr(lang, "Ungültige Antwort.", "Invalid answer."))
            return n
        if kind == "multi":
            picked = sorted({whole(v) for v in values if whole(v) is not None and 1 <= whole(v) <= points_of(col)})
            return picked or None
        if kind == "number":
            from app.items.text import _num

            try:
                number = _num(values[0].strip(), lang)
            except ValueError:
                raise AnswerError(tr(lang, "Bitte geben Sie eine Zahl ein.", "Please enter a number.")) from None
            if col.get("number") != "decimal" and not number.is_integer():
                raise AnswerError(tr(lang, "Bitte geben Sie eine ganze Zahl ein.", "Please enter a whole number."))
            lo, hi = col.get("min"), col.get("max")
            if (lo is not None and number < lo) or (hi is not None and number > hi):
                raise AnswerError(self.range_hint(col, lang))
            return int(number) if col.get("number") != "decimal" else number
        text = values[0].replace("\r\n", "\n").strip()
        if len(text) > MAX_TEXT:
            raise AnswerError(tr(lang, "Die Antwort ist zu lang.", "The answer is too long."))
        return text or None

    def range_hint(self, col, lang):
        from app.items.base import number_text

        lo, hi = col.get("min"), col.get("max")
        if lo is not None and hi is not None:
            return tr(lang, "Bitte geben Sie eine Zahl von {lo} bis {hi} ein.", "Please enter a number from {lo} to {hi}.",
                      lo=number_text(lo, lang), hi=number_text(hi, lang))
        if lo is not None:
            return tr(lang, "Bitte geben Sie eine Zahl ab {lo} ein.", "Please enter a number of at least {lo}.", lo=number_text(lo, lang))
        return tr(lang, "Bitte geben Sie eine Zahl bis {hi} ein.", "Please enter a number of at most {hi}.", hi=number_text(hi, lang))

    def field(self, item, row, col):
        return f"i{item.id}_{row['id']}_{col['id']}"

    def parse_answer(self, item, form, lang="de", meta=None, answers=None):
        all_rows = read_cfg(item).get("rows", [])
        gone = rows.hidden(all_rows, answers, item.survey)
        answer, error, bad, open_ = {}, None, None, {}
        if gone:
            answer[rows.HIDDEN] = sorted(gone)
        cols = self.columns(item)
        for row in all_rows:
            if row["id"] in gone:
                continue
            cells = {}
            for col in cols:
                if not cell_applies(col, cells):
                    continue  # not asked in this row (E10): an answer sent anyway (no script) is dropped
                try:
                    value = self._cell(item, col, form.getlist(self.field(item, row, col)), lang)
                except AnswerError as e:
                    if error is None:
                        error, bad = e, (row["id"], col["id"])
                    continue  # a value that is no answer is not open as well: the cell says what is wrong (X35)
                if value is not None:
                    cells[col["id"]] = value
                elif col.get("required") or item.required:
                    open_.setdefault(col["id"], []).append(row["id"])
            if cells:
                # a row answered in some column: a multiple choice column asked there without a tick is "nothing
                # ticked" (0 in the export), as in the plain multiple choice; empty stays for a row left out
                # altogether (audit 2026-10-10, N-5)
                for col in cols:
                    if col["kind"] == "multi" and col["id"] not in cells and cell_applies(col, cells):
                        cells[col["id"]] = []
                answer[row["id"]] = cells
        missing = sum(len(v) for v in open_.values())
        if error is None and not missing:
            return answer
        names = {r["id"]: plain(r["text"]) for r in self.statements(item, lang, answers=answers)}
        said = []
        if error is not None:
            col = self.column(item, bad[1])
            said.append(f"{error} ({self.where(item, col, [bad[0]], names, lang)})")
        if missing and (item.required or any(c.get("required") for c in cols)):
            where = ", ".join(self.where(item, self.column(item, cid), rids, names, lang) for cid, rids in open_.items())
            said.append(tr(lang, "Bitte füllen Sie alle Pflichtfelder aus (noch offen: {n} – {where}).",
                           "Please fill in all required fields ({n} still open – {where}).", n=missing, where=where))
        raise AnswerError(" ".join(said))

    def where(self, item, col, row_ids, names, lang):
        """Which cells a message means, in words: „Genutzt?“ bei Bus, or „Genutzt?“ in 3 Zeilen (X35)."""
        title = plain(self.title(item, col, lang))
        if len(row_ids) == 1:
            return tr(lang, "„{col}“ bei {row}", "“{col}” for {row}", col=title, row=names.get(row_ids[0], ""))
        return tr(lang, "„{col}“ in {n} Zeilen", "“{col}” in {n} rows", col=title, n=len(row_ids))

    def cell_marks(self, item, cells, lang, every=False):
        """{column id: message} of the cells of one row the page marks after a failed "Next" (X35): a value that is
        no answer with what is wrong, a required cell left empty with "" (`every`, the nudge: every empty cell that
        is asked). A cell whose condition does not hold is never marked."""
        out = {}
        cells = cells if isinstance(cells, dict) else {}
        for col in self.columns(item):
            if not cell_applies(col, cells):
                continue
            v = cells.get(col["id"])
            if v in (None, "", []):
                if every or col.get("required") or item.required:
                    out[col["id"]] = ""
            elif col["kind"] in ("number", "text") and isinstance(v, str):  # as sent (display_value): check it again
                try:
                    self._cell(item, col, [v], lang)
                except AnswerError as e:
                    out[col["id"]] = str(e)
        return out

    def display_value(self, item, form, meta=None):
        out = {}
        for row in read_cfg(item).get("rows", []):
            cells = {}
            for col in self.columns(item):
                raw = [v for v in form.getlist(self.field(item, row, col)) if v not in (None, "")]
                if not raw:
                    continue
                if col["kind"] == "multi":
                    cells[col["id"]] = [whole(v) for v in raw if whole(v) is not None]
                elif col["kind"] in ("single", "dropdown"):
                    cells[col["id"]] = DONT_KNOW if raw[0] == DONT_KNOW else whole(raw[0])
                else:
                    cells[col["id"]] = raw[0]
            if cells:
                out[row["id"]] = cells
        return out

    def open_cells(self, item, answer):
        """{row id: [column ids]} of required cells left empty in a stored answer (the page marks these rows)."""
        out = {}
        if not isinstance(answer, dict):
            return out
        gone = rows.hidden_in(answer)
        for row in read_cfg(item).get("rows", []):
            if row["id"] in gone:
                continue
            cells = answer.get(row["id"]) or {}
            empty = [c["id"] for c in self.columns(item) if (c.get("required") or item.required) and cell_applies(c, cells)
                     and cells.get(c["id"]) in (None, "", [])]
            if empty:
                out[row["id"]] = empty
        return out

    def is_missing(self, item, answer):
        if not isinstance(answer, dict):
            return True
        gone = rows.hidden_in(answer)
        def open_in(cells):
            return not cells or any(cell_applies(c, cells) and cells.get(c["id"]) in (None, "", []) for c in self.columns(item))

        return any(open_in(answer.get(r["id"]) or {}) for r in read_cfg(item).get("rows", []) if r["id"] not in gone)

    # --- export ---------------------------------------------------------------------------------------------------

    def _cells(self, item):
        """[(export column, column, row, answer number or None)] in the order of the export: column by column. Built
        once per element while an export plan is open (exports and results.compute; code review 2026-10-15, A9)."""
        return prepared(item, "sbs_cells", lambda: self._list_cells(item))

    def _list_cells(self, item):
        out = []
        all_rows = read_cfg(item).get("rows", [])
        for ci, col in enumerate(self.columns(item)):
            for ri, row in enumerate(all_rows):
                base = f"{item.code}_{column_code(ci, col)}_{row_code(ri, row)}"
                if col["kind"] == "multi":
                    out.extend((f"{base}_{k}", col, row, k) for k in range(1, points_of(col) + 1))
                else:
                    out.append((base, col, row, None))
        return out

    def _cell_of(self, item, column):
        """(column, row, answer number) of an export column: looked up in a dict built once per element while an
        export plan is open (exports and results.compute); a linear search per column made up to 4000 columns
        quadratic (code review 2026-10-15, A9)."""
        index = prepared(item, "sbs_cell_of", lambda: {name: (col, row, k) for name, col, row, k in self._cells(item)})
        return index.get(column, (None, None, None))

    def open_columns(self, item):
        return [c for c, _ in self.export_columns(item) if self.lists_texts(item, c)]  # the columns "Kurztext"

    def export_columns(self, item):
        from app.items.base import plain

        lang = None
        rtexts = item.text("rows") or {}
        out = []
        for name, col, row, k in self._cells(item):
            label = f"{plain(rtexts.get(row['id'], ''))} – {plain(self.title(item, col, lang))}"
            if k is not None:
                label += f": {plain(self.labels(item, col, lang)[k - 1])}"
            if row.get("when"):
                label += f" [nur gezeigt, wenn {rows.text(item, row)}; {rows.NOT_SHOWN} = nicht gezeigt]"
            if col.get("when"):
                label += f" [nur gefragt, wenn {self.when_text(item, col, lang, ui='de')}; {rows.NOT_SHOWN} = nicht gefragt]"
            out.append((name, label))
        return out

    def export_values(self, item, answer):
        gone = rows.hidden_in(answer)
        out = {}
        for name, col, row, k in self._cells(item):
            if answer is None:
                out[name] = None
                continue
            if row["id"] in gone:
                out[name] = rows.NOT_SHOWN
                continue
            cells = ((answer or {}).get(row["id"]) or {}) if isinstance(answer, dict) else {}
            if not cell_applies(col, cells):
                out[name] = rows.NOT_SHOWN  # the column is not asked in this row (E10)
                continue
            v = cells.get(col["id"])
            if k is not None:  # a multiple choice column: 0/1 per answer, empty where the row was left out
                out[name] = None if v is None else int(isinstance(v, list) and k in v)
            elif v == DONT_KNOW:
                out[name] = DONT_KNOW_CODE
            else:
                out[name] = v
        return out

    def column_level(self, item, column):
        col, _, k = self._cell_of(item, column)
        if col is None:
            return None
        if k is not None or col["kind"] == "text":
            return "nominal"
        return col.get("level") if col.get("level") in LEVELS[col["kind"]] else LEVELS[col["kind"]][0]

    def missing_codes(self, item, column):
        col, row, k = self._cell_of(item, column)
        if col is None:
            return ()
        found = (rows.NOT_SHOWN,) if rows.partial(row) or col.get("when") else ()
        return found + ((DONT_KNOW_CODE,) if col.get("dont_know") and col["kind"] == "single" else ())

    def lists_texts(self, item, column):
        col, _, _ = self._cell_of(item, column)
        return col is not None and col["kind"] == "text"

    def result_column(self, item, column):
        """Results page: answers with their labels in the order of the column; numbers as numbers, texts listed."""
        col, _, k = self._cell_of(item, column)
        if col is None:
            return None, "", None
        lang = item.survey.default_language
        if k is not None:
            return "categorical", "", {"0": tr(lang, "nicht angekreuzt", "not ticked"), "1": tr(lang, "angekreuzt", "ticked")}
        if col["kind"] in ("single", "dropdown"):
            found = {str(n): label or str(n) for n, label in enumerate(self.labels(item, col, lang), start=1)}
            if col.get("dont_know"):
                found[str(DONT_KNOW_CODE)] = tr(lang, "weiß nicht", "don't know")
            return ("categorical" if self.column_level(item, column) == "nominal" else "auto"), "", found
        if col["kind"] == "number":
            return "auto", self.unit(item, col, lang), None
        return "auto", "", None

    def is_ratio(self, item):
        return False

    def codebook_info(self, item, column, lang, ui=None):
        """(type, value labels, notes) of one cell: value labels in the survey language `lang`, notes in the reader's
        language `ui`. The type is a key of export.TYPES_EN."""
        col, row, k = self._cell_of(item, column)
        if col is None:
            return None
        notes, values, missing = [], "", []
        if rows.partial(row):
            missing.append(in_lang(ui, "{n} = nicht gezeigt", "{n} = not shown", n=rows.NOT_SHOWN))
            if row.get("when"):
                notes.append(in_lang(ui, "nur gezeigt, wenn {cond}; {n} zählt als fehlend, leer = gezeigt und nicht beantwortet",
                                     "shown only if {cond}; {n} counts as missing, empty = shown and not answered",
                                     cond=rows.text(item, row, lang, ui), n=rows.NOT_SHOWN))
            else:
                notes.append(in_lang(ui, "aus einer früheren Frage übernommene Zeile; {n} = nicht gezeigt, zählt als fehlend",
                                     "row carried from an earlier question; {n} = not shown, counts as missing", n=rows.NOT_SHOWN))
        if col.get("when"):
            if not missing:
                missing.append(in_lang(ui, "{n} = nicht gezeigt", "{n} = not shown", n=rows.NOT_SHOWN))
            notes.append(in_lang(ui, "nur gefragt, wenn {cond}; {n} = nicht gefragt, zählt als fehlend",
                                 "asked only if {cond}; {n} = not asked, counts as missing",
                                 cond=self.when_text(item, col, lang, ui=ui), n=rows.NOT_SHOWN))
        if k is not None:
            typ = "Auswahl"
            values = "; ".join([in_lang(ui, "0 = nicht angekreuzt", "0 = not ticked"),
                               in_lang(ui, "1 = angekreuzt", "1 = ticked")] + missing)
            notes.append(in_lang(ui, "Mehrfachauswahl: eine Spalte je Antwort; 0 auch, wenn in einer beantworteten "
                                     "Zeile nichts angekreuzt wurde; leer = Zeile ganz ohne Antwort",
                                 "multiple choice: one column per answer; 0 also when nothing was ticked in an answered "
                                 "row; empty = row not answered at all"))
        elif col["kind"] in ("single", "dropdown"):
            from app.items.base import plain

            typ = "Auswahl"
            labels = [f"{n} = {plain(l)}" if l else str(n) for n, l in enumerate(self.labels(item, col, lang), start=1)]
            if col.get("dont_know"):
                labels.append(in_lang(ui, "{code} = weiß nicht", "{code} = don't know", code=DONT_KNOW_CODE))
                notes.append(in_lang(ui, "{code} = „weiß nicht“, zählt als fehlend", "{code} = “don't know”, counts as missing",
                                     code=DONT_KNOW_CODE))
            values = "; ".join(labels + missing)
            if col["kind"] == "dropdown":
                notes.append(in_lang(ui, "als Dropdown-Liste", "as a drop-down list"))
        elif col["kind"] == "number":
            from app.items.base import number_text

            typ = "Zahl" if col.get("number") == "decimal" else "Ganzzahl"
            lo, hi = col.get("min"), col.get("max")
            if lo is not None or hi is not None:
                values = (in_lang(ui, "{lo} bis {hi}", "{lo} to {hi}", lo=number_text(lo), hi=number_text(hi)) if lo is not None and hi is not None
                          else in_lang(ui, "ab {lo}", "from {lo}", lo=number_text(lo)) if hi is None
                          else in_lang(ui, "bis {hi}", "up to {hi}", hi=number_text(hi)))
            values = "; ".join([v for v in [values] if v] + missing)
            unit = self.unit(item, col, lang)
            if unit:
                notes.append(in_lang(ui, "Einheit: {u}", "unit: {u}", u=unit))
        else:
            typ = "Text"
            values = "; ".join(missing)
            notes.append(in_lang(ui, "Kurztext, höchstens {n} Zeichen", "short text, at most {n} characters", n=MAX_TEXT))
        if col.get("required"):
            notes.append(in_lang(ui, "Pflichtangabe", "required"))
        return typ, values, notes

    def questionnaire_lines(self, item, lang):
        lines = []
        for i, col in enumerate(self.columns(item)):
            what = self.title(item, col, lang)
            if has_labels(col):
                what += ": " + " / ".join(l or str(n) for n, l in enumerate(self.labels(item, col, lang), start=1))
            elif col["kind"] == "number":
                what += f" ({tr(lang, 'Zahl', 'number')}{' ' + self.unit(item, col, lang) if self.unit(item, col, lang) else ''})"
            lines.append(f"- {column_code(i, col)}: {what}")
        return lines

    # --- piping: one value per cell (E15) ---------------------------------------------------------------------------

    def pipe_parts(self, item, lang):
        from app.items.base import plain

        rtexts = item.text("rows", lang) or {}
        return [(name, f"{plain(rtexts.get(row['id'], ''))} – {plain(self.title(item, col, lang))}")
                for name, col, row, k in self._cells(item) if k is None]

    def pipe_part_text(self, item, answer, name, lang):
        from app.items.base import number_text, typed_text

        col, row, k = self._cell_of(item, name)
        if col is None or k is not None or not isinstance(answer, dict):
            return ""
        v = (answer.get(row["id"]) or {}).get(col["id"])
        if v is None:
            return ""
        if v == DONT_KNOW:
            return tr(lang, "weiß nicht", "don't know")
        if col["kind"] in ("single", "dropdown"):
            labels = self.labels(item, col, lang)
            return labels[v - 1] if isinstance(v, int) and 1 <= v <= len(labels) and labels[v - 1] else str(v)
        if col["kind"] == "number":
            return number_text(v, lang)
        return typed_text(v)

    # --- dummy data, duration -----------------------------------------------------------------------------------------

    def dummy_fields(self, item, rng, meta, answers=None):
        fields = {}
        for row in self.statements(item, None, answers=answers):
            for col in self.columns(item):
                name = self.field(item, row, col)
                kind = col["kind"]
                if kind in ("single", "dropdown"):
                    fields[name] = str(rng.randint(1, points_of(col)))
                elif kind == "multi":
                    fields[name] = [str(k) for k in range(1, points_of(col) + 1) if rng.random() < 0.4] or ["1"]
                elif kind == "number":
                    lo = col.get("min") if col.get("min") is not None else 0
                    hi = col.get("max") if col.get("max") is not None else max(lo, 0) + 100
                    fields[name] = str(int(rng.uniform(lo, hi)) if col.get("number") != "decimal" else round(rng.uniform(lo, hi), 1))
                else:
                    fields[name] = rng.choice(["ok", "gut", "geht so"])
        return fields

    def seconds(self, item, read):
        """About 3 s per answer field and row plus reading the rows and column questions."""
        n_rows = len(read_cfg(item).get("rows", []))
        return 4 + n_rows * (2 + 3 * len(self.columns(item)))
