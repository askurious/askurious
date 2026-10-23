"""Loops: the blocks inside a loop node of the survey flow run once per selected row
of the loop's stimulus table.

A loop is a group in the flow tree (like a randomizer), edited on the Ablauf page:
  {"t": "loop", "id": "l..", "name": "Schleife 1", "code": "loop1",
   "columns": [{"id": "c..", "name": "robot", "hidden": false}],
   "rows": [{"id": "r..", "values": {column id: {lang: text}}}],
   "mode": "all" | "k" | "level", "k": 2, "order": "fixed" | "random", "balanced": true,
   "level_cols": [column ids],  # mode "level": one row per level (per combination of the values of these columns)
   "children": [block nodes]}
All blocks in a loop run together per row (row 1: A, B; row 2: A, B; ...). Loops contain only
blocks (answers inside are stored per row as "item#row", so conditions or randomizers inside
would be ambiguous), but may themselves sit in a randomizer or condition.

Texts reference columns as {{name}}; they are filled live while rendering, in the
participant's language. Images whose prompt uses variables need one pre-generated image per
distinct combination (see items/image.py).

The block-level helpers (config, active, variables) keep working for code that only knows a
block: they read the loop node that contains the block.
"""
import re

from app.items.base import CODE_RE
from app.items.base import form_id, form_ids, new_id as _nid
from app.lang import de_en

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}\Z")


FIELDS = ("name", "code", "columns", "rows", "mode", "k", "order", "balanced",
          "source", "attributes", "prohibitions", "texts", "plan",  # vignettes (app/vignettes.py)
          "subset",  # a D-optimal subset of the table (app/loop_plan.py)
          "level_cols")  # mode "level": one row per level of these columns (stimulus sampling)
# rows a balanced draw of k may pick: their counts travel in the session cookie between the first page and its
# first "Next" (balance.keep, about 7 bytes a row); browsers keep 4 KB per cookie
BALANCED_PICK_MAX = 300


def _defaults(node=None):
    return {"enabled": False, "id": None, "name": "", "columns": [], "rows": [], "mode": "all", "k": 1,
            "order": "random", "balanced": True, "code": "loop", "level_cols": []}


def nodes(survey):
    from app import flow

    return [n for n, _, _ in flow.walk(flow.tree(survey)) if n["t"] == "loop"]


def node_of(block):
    """The loop node that contains the block, or None."""
    from app import flow

    root = flow.tree(block.survey)
    return next((g for g in reversed(flow.ancestors(root, f"k{block.id}")) if g["t"] == "loop"), None)


def config_node(node):
    """A deep copy of a loop node's settings (callers may change it and then save_node() it)."""
    import copy

    cfg = _defaults()
    cfg.update({k: copy.deepcopy(node[k]) for k in FIELDS if k in node})
    cfg.update(enabled=True, id=node["id"])
    return cfg


def config(block):
    node = node_of(block)
    cfg = config_node(node) if node else _defaults()
    if not node:
        cfg["code"] = f"loop{block.id}"
    return cfg


def active(block):
    loop = config(block)
    return bool(loop["enabled"] and loop["columns"] and loop["rows"])


def blocks_in(survey, node):
    ids = [n["block"] for n in node.get("children", []) if n["t"] == "block"]
    by_id = {b.id: b for b in survey.blocks}
    return [by_id[i] for i in ids if i in by_id]


def save_node(survey, nid, loop):
    """Write settings back into the loop node (the JSON column needs a new object)."""
    import copy

    from app import flow

    root = copy.deepcopy(flow.tree(survey))
    node, _ = flow.find(root, nid)
    if node is None:
        return False
    node.update({k: loop[k] for k in FIELDS if k in loop})
    survey.flow = root
    return True


def save(block, loop):
    if loop.get("id"):
        save_node(block.survey, loop["id"], loop)


def wrap_block(survey, block):
    """Put the block into a new loop node at its current place; returns the node id."""
    import copy

    from app import flow

    flow.normalize(survey)
    root = copy.deepcopy(flow.tree(survey))
    node, parent = flow.find(root, f"k{block.id}")
    n = sum(1 for x, _, _ in flow.walk(root) if x["t"] == "loop")
    loop = {"t": "loop", "id": _nid("l"), "name": de_en("Schleife {n}", "Loop {n}", n=n + 1), "code": f"loop{n + 1}", "columns": [],
            "rows": [], "mode": "all", "k": 1, "order": "random", "balanced": True, "children": [node]}
    parent["children"][parent["children"].index(node)] = loop
    survey.flow = root
    return loop["id"]


