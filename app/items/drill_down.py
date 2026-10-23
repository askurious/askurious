"""Drill-down: several drop-down lists one below the other, each offering only what fits the choice above it
(brand, then a model of that brand, then a year that model was built).

The researchers give the hierarchy as a table, one row per valid combination and one column per list, with the
names of the lists in the first row (pasted from a spreadsheet or read from a CSV file). A row may end early: a
branch without a further level (Berlin has no districts in a list of states and districts) ends the answer there.

Storage: config "lists" = [{"id", "code"}] (the code is the end of the export column <code>_<list code>, made from
the name of the list), config "entries" = [{"id", "code", "list": level from 0, "parent": id (not on level 0)}]
in the order of the table; texts[lang]["lists"] = {list id: name}, texts[lang]["entries"] = {entry id: label}. The
codes of the entries are numbered per list in the order of the table (1, 2, ...). Before real answers exist they are
numbered anew on every change; afterwards an entry keeps its code and a new one gets the next free number.

Answer: the chosen entries from the top as a list of ids, as far as the person chose; complete when the last one
has nothing below it. Export: one column per list with the code of the chosen entry (empty where nothing was chosen
or the branch ended above).

Participant page (items/drill_down/participant.html, participant.js [data-drill]): every list below the first
holds all its entries grouped by the entry above (optgroup), so the page works without JavaScript too (the server
checks that the combination exists); the script keeps only the entries below the current choice, locks a list
until the one above is chosen and empties the lists below when a choice changes."""
import csv
import io
import re

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_id, r_int, r_maybe, r_rows, r_text  # rules for survey files, see base.py
from app.items.base import CODE_RE, AnswerError, FormRefused, ItemType, in_lang, new_id, plain, prepared
from app.items.base import is_place_list, read_places  # lists of places count like postal codes
from app.lang import Both, de_en, english
from app.ui_i18n import tr

MAX_LISTS = 5
MAX_ENTRIES = 10000  # all levels together: the 400 districts or 11,000 municipalities of Germany are a lot already
MAX_LABEL = 200
LIST_RULES = r_rows(MAX_LISTS, id=r_id, code=r_text(30, 1))
ENTRY_RULES = r_rows(MAX_ENTRIES, id=r_id, code=r_text(64, 1), list=r_int(0, MAX_LISTS - 1), parent=r_maybe(r_id))
UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def read_table(text):
    """The table the editor's field holds: (names of the lists, rows of labels). The separator is a tab (pasted from
    a spreadsheet), else a semicolon, else a comma, as the first line shows; quoted cells as in CSV. Every cell is
    trimmed, a row ends at its first empty cell, empty rows are left out, and so are lists beyond MAX_LISTS.
    A quote that is not closed would take the rest of the table into one cell: FormRefused naming its line."""
    lines = [line for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    head = next((line for line in lines if line.strip()), "")
    sep = "\t" if "\t" in head else ";" if ";" in head else ","
    rows = []
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=sep)
    while True:
        start = reader.line_num + 1  # the line the next row begins on
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error:  # a quoted cell longer than the csv module takes (the rest of a long table)
            raise FormRefused(_open_quote(start)) from None
        if any("\n" in c for c in cells):
            raise FormRefused(_open_quote(start))
        cells = [" ".join(c.split())[:MAX_LABEL] for c in cells[:MAX_LISTS]]
        if "" in cells:
            cells = cells[:cells.index("")]
        if cells:
            rows.append(cells)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _open_quote(line):
    return de_en("In Zeile {n} der Tabelle wird ein Anführungszeichen nicht geschlossen (oder eine Zelle enthält einen "
                 "Zeilenumbruch). Bitte das Anführungszeichen entfernen oder schließen.",
                 "In line {n} of the table a quotation mark is not closed (or a cell holds a line break). Please remove "
                 "or close the quotation mark.", n=line)


def _count(n):
    """A count in the language of the page (10.000 / 10,000)."""
    return f"{n:,}" if english() else f"{n:,}".replace(",", ".")