def value(loop, row, column, lang, default_lang):
    cell = row.get("values", {}).get(column["id"], {})
    return cell.get(lang) or cell.get(default_lang) or ""


def variables(block, row_id, lang):
    """{column name: value} of one row in a language (falls back to the default language)."""
    loop = config(block)
    row = next((r for r in loop["rows"] if r["id"] == row_id), None)
    if row is None:
        return {}
    default = block.survey.default_language
    return {c["name"]: value(loop, row, c, lang, default) for c in loop["columns"]}


def all_variables(block, lang=None):
    """Variables for every row (default language unless given): used to plan image assets."""
    lang = lang or block.survey.default_language
    return [variables(block, r["id"], lang) for r in config(block)["rows"]]


def row_caption(block, row_id, n, limit=60, lang=None):
    """What the suffix _n of a loop's columns stands for: "Durchgang 2: Samsung · 3,99 €" (the values of the row in
    the default language). The word in `lang`: the researcher's on the pages ("Pass 2: …" in English, UI/UX audit
    2026-10-07, W108); without it, as for the variable labels of the export, the survey's default language."""
    values = [v for v in variables(block, row_id, block.survey.default_language).values() if str(v).strip()]
    text = " · ".join(str(v).strip() for v in values)
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    word = "Pass" if (lang or block.survey.default_language) == "en" else "Durchgang"
    return f"{word} {n}: {text}" if text else f"{word} {n}"


READ_ROWS = 20  # rows the loop page and the AI review read a text with (UI audit 2026-10-12, U45)


def read_with_rows(block, text, lang=None, limit=READ_ROWS):
    """`text` as the participants of each pass read it: the values of each row of the block's loop put in for its
    columns, other placeholders kept (UI audit 2026-10-12, U45: "Bewertung: dem Fahrrad"). [(row number, text)] of
    the first `limit` rows; [] outside a loop or when the text uses no column of its table."""
    from app.items.base import PLACEHOLDER, placeholders

    if not text or not active(block):
        return []
    loop = config(block)
    names = {c["name"] for c in loop["columns"]}
    if not placeholders(text) & names:
        return []
    out = []
    for n, row in enumerate(loop["rows"][:limit], start=1):
        values = variables(block, row["id"], lang or block.survey.default_language)

        def put(m, values=values):
            if m.group(1) not in names:
                return m.group(0)
            return str(values.get(m.group(1)) or "").strip() or (m.group(2) or "").strip() or m.group(0)

        out.append((n, PLACEHOLDER.sub(put, text)))
    return out


def readings(survey, node, lang=None):
    """The texts of a loop's blocks that use a column of its table, each read with every row ("So lesen sich die
    Texte" on the loop page, U45): [{"where", "text", "rows": [(n, text)]}] in block order: the shown block title
    first, then the texts of the elements as the AI review sees them (text_review.parts)."""
    from app import text_review
    from app.items.base import plain

    lang = lang or survey.default_language
    out = []
    for block in blocks_in(survey, node):
        title = block.title(lang)
        rows = read_with_rows(block, title, lang) if block.show_title else []
        if rows:
            out.append({"where": de_en("Blocktitel", "Block title"), "text": title,
                        "rows": [(n, plain(t)) for n, t in rows]})
        for item in block.items:
            for _pid, role, text in text_review.parts(item, lang):
                rows = read_with_rows(block, text, lang)
                if rows:
                    out.append({"where": f"{item.code} · {text_review.role_label(role)}", "text": text,
                                "rows": [(n, plain(t)) for n, t in rows]})
    return out


def row_number(loop, row_id):
    return next((n for n, r in enumerate(loop["rows"], start=1) if r["id"] == row_id), None)


VARIANT_COLUMN = "variante"
MAX_ROWS = 500  # rows a table may get from add_variants


def level_columns(loop):
    """The columns of mode "level" that still exist, in table order."""
    wanted = set(loop.get("level_cols") or [])
    return [c for c in loop["columns"] if c["id"] in wanted]


def level_groups(survey, loop):
    """Mode "level": the row ids per level (a combination of the values of the level columns in the default
    language), levels in the order of their first row."""
    cols, default = level_columns(loop), survey.default_language
    groups = {}
    for row in loop["rows"]:
        key = tuple(value(loop, row, c, default, default).strip() for c in cols)
        groups.setdefault(key, []).append(row["id"])
    return list(groups.values())