def write_table(names, paths):
    """The table for the editor's field: names of the lists, then one row per path, separated by semicolons."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(names)
    for path in paths:
        writer.writerow(path)
    return out.getvalue().rstrip("\n")


def list_code(name, n, taken):
    """The end of a list's column name, made from its name ("Marke" -> marke, "Baujahr (ab)" -> baujahr_ab)."""
    code = re.sub(r"[^a-z0-9]+", "_", (name or "").translate(UMLAUTS).lower()).strip("_")[:20].rstrip("_")
    if not code or not code[0].isalpha():
        code = f"l{n + 1}"
    base, k = code, 2
    while code in taken:
        code, k = f"{base[:17]}_{k}", k + 1
    return code


@register
class DrillDown(ItemType):
    key = "drill_down"
    label = Both("Abhängige Auswahllisten (Drill-down)", "Dependent drop-downs (drill-down)")
    short = Both("Abhängige Dropdown-Listen", "Dependent drop-down lists")
    description = Both("Abhängige Dropdown-Listen: Jede bietet nur, was zur Wahl darüber passt (Marke, dann Modell)",
                       "Dependent drop-down lists: each offers only what fits the choice above (brand, then model)")
    order = 12
    fact_sheet = FactSheet(
        "classic",
        keywords=["Abhängige Listen", "Dependent lists", "Kaskade", "Cascading", "Marke Modell", "Brand model"],
        can=[("Zwei bis fünf Dropdown-Listen untereinander, jede hängt von der Wahl darüber ab.",
              "Two to five drop-down lists one below the other, each depending on the choice above."),
             ("Die Hierarchie kommt als Tabelle: aus Excel einfügen oder als CSV-Datei laden, eine Zeile je Kombination.",
              "The hierarchy comes as a table: paste it from Excel or load a CSV file, one row per combination."),
             ("Zweige dürfen früher enden (ein Land ohne Kreise).", "Branches may end earlier (a state without districts)."),
             ("Export: eine Spalte je Liste mit dem Code des gewählten Eintrags.",
              "Export: one column per list with the code of the chosen entry.")],
        good_for=[("Lange, gegliederte Listen: Bundesland, Kreis, Gemeinde; Fach und Hochschule; Beruf nach Berufsfeld",
                   "Long structured lists: state, district, municipality; subject and university; occupation by field"),
                  ("Produkt nach Marke, ohne unmögliche Kombinationen", "Product by brand, without impossible combinations")],
        not_for=[("Kurze Listen ohne Gliederung (dann Einfachauswahl als Dropdown)",
                  "Short lists without structure (then a single choice as a drop-down)"),
                 ("Mehrere Antworten je Liste (dann Mehrfachauswahl)", "Several answers per list (then multiple choice)")],
        designs=["cross"],
        examples=[("Welches Auto fahren Sie? Marke, Modell, Baujahr", "Which car do you drive? Brand, model, year of manufacture")],
    )
    levels = ("nominal",)
    result_kind = "categorical"
    import_rules = {"lists": LIST_RULES, "entries": ENTRY_RULES, "places": r_flag}

    def default_config(self):
        return {"lists": [], "entries": []}

    def default_texts(self, lang):
        return {"question": "", "lists": {}, "entries": {}}

    # --- the hierarchy ---------------------------------------------------------------------------------

    def lists(self, item, lang=None):
        """[{"id", "code", "label"}] of the lists from the top."""
        names = item.t(lang).get("lists") or {}
        return [{**x, "label": names.get(x["id"], "")} for x in (item.config or {}).get("lists", [])]

    def entries(self, item, lang=None):
        """[{"id", "code", "list", "parent", "label"}] in the order of the table."""
        labels = item.t(lang).get("entries") or {}
        return [{**e, "label": labels.get(e["id"], "")} for e in (item.config or {}).get("entries", [])]

    def children(self, item, lang=None):
        """{parent id ("" on the first level): [entries]} in the order of the table."""
        out = {}
        for e in self.entries(item, lang):
            out.setdefault(e.get("parent") or "", []).append(e)
        return out

    def paths(self, item, lang=None):
        """Every combination from the top to an entry with nothing below, as [labels], in the order of the table."""
        kids, out = self.children(item, lang), []

        def walk(entry, above):
            path = above + [entry["label"]]
            below = kids.get(entry["id"])
            if not below:
                out.append(path)
            for e in below or ():
                walk(e, path)

        for e in kids.get("", []):
            walk(e, [])
        return out

    def table(self, item, lang):
        """The table of the editor's field in `lang` (names of the lists, one row per combination)."""
        if not (item.config or {}).get("lists"):
            return ""
        return write_table([x["label"] for x in self.lists(item, lang)], self.paths(item, lang))

    # --- editor -----------------------------------------------------------------------------------------

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        read_places(item, form)  # a privacy setting: also in a running survey
        if "table" not in form:
            return
        text = form.get("table", "")
        if structural and lang == item.survey.default_language:
            self._rebuild(item, *read_table(text), lang)
        elif not structural and read_table(text) == read_table(self.table(item, lang)):
            return  # the table as the page showed it: nothing to change (also where it cannot be read back in full)
        else:  # a translation of a draft without real answers may still put cells beyond the table aside, as before
            from app import answer_lock

            survey = item.survey
            fixed = not structural and (not survey.is_editable or answer_lock.fixed(survey, item))
            self._relabel(item, *read_table(text), lang, strict=fixed)

    def _id_rows(self, item):
        """Every combination from the top to an entry with nothing below, as [ids], in the order of the table."""
        kids, out = self.children(item), []

        def walk(entry, above):
            path = above + [entry["id"]]
            below = kids.get(entry["id"])
            if not below:
                out.append(path)
            for e in below or ():
                walk(e, path)

        for e in kids.get("", []):
            walk(e, [])
        return out

    def _by_place(self, item, rows):
        """{labels from the top: id of the entry at that place} if the table `rows` has the form of the stored one
        (as many rows, each as long, and every entry named alike in all its rows, no two entries alike), else None:
        then the cells can be put on the entries at the same place without moving a meaning."""
        old = self._id_rows(item)
        if len(old) != len(rows) or any(len(ids) != len(cells) for ids, cells in zip(old, rows)):
            return None
        place, back = {}, {}
        for ids, cells in zip(old, rows):
            for n, eid in enumerate(ids):
                path = tuple(cells[:n + 1])
                if place.setdefault(path, eid) != eid or back.setdefault(eid, path) != path:
                    return None
        return place

    def _rebuild(self, item, names, rows, lang):
        """The hierarchy anew from the table. An entry found again by its labels from the top (in the edited
        language) keeps its id, so its translations stay; the codes are numbered anew while no real answers exist,
        afterwards an entry keeps its code and a new one gets the next free number of its list."""
        config, texts = dict(item.config or {}), dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        keep = self.has_real_answers(item)
        old_lists = config.get("lists", [])
        lists, list_names = [], {}
        for n, name in enumerate(names[:MAX_LISTS]):
            old = old_lists[n] if n < len(old_lists) else None
            lid = old["id"] if old else new_id("l")
            code = old["code"] if old and keep else list_code(name, n, {x["code"] for x in lists})
            lists.append({"id": lid, "code": code})
            list_names[lid] = name
        rows = [row[:len(lists)] for row in rows]
        total = len({tuple(row[:n + 1]) for row in rows for n in range(len(row))})
        if total > MAX_ENTRIES:  # cutting the table would drop whole branches and end others too early (E2)
            raise FormRefused(de_en("Die Tabelle ergibt {n} Einträge (alle Listen zusammen); abhängige Auswahllisten fassen "
                                    "höchstens {max}. Bitte die Tabelle kürzen oder auf mehrere Elemente aufteilen.",
                                    "The table makes {n} entries (all lists together); dependent drop-downs hold at most "
                                    "{max}. Please shorten the table or split it over several elements.",
                                    n=_count(total), max=_count(MAX_ENTRIES)))
        found, path_of = {}, {}  # (labels from the top) -> old entry; old id -> its labels from the top
        old_entries = self.entries(item, lang)
        for e in old_entries:  # a parent always comes before its entries
            above = path_of.get(e["parent"]) if e.get("parent") else ()
            if above is None:
                continue
            path_of[e["id"]] = above + (e["label"],)
            found.setdefault(path_of[e["id"]], e)
        # the same form as before: an entry not found by its labels is the one at the same place, so a wording
        # corrected above ("VW" -> "Volkswagen") keeps the ids below it and their translations (N6)
        place = self._by_place(item, rows) if len(lists) == len(old_lists) else None
        if place:
            by_id = {e["id"]: e for e in old_entries}
            claimed = {found[p]["id"] for p in place if p in found}
            for p, eid in place.items():
                if p not in found and eid not in claimed and eid in by_id:
                    found[p] = by_id[eid]
        next_code = [max((int(e["code"]) for e in config.get("entries", []) if e["list"] == n and str(e["code"]).isdigit()),
                         default=0) + 1 for n in range(len(lists))]
        counts = [0] * len(lists)
        entries, labels, made = [], {}, {}
        for row in rows:
            parent = None
            for n in range(len(row)):
                path = tuple(row[:n + 1])
                if path not in made:
                    old = found.get(path)
                    eid = old["id"] if old else new_id("e")
                    if keep and old:
                        code = old["code"]
                    elif keep:
                        code, next_code[n] = str(next_code[n]), next_code[n] + 1
                    else:
                        counts[n] += 1
                        code = str(counts[n])
                    entry = {"id": eid, "code": code, "list": n}
                    if parent:
                        entry["parent"] = parent
                    entries.append(entry)
                    labels[eid] = row[n]
                    made[path] = eid
                parent = made[path]
        config["lists"], config["entries"] = lists, entries
        lt["lists"], lt["entries"] = list_names, labels
        texts[lang] = lt
        # the other languages keep the texts of the lists and entries that are still there
        ids = {x["id"] for x in lists} | {e["id"] for e in entries}
        for other, ot in list(texts.items()):
            if other != lang and isinstance(ot, dict):
                texts[other] = {**ot, **{k: {i: v for i, v in (ot.get(k) or {}).items() if i in ids} for k in ("lists", "entries")}}
        item.config, item.texts = config, texts

    def _relabel(self, item, names, rows, lang, strict=False):
        """Another language, or a survey that has started: the table only gives new wordings. The names go to the
        lists in their order, the cells to the entries at the same place of the same row; the structure stays.
        strict (the structure is fixed: in the field, or real answers): only a table of the same form is taken,
        else FormRefused; a row added in the middle would give the stored answers other labels (N1)."""
        lists = (item.config or {}).get("lists", [])
        if strict and (len(names) != len(lists) or self._by_place(item, rows) is None):
            raise FormRefused(de_en(
                "Listen und Einträge stehen fest (die Befragung ist im Feld oder hat echte Antworten). Hier lassen sich "
                "nur die Wortlaute an derselben Stelle ändern: gleich viele Zeilen und Spalten, ein Eintrag in allen "
                "seinen Zeilen gleich benannt. Zeilen hinzufügen, löschen oder umstellen geht nur vor dem Feldstart.",
                "Lists and entries are fixed (the survey is in the field or has real answers). Only the wordings can "
                "be changed here, at the same place: as many rows and columns, an entry named alike in all its rows. "
                "Adding, deleting or moving rows is only possible before fieldwork starts."))
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        list_names, labels = dict(lt.get("lists") or {}), dict(lt.get("entries") or {})
        for x, name in zip(lists, names):
            list_names[x["id"]] = name
        for ids, cells in zip(self._id_rows(item), rows):
            for eid, cell in zip(ids, cells):
                labels[eid] = cell
        lt["lists"], lt["entries"] = list_names, labels
        texts[lang] = lt
        item.texts = texts

    def validate(self, item):
        problems = []
        lists, entries = self.lists(item), self.entries(item)
        if len(lists) < 2:
            problems.append(de_en("mindestens zwei Listen (Spalten der Tabelle)", "at least two lists (columns of the table)"))
        if not entries:
            problems.append(de_en("keine Einträge: Tabelle mit einer Zeile je Kombination einfügen",
                                  "no entries: paste a table with one row per combination"))
        if any(not x["label"] for x in lists):
            problems.append(de_en("Liste ohne Namen (erste Zeile der Tabelle)", "list without a name (first row of the table)"))
        if any(not e["label"] for e in entries):
            problems.append(de_en("Eintrag ohne Beschriftung", "entry without a label"))
        bad = [x["code"] for x in lists if not CODE_RE.match(f"{item.code}_{x['code']}")]
        if bad and not self.has_real_answers(item):
            problems.append(de_en("Variablennamen zu lang oder ungültig: {bad}", "variable names too long or invalid: {bad}",
                                  bad=", ".join(f"{item.code}_{c}" for c in bad[:3])))
        kids = self.children(item)
        if lists and entries and not any(e["id"] in kids for e in kids.get("", [])):
            problems.append(de_en("Die Tabelle hat nur eine Ebene: Dafür genügt eine Einfachauswahl als Dropdown",
                                  "the table has one level only: a single choice as a drop-down is enough for that"))
        return problems

    def import_problems(self, item):
        """Every entry below the first list names an entry of the list above as its parent."""
        level = {e["id"]: e["list"] for e in (item.config or {}).get("entries", [])}
        n = len((item.config or {}).get("lists", []))
        for e in (item.config or {}).get("entries", []):
            if e["list"] >= n or (e["list"] == 0) != ("parent" not in e) or (
                    "parent" in e and level.get(e["parent"]) != e["list"] - 1):
                return [de_en("Die Einträge passen nicht zu den abhängigen Auswahllisten",
                              "the entries do not fit the dependent drop-downs")]
        return []

    def preview_empty(self, item):
        if not (item.config or {}).get("entries"):
            return de_en("Noch keine Tabelle: eine Zeile je Kombination einfügen", "No table yet: paste one row per combination"), None
        return None

    # --- participant side ---------------------------------------------------------------------------------

    def levels_for(self, item, lang, chosen):
        """What the page shows: [{"index", "list", "groups": [(parent label or None, [entries])], "chosen", "open"}],
        one per list. "open" is False for a list below a choice that has nothing below it (the branch ends)."""
        kids = self.children(item, lang)
        by_id = {e["id"]: e for es in kids.values() for e in es}
        chosen = list(chosen or [])
        out, ended = [], False
        for n, x in enumerate(self.lists(item, lang)):
            if n == 0:
                groups = [(None, kids.get("", []))]
            else:
                groups = [(plain(by_id[p]["label"]), es) for p, es in kids.items() if p and by_id.get(p, {}).get("list") == n - 1]
            if n > 0 and len(chosen) >= n and not kids.get(chosen[n - 1]):
                ended = True
            out.append({"index": n, "list": x, "groups": groups, "chosen": chosen[n] if n < len(chosen) else "",
                        "open": not ended})
        return out

    def _path(self, item, form):
        """The ids sent, from the top, up to the first empty list."""
        out = []
        for n in range(len((item.config or {}).get("lists", []))):
            value = form.get(f"i{item.id}_{n}", "")
            if not value:
                break
            out.append(value)
        return out

    def parse_answer(self, item, form, lang="de", meta=None):
        path = self._path(item, form)
        kids = self.children(item)
        parent = ""
        for eid in path:
            if eid not in {e["id"] for e in kids.get(parent, [])}:
                raise AnswerError(tr(lang, "Ungültige Antwort.", "Invalid answer."))
            parent = eid
        if not path:
            if item.required:
                raise AnswerError(tr(lang, "Bitte wählen Sie eine Antwort.", "Please choose an answer."))
            return None
        if kids.get(parent) and item.required:
            gap = self.first_open(item, lang, path)
            names = self.lists(item, lang)
            if gap is not None and gap < len(names) and plain(names[gap]["label"]):
                raise AnswerError(tr(lang, "Bitte wählen Sie noch etwas bei „{name}“.", "Please also choose an entry for “{name}”.",
                                     name=plain(names[gap]["label"])))
            raise AnswerError(tr(lang, "Bitte wählen Sie in jeder Liste eine Antwort.", "Please choose an answer in every list."))
        return path

    def first_open(self, item, lang, chosen):
        """The index of the first list still to choose below the chain `chosen` (None if the branch has ended): the
        list a message names and the page marks (X36)."""
        for level in self.levels_for(item, lang, chosen):
            if level["open"] and not level["chosen"]:
                return level["index"]
        return None

    def display_value(self, item, form, meta=None):
        return self._path(item, form)

    def is_missing(self, item, answer):
        if not isinstance(answer, list) or not answer:
            return True
        parents = prepared(item, "drill_parents", lambda: {e.get("parent") for e in (item.config or {}).get("entries", [])})
        return answer[-1] in parents

    # --- piping: the chosen entry of each list ---------------------------------------------------------------

    def pipeable(self, item):
        return True

    def pipe_text(self, item, answer, lang):
        """The deepest chosen entry ("Golf")."""
        labels = item.t(lang).get("entries") or {}
        return plain(labels.get(answer[-1], "")) if isinstance(answer, list) and answer else ""

    def pipe_parts(self, item, lang):
        return [(f"{item.code}_{x['code']}", plain(x["label"])) for x in self.lists(item, lang)]

    def pipe_part_text(self, item, answer, name, lang):
        labels = item.t(lang).get("entries") or {}
        for n, x in enumerate((item.config or {}).get("lists", [])):
            if name == f"{item.code}_{x['code']}" and isinstance(answer, list) and n < len(answer):
                return plain(labels.get(answer[n], ""))
        return ""

    # --- export ----------------------------------------------------------------------------------------------

    def export_columns(self, item):
        question = plain(item.text("question") or "")
        return [(f"{item.code}_{x['code']}", f"{question}: {plain(x['label'])}".lstrip(": "))
                for x in self.lists(item)]

    def place_columns(self, item):
        """A list of places (ticked, or municipality keys as codes): every level, as the upper ones narrow it down."""
        codes = [e["code"] for e in (item.config or {}).get("entries", [])]
        return [c for c, _ in self.export_columns(item)] if is_place_list(item.config, codes) else []

    def export_values(self, item, answer):
        codes = prepared(item, "drill_codes", lambda: {e["id"]: e["code"] for e in (item.config or {}).get("entries", [])})
        path = answer if isinstance(answer, list) else []
        return {f"{item.code}_{x['code']}": codes.get(path[n]) if n < len(path) else None
                for n, x in enumerate((item.config or {}).get("lists", []))}

    def _level_of(self, item, column):
        for n, x in enumerate((item.config or {}).get("lists", [])):
            if column == f"{item.code}_{x['code']}":
                return n
        return None

    def value_labels(self, item, n, lang):
        """{code: label} of the entries of list `n`; below the first list with the entry above in brackets, as the
        same label may stand under several entries ("Sonstige")."""
        entries = self.entries(item, lang)
        by_id = {e["id"]: e for e in entries}
        out = {}
        for e in entries:
            if e["list"] == n:
                label = plain(e["label"]).replace(";", ",")
                parent = by_id.get(e.get("parent"))
                out[e["code"]] = f"{label} ({plain(parent['label']).replace(';', ',')})" if parent else label
        return out

    def result_column(self, item, column):
        n = self._level_of(item, column)
        if n is None:
            return None, "", None
        return self.result_kind, "", self.value_labels(item, n, item.survey.default_language)

    def codebook_info(self, item, column, lang, ui=None):
        n = self._level_of(item, column)
        if n is None:
            return None
        values = "; ".join(f"{code} = {label}" for code, label in self.value_labels(item, n, lang).items())
        notes = [in_lang(ui, "Liste {n} von {total}; leer = nicht gewählt oder Zweig endet weiter oben",
                         "list {n} of {total}; empty = not chosen or the branch ends further up",
                         n=n + 1, total=len(item.config.get("lists", [])))]
        return "Auswahl", values, notes

    def questionnaire_lines(self, item, lang):
        lists = self.lists(item, lang)
        paths = self.paths(item, lang)
        out = [f"  - {' → '.join(plain(x['label']) for x in lists)}"]
        out += [f"    - {' → '.join(plain(p) for p in path)}" for path in paths[:50]]
        if len(paths) > 50:
            out.append(f"    - … (+{len(paths) - 50})")
        return out

    # --- dummy data, duration --------------------------------------------------------------------------------

    def dummy_fields(self, item, rng, meta, answers=None):
        kids, out, parent = self.children(item), {}, ""
        for n in range(len((item.config or {}).get("lists", []))):
            below = kids.get(parent)
            if not below:
                break
            parent = rng.choice(below)["id"]
            out[f"i{item.id}_{n}"] = parent
        return out

    def seconds(self, item, read):
        return read + 5 * max(1, len((item.config or {}).get("lists", [])))