def shown_rows(survey, loop):
    """How many rows one person goes through (all, k of n, one per level)."""
    n = len(loop["rows"])
    if loop["mode"] == "k":
        return min(loop["k"], n)
    if loop["mode"] == "level" and level_columns(loop):
        return len(level_groups(survey, loop))
    return n


def selection_text(survey, loop, en=False):
    """The selection per person in words, for the codebook, README and questionnaire."""
    n = len(loop["rows"])
    if loop["mode"] == "k":
        return f"{loop['k']} of {n} rows" if en else f"{loop['k']} von {n} Zeilen"
    if loop["mode"] == "level" and level_columns(loop):
        names = ", ".join(c["name"] for c in level_columns(loop))
        shown = shown_rows(survey, loop)
        return (f"one row per level of {names} ({shown} of {n} rows)" if en
                else f"eine Zeile je Stufe von {names} ({shown} von {n} Zeilen)")
    return "all rows" if en else "alle Zeilen"


def add_variants(survey, node, cond_ids, n):
    """Stimulus sampling: every condition (a combination of the values of the columns `cond_ids`) gets `n`
    variants. Missing variants are copies of the condition's last row (to be changed by hand, or to differ by
    {{variante}} in an image prompt); a hidden column "variante" numbers them; the loop then shows one row per
    condition. Conditions with more rows keep them all. Returns (conditions, rows added)."""
    import copy

    loop = config_node(node)
    chosen = set(cond_ids)
    cond_ids = [c["id"] for c in loop["columns"] if c["id"] in chosen]
    if not loop["rows"]:
        raise ValueError(de_en("Die Tabelle hat noch keine Zeilen.", "The table has no rows yet."))
    if not cond_ids:
        raise ValueError(de_en("Bitte mindestens eine Spalte wählen, die die Bedingung angibt.",
                               "Please choose at least one column that gives the condition."))
    if any(c["name"] == VARIANT_COLUMN for c in loop["columns"]):
        raise ValueError(de_en("Die Tabelle hat schon eine Spalte „{c}“.", "The table already has a column “{c}”.",
                               c=VARIANT_COLUMN))
    loop["level_cols"] = cond_ids
    groups = level_groups(survey, loop)
    by_id = {r["id"]: r for r in loop["rows"]}
    total = sum(max(len(g), n) for g in groups)
    if total > MAX_ROWS:
        raise ValueError(de_en("{n} Zeilen wären zu viele (höchstens {most}).", "{n} rows would be too many (at most {most}).",
                               n=total, most=MAX_ROWS))
    vcol = {"id": _nid("c"), "name": VARIANT_COLUMN, "hidden": True}
    lang, rows, added = survey.default_language, [], 0
    for group in groups:
        mine = [by_id[r] for r in group]
        while len(mine) < n:
            mine.append({"id": _nid("r"), "values": copy.deepcopy(mine[-1].get("values", {}))})
            added += 1
        for no, row in enumerate(mine, start=1):
            row.setdefault("values", {})[vcol["id"]] = {lang: str(no)}
            rows.append(row)
    loop.update(columns=loop["columns"] + [vcol], rows=rows, mode="level", balanced=True)
    save_node(survey, node["id"], loop)
    return len(groups), added


def update_from_form(survey, node, form, lang):
    """Save the table editor of a loop node. Returns the numbers of the rows that stayed although the form left them
    out (shown to real participants already, in a survey opened for editing again).

    Default language in draft: columns, rows, and selection may change (empty rows are dropped).
    Translations: only the cell texts of existing rows in `lang`. A running or closed survey keeps the cells of
    the default language: image keys and the meaning of hidden columns hang on them (assets freeze with the logic).
    """
    loop = config_node(node)
    structural = survey.is_editable and lang == survey.default_language
    old_rows = {r["id"]: r for r in loop["rows"]}

    if not survey.is_editable and lang == survey.default_language:
        return
    if not structural:
        for row in loop["rows"]:
            for c in loop["columns"]:
                key = f"cell_{row['id']}_{c['id']}"
                if key in form:
                    cell = dict(row.get("values", {}).get(c["id"], {}))
                    text = form.get(key, "").strip()[:500]
                    if text:
                        cell[lang] = text
                    else:
                        cell.pop(lang, None)
                    row.setdefault("values", {})[c["id"]] = cell
        save_node(survey, node["id"], loop)
        return

    hidden = set(form.getlist("col_hidden"))
    named = [(cid, name) for cid, name in zip(form.getlist("col_id"), form.getlist("col_name")) if name.strip()]
    # stored ids as the file import takes them: they go into field names and the selectors of the scripts; the
    # form's own ids still name its cells (audit 2026-10-13, XS-2)
    columns = [{"id": sid or _nid("c"), "name": name.strip()[:40], "hidden": cid in hidden}
               for (cid, name), sid in zip(named, form_ids([cid for cid, _ in named], "c"))]
    rows = []
    for rid in form.getlist("row_id"):
        old = old_rows.get(rid, {"values": {}})
        values = {}
        for c, (cid, _) in zip(columns, named):
            cell = dict(old.get("values", {}).get(c["id"], {}))
            text = form.get(f"cell_{rid}_{cid or c['id']}", "").strip()[:500]
            if text:
                cell[lang] = text
            else:
                cell.pop(lang, None)
            values[c["id"]] = cell
        if any(cell.get(lang) for cell in values.values()):
            rows.append({"id": form_id(rid, "r") if rid else _nid("r"), "values": values})
    kept = []
    if (survey.qversion or 1) > 1:  # real participations exist only after a start: opened for editing since
        # a row real participants were shown stays (their answers are stored under it, "item#row"): taken out of the
        # table, its answers would fall out of the data set and the order columns would name a row that is gone
        shown = rows_shown_to_real(survey, node["id"])
        new_ids = {r["id"] for r in rows}
        for n, old in enumerate(loop["rows"]):
            if old["id"] in shown and old["id"] not in new_ids:
                rows.insert(min(n, len(rows)), old)
                kept.append(n + 1)
    code = form.get("code", "").strip()
    if form.get("mode") == "plan":  # a D-optimal subset (app/loop_plan.py)
        subset = dict(loop.get("subset") or {})
        known = {c["id"] for c in columns}
        subset["cols"] = [c for c in form.getlist("plan_col") if c in known]
        for key, default in (("per_person", 6), ("decks", 1)):
            value = form.get(f"plan_{key}", "")
            subset[key] = max(1, min(500, int(value))) if value.isascii() and value.isdigit() else subset.get(key, default)
        loop["subset"] = subset
    known = {c["id"] for c in columns}
    loop["level_cols"] = [c for c in form.getlist("level_col") if c in known]
    k = form.get("k", "1")
    k = k if k.isascii() and len(k) < 12 else ""  # "²".isdigit() is true, and int("²") raises
    loop.update(
        columns=columns, rows=rows, name=form.get("name", "").strip()[:100] or loop["name"] or loop["code"],
        code=code if CODE_RE.match(code) else loop["code"],
        mode=form.get("mode") if form.get("mode") in ("k", "level", "plan") else "all", k=max(1, int(k)) if k.isdigit() else 1,
        order="fixed" if form.get("order") == "fixed" else "random", balanced=bool(form.get("balanced")),
    )
    save_node(survey, node["id"], loop)
    return kept


def rows_shown_to_real(survey, nid):
    """Ids of the rows of loop `nid` that real participations were shown (their layouts)."""
    from app.extensions import db
    from app.models import Response

    out = set()
    for layout in db.session.scalars(db.select(Response.layout).filter_by(survey_id=survey.id, source="real",
                                                                           is_preview=False)):
        out.update(((layout or {}).get("loops") or {}).get(nid) or [])
    return out


def _row_counts(survey, nid):
    """How often each row of loop `nid` has been shown to real participants (app/balance.py)."""
    from app import balance

    return balance.counts(survey)["loop"].get(nid, {})


def choose_rows(survey, node, rng):
    """Rows of a loop node for one response, in presentation order."""
    loop = config_node(node)
    if node.get("source") == "factors":  # vignettes: a deck (app/vignettes.py)
        from app import vignettes

        return vignettes.choose_rows(survey, node, rng, _row_counts(survey, node["id"]))
    if node.get("mode") == "plan":  # a D-optimal subset of the table in decks (app/loop_plan.py)
        from app import loop_plan

        return loop_plan.choose_rows(survey, node, rng, _row_counts(survey, node["id"]))
    ids = [r["id"] for r in loop["rows"]]
    if loop["mode"] == "level" and level_columns(loop):  # one row per level, the least shown first when balanced
        counts = _row_counts(survey, node["id"]) if loop["balanced"] else {}
        picked = []
        for group in level_groups(survey, loop):
            shuffled = group[:]
            rng.shuffle(shuffled)
            picked.append(min(shuffled, key=lambda r: counts.get(r, 0)) if loop["balanced"] else shuffled[0])
        if loop["order"] == "random":
            rng.shuffle(picked)
        else:
            picked.sort(key=ids.index)
        return picked
    k = min(loop["k"], len(ids)) if loop["mode"] == "k" else len(ids)
    if k < len(ids):
        if loop["balanced"]:
            counts = _row_counts(survey, node["id"])
            shuffled = ids[:]
            rng.shuffle(shuffled)
            picked = sorted(shuffled, key=lambda r: counts.get(r, 0))[:k]
        else:
            picked = rng.sample(ids, k)
    else:
        picked = ids[:]
    if loop["order"] == "random":
        rng.shuffle(picked)
    else:
        picked.sort(key=ids.index)
    return picked


# --- checks ---------------------------------------------------------------------------------


def check(survey):
    """[(severity, message, item_id, block_id)] for all loops of the survey."""
    from app import flow, panel
    from app.items.base import BUILTIN_VARIABLES, placeholders

    # {{pid}}, URL parameters, answers to questions (app/piping.py checks those)
    from app import piping

    everywhere = (BUILTIN_VARIABLES | {p["name"] for p in panel.config(survey)["params"]} | piping.names(survey)
                  | set(flow.branch_names(survey)) | set(__import__("app.webservice", fromlist=["names"]).names(survey)))
    problems = []
    loop_blocks = set()
    for node, _, _ in flow.walk(flow.tree(survey)):
        if node["t"] != "loop":
            continue
        loop = config_node(node)
        label = de_en("Schleife „{name}“", "Loop “{name}”", name=loop["name"] or loop["code"])
        blocks = blocks_in(survey, node)
        first = blocks[0].id if blocks else None
        if any(ch["t"] != "block" for ch in node.get("children", [])):
            problems.append(("error", de_en("{label}: Darf nur Blöcke enthalten (keine Randomisierung, Bedingung oder "
                                            "Schleife darin).",
                                            "{label}: may contain blocks only (no randomisation, condition or loop in it).",
                                            label=label), None, first))
        if not blocks:
            problems.append(("warning", de_en("{label} ist leer: Blöcke hineinziehen.", "{label} is empty: drag blocks into it.",
                                              label=label), None, None))
        names = [c["name"] for c in loop["columns"]]
        if node.get("source") == "factors":  # vignettes: their own findings instead of the table's
            from app import vignettes

            problems += vignettes.check(survey, node, label, first)
        elif not loop["columns"] or not loop["rows"]:
            problems.append(("error", de_en("{label}: Tabelle ist leer.", "{label}: the table is empty.", label=label), None, first))
        if len(set(names)) != len(names):
            problems.append(("error", de_en("{label}: Spaltennamen sind doppelt.", "{label}: column names are duplicated.",
                                            label=label), None, first))
        bad = [n for n in names if not NAME_RE.match(n)]
        if bad:
            problems.append(("error", de_en("{label}: ungültiger Spaltenname „{name}“ (Buchstaben, Ziffern, _).",
                                            "{label}: invalid column name “{name}” (letters, digits, _).", label=label,
                                            name=bad[0]), None, first))
        if node.get("mode") == "plan" and node.get("source") != "factors":
            from app import loop_plan

            for text in loop_plan.problems(survey, node):
                problems.append(("error", f"{label}: {text}", None, first))  # loop_plan.problems: in the page's language
            if not loop_plan.current(survey, node):
                problems.append(("error", de_en("{label}: Versuchsplan noch nicht erzeugt oder veraltet (nach einer Änderung an "
                                                "Tabelle, Faktor-Spalten oder Deckgröße neu erzeugen).",
                                                "{label}: experimental design not generated yet or out of date (generate it anew "
                                                "after a change to the table, the factor columns or the deck size).",
                                                label=label), None, first))
        if loop["mode"] == "level" and node.get("source") != "factors" and loop["rows"]:
            problems += _level_problems(survey, loop, label, blocks, first)
        if loop["mode"] == "k" and loop["k"] > len(loop["rows"]):
            problems.append(("error", de_en("{label}: {k} von nur {n} Zeilen.", "{label}: {k} of only {n} rows.",
                                            label=label, k=loop["k"], n=len(loop["rows"])), None, first))
        if loop["mode"] == "k" and loop["balanced"] and BALANCED_PICK_MAX < loop["k"] < len(loop["rows"]):
            problems.append(("warning", de_en("{label}: {k} Zeilen balanciert zu ziehen ist zu viel für das "
                                              "Sitzungscookie der Teilnehmenden (höchstens {max}); weniger "
                                              "Zeilen ziehen oder „balanciert“ ausschalten.",
                                              "{label}: drawing {k} rows balanced is too much for the participants' "
                                              "session cookie (at most {max}); draw fewer rows or switch off "
                                              "“balanced”.", label=label, k=loop["k"], max=BALANCED_PICK_MAX), None, first))
        default = survey.default_language
        for row_no, row in enumerate(loop["rows"], start=1):
            missing = [c["name"] for c in loop["columns"] if not value(loop, row, c, default, default)]
            if missing:
                problems.append(("warning", de_en("{label}, Zeile {row}: kein Wert für {cols}.",
                                                  "{label}, row {row}: no value for {cols}.", label=label, row=row_no, cols=", ".join(missing)), None, first))
                break
        hidden = {c["name"] for c in loop["columns"] if c["hidden"]}
        for block in blocks:
            loop_blocks.add(block.id)
            for item in block.items:
                if not item.kind.allowed_in_loop:
                    problems.append(("error", de_en("{code}: {kind} kann nicht in einer Schleife stehen.",
                                                    "{code}: {kind} cannot be in a loop.", code=item.code, kind=item.kind.label),
                                     item.id, block.id))
                unknown = item.kind.used_placeholders(item) - set(names) - everywhere
                if unknown:
                    problems.append(("error", de_en("{code}: unbekannter Platzhalter {names}.",
                                                    "{code}: unknown placeholder {names}.", code=item.code,
                                                    names=", ".join("{{" + u + "}}" for u in sorted(unknown))), item.id, block.id))
                shown_hidden = placeholders(" ".join(str(v) for v in (item.texts or {}).values())) & hidden
                if shown_hidden and not item.kind.has_image:
                    problems.append(("warning", de_en("{code}: Interne Spalte {cols} wird Teilnehmenden angezeigt.",
                                                      "{code}: internal column {cols} is shown to participants.", code=item.code,
                                                      cols=", ".join(sorted(shown_hidden))), item.id, block.id))
    return problems


def _level_problems(survey, loop, label, blocks, first):
    """Mode "level": a level column is chosen, some level has more than one row, and an AI image differs between
    the rows of a level (else every variant shows the same picture)."""
    cols = level_columns(loop)
    if not cols:
        return [("error", de_en("{label}: „eine Zeile je Stufe“ braucht eine Spalte, deren Stufen gezogen werden.",
                                "{label}: “one row per level” needs a column whose levels are drawn.", label=label), None, first)]
    if all(len(g) == 1 for g in level_groups(survey, loop)):
        return [("warning", de_en("{label}: Jede Stufe von {cols} hat nur eine Zeile; das ist dasselbe wie „alle Zeilen“.",
                                  "{label}: every level of {cols} has only one row; that is the same as “all rows”.",
                                  label=label, cols=", ".join(c["name"] for c in cols)), None, first)]
    out = []
    others = {c["name"] for c in loop["columns"]} - {c["name"] for c in cols}
    for block in blocks:
        for item in block.items:
            if not item.kind.has_image:
                continue
            used = item.kind.prompt_variables(item)
            if used and not used & others:
                if VARIANT_COLUMN in others:
                    message = de_en("{code}: Das Bild ist für alle Zeilen einer Stufe gleich; für eigene Bilder je "
                                    "Variante {where} in den Bild-Prompt schreiben.",
                                    "{code}: the image is the same for all rows of a level; for an image of its own per "
                                    "variant, write {where} into the image prompt.",
                                    code=item.code, where="{{" + VARIANT_COLUMN + "}}")
                else:
                    message = de_en("{code}: Das Bild ist für alle Zeilen einer Stufe gleich; für eigene Bilder je "
                                    "Variante eine Spalte, die sich je Variante unterscheidet, in den Bild-Prompt schreiben.",
                                    "{code}: the image is the same for all rows of a level; for an image of its own per "
                                    "variant, write a column that differs per variant into the image prompt.", code=item.code)
                out.append(("warning", message, item.id, block.id))
    return out
