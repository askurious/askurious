"""Answer options carried forward: a later question shows the options a person chose in an earlier one.

"Which devices do you use?" (multiple choice), then "Which of them most often?" with the chosen devices only, or
"How satisfied are you with …" with one statement per chosen device. The later question (single or multiple
choice, ranking, constant sum, or the statements of a Likert matrix) names the earlier single or multiple choice
(config "carry": {"item": id, "which": "chosen" | "unchosen" | "all"}).

A side-by-side matrix is a source too, by one answer of one of its choice columns (config "carry" with "col" and
"value"): "nur die genutzten Verkehrsmittel" carries the rows of the matrix where "Genutzt?" is "ja" ("chosen"),
or where it has another answer ("unchosen"). Its rows come with their codes and wording like options.

The carried options are not kept twice by hand: sync() writes them into the options (or statements) of the
later question as rows with "src" ({"item", "code", "which", "text"?}), with the codes of the earlier question
and its wording in every language, in front of the question's own rows ("none of these"). It runs whenever a
question or a translation is saved. So every place that reads options (export, results, codebook, conditions)
finds them like any other.

Which of them a person sees follows from their answer to the earlier question (app/rows.py: hidden). What was
not shown is exported as -77 where an option has a column of its own; a question with no carried option left is
left out, a single choice, ranking or constant sum also with one option left (nothing to decide).
"""
import re

TARGETS = {"single_choice": "options", "multiple_choice": "options", "ranking": "options", "constant_sum": "options",
           "likert": "statements", "side_by_side": "rows"}
TEXTS = {"options": "options", "statements": "statements", "rows": "rows"}
SOURCES = ("single_choice", "multiple_choice", "side_by_side")
WHICH = (("chosen", "die gewählten"), ("unchosen", "die nicht gewählten"), ("all", "alle"))
WHICH_EN = (("chosen", "the chosen"), ("unchosen", "the unchosen"), ("all", "all"))
NEEDS_TWO = ("single_choice", "ranking", "constant_sum")  # with one option left there is nothing to decide


def which():
    """WHICH as the editor shows it, in the language of the page."""
    from app.lang import english

    return WHICH_EN if english() else WHICH


def r_carry(value):
    """Rule for a survey file."""
    from app.items.base import Unfit

    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) not in ({"item", "which"}, {"item", "which", "col", "value"})
            or isinstance(value["item"], bool) or not isinstance(value["item"], int) or value["which"] not in dict(WHICH)):
        raise Unfit
    if "col" in value:  # one answer of a column of a side-by-side matrix
        if (not isinstance(value["col"], str) or not 0 < len(value["col"]) <= 40 or isinstance(value["value"], bool)
                or not isinstance(value["value"], int) or not 1 <= value["value"] <= 20):
            raise Unfit
        return {"item": value["item"], "which": value["which"], "col": value["col"], "value": value["value"]}
    return {"item": value["item"], "which": value["which"]}


def r_src(value):
    from app.items.base import Unfit

    if (not isinstance(value, dict) or set(value) - {"item", "code", "which", "text", "col", "value"} or isinstance(value.get("item"), bool)
            or not isinstance(value.get("item"), int) or not isinstance(value.get("code"), str) or len(value["code"]) > 64
            or value.get("which") not in dict(WHICH) or value.get("text", True) is not True
            or ("col" in value) != ("value" in value) or ("col" in value and not isinstance(value["col"], str))
            or ("value" in value and (isinstance(value["value"], bool) or not isinstance(value["value"], int)))):
        raise Unfit
    return dict(value)


r_src.optional = True  # a row may leave it out (see items.base.r_dict)


def key_of(item):
    return TARGETS.get(item.type)


def carried(rows):
    return [r for r in rows if r.get("src")]


def ref_of(carry):
    """The source as the editor's list names it: the question's id, for a side-by-side matrix "<id>:<column>:<answer>"."""
    if not carry or not carry.get("item"):
        return ""
    return f"{carry['item']}:{carry['col']}:{carry['value']}" if carry.get("col") else str(carry["item"])


def source(item):
    """The question the options come from, or None (none named, or it is gone or of another type)."""
    from app.conditions import own_item

    ref = ((item.config or {}).get("carry") or {}).get("item")
    found = own_item(item.block.survey, ref) if isinstance(ref, int) and not isinstance(ref, bool) else None
    return found if found is not None and found.type in SOURCES and found.id != item.id else None


def sources(item, lang=None):
    """[(id as text, label)] of the questions options can be carried from: single and multiple choice on
    earlier pages, outside a loop, in the order of the questionnaire."""
    from app import flow, piping
    from app.items.base import plain

    survey = item.block.survey
    pos = piping._positions(survey)
    here = pos.get(item.id)
    in_loop = flow.loop_blocks(survey)
    found = []
    for block in survey.blocks:
        if block.id in in_loop:
            continue
        for other in block.items:
            if other.type in SOURCES and here is not None and other.id in pos and pos[other.id] < here:
                question = plain(other.text("question", lang) or "") or other.kind.label
                if other.type != "side_by_side":
                    found.append((pos[other.id], str(other.id), f"{other.code} · {question[:60]}"))
                    continue
                for col in other.kind.columns(other):  # one entry per answer of a choice column
                    if col["kind"] not in ("single", "dropdown"):
                        continue
                    title = plain(other.kind.title(other, col, lang)) or col["id"]
                    for n, answer in enumerate(other.kind.labels(other, col, lang), start=1):
                        found.append((pos[other.id], f"{other.id}:{col['id']}:{n}",
                                      f"{other.code} · {question[:40]} · {title[:40]} = {plain(answer) or n}"))
    return [(ref, label) for _, ref, label in sorted(found, key=lambda f: f[0])]


def from_form(item, form):
    """The editor's choice: the question to carry from ("" for none) and which of its options."""
    if "carry_item" not in form:
        return
    config = dict(item.config)
    ref = form.get("carry_item", "")
    first, _, rest = ref.partition(":")
    col, _, value = rest.partition(":")
    if first.isascii() and first.isdigit() and len(first) < 12 and (not rest or _column_answer(item, int(first), col, value)):
        which = form.get("carry_which")
        config["carry"] = {"item": int(first), "which": which if which in dict(WHICH) else "chosen"}
        if rest:
            config["carry"].update(col=col, value=int(value))
    else:
        config.pop("carry", None)
    item.config = config


def _column_answer(item, ref, col, value):
    """Whether "<col>:<value>" names an answer of a choice column of the side-by-side matrix `ref`: the same
    bounds as r_carry (1 to 20), and the column and answer must be there in the matrix."""
    from app.conditions import own_item
    from app.items.base import whole

    n = whole(value)
    if not col or len(col) > 40 or n is None or not 1 <= n <= 20:
        return False
    origin = own_item(item.block.survey, ref)
    if origin is None or origin.type != "side_by_side" or origin.id == item.id:
        return False
    from app.items.side_by_side import points_of

    found = next((c for c in origin.kind.columns(origin) if c.get("id") == col), None)
    return found is not None and found.get("kind") in ("single", "dropdown") and n <= points_of(found)


def sync(item, check=True):
    """Writes the options of the earlier question into `item` (see the module text). Returns True if anything
    changed. Without a source the carried rows go; the setting stays, so the pre-launch check can name it.
    A question with real answers in a survey opened for editing again keeps its rows when the new ones would
    change or drop a code (app/answer_lock.py: the earlier question deleted, its codes changed); only the
    wording follows. check=False: the caller checks the result itself (the element editor)."""
    key = key_of(item)
    if key is None:
        return False
    config = item.config or {}
    rows = config.get(key) or []
    own = [r for r in rows if not r.get("src")]
    carry, origin = config.get("carry"), source(item)
    if not carry and not carried(rows):
        return False
    new_rows, labels = [], {}
    if carry and origin is not None and origin.type == "side_by_side":
        from app.items.side_by_side import row_code

        for n, r in enumerate(origin.config.get("rows", [])):
            if r.get("src"):
                continue  # rows the matrix carries itself: not carried on
            rid = f"cf_{r['id']}"
            src = {"item": origin.id, "code": r["id"], "which": carry["which"], "col": carry.get("col", ""), "value": carry.get("value", 0)}
            code = row_code(n, r)
            if key == "statements":
                new_rows.append({"id": rid, "code": f"{item.code}_{code}", "reverse": False, "src": src})
            else:
                new_rows.append({"id": rid, "code": code, "src": src})
            labels[rid] = r["id"]
    elif carry and origin is not None:
        for o in origin.config.get("options", []):
            if o.get("missing"):
                continue  # "weiß nicht", "keine Angabe": no answer to ask about again
            rid = f"cf_{o['id']}"
            src = {"item": origin.id, "code": o["code"], "which": carry["which"], **({"text": True} if o.get("text") else {})}
            if key == "statements":
                new_rows.append({"id": rid, "code": f"{item.code}_{o['code']}", "reverse": False, "src": src})
            else:
                new_rows.append({"id": rid, "code": o["code"], "src": src})
            labels[rid] = o["id"]
    texts = {lang: dict(per) for lang, per in (item.texts or {}).items()}
    for lang, per in texts.items():
        # the wording of carried rows comes from the earlier question; that of rows no longer carried goes
        held = {k: v for k, v in (per.get(TEXTS[key]) or {}).items() if not k.startswith("cf_")}
        wording = ((origin.texts or {}).get(lang) or {}).get("rows" if origin.type == "side_by_side" else "options") or {} if origin is not None else {}
        for rid, oid in labels.items():
            if wording.get(oid):
                held[rid] = wording[oid]
        per[TEXTS[key]] = held
    new_config = {**config, key: new_rows + own}
    if check and new_config != config:
        from app import answer_lock

        survey = item.block.survey
        if answer_lock.refused_shape(survey, item, answer_lock.shape(item), answer_lock.shape_of(item, item.code, new_config)):
            new_config = config  # the rows stay; texts of rows still carried follow below
            for lang, per in texts.items():
                old = ((item.texts or {}).get(lang) or {}).get(TEXTS[key]) or {}
                per[TEXTS[key]] = {**{k: v for k, v in old.items() if k.startswith("cf_")},
                                   **{k: v for k, v in per[TEXTS[key]].items() if not k.startswith("cf_")
                                      or k in {r["id"] for r in rows}}}
    if new_config == config and texts == (item.texts or {}):
        return False
    item.config, item.texts = new_config, texts
    return True


def sync_survey(survey):
    """After a question or a translation was saved: every question that carries options takes them anew."""
    changed = False
    for block in survey.blocks:
        for item in block.items:
            if key_of(item) and ((item.config or {}).get("carry") or carried((item.config or {}).get(key_of(item)) or [])):
                changed = sync(item) or changed
    return changed


def hidden(row, answers):
    """Is this carried row hidden from a person with these answers so far? The earlier question never asked
    (skipped, or not reached): nothing was chosen or left, so nothing of it is shown."""
    from app.items.choice import selected

    src = row["src"]
    if src.get("which") == "all":
        return False
    if src.get("col"):  # a row of a side-by-side matrix: by the answer in one of its columns
        cells = answers.get(src["item"])
        value = ((cells.get(src["code"]) or {}) if isinstance(cells, dict) else {}).get(src["col"])
        if value is None or value == "dk":
            return True  # not answered there, or "weiß nicht": neither chosen nor another answer
        return (value == src.get("value")) != (src.get("which") == "chosen")
    answer = selected(answers.get(src["item"]))
    if answer is None or answer == "":
        return True
    codes = [str(a) for a in answer] if isinstance(answer, list) else [str(answer)]
    return (str(src["code"]) in codes) != (src.get("which") == "chosen")


def label(row, answers, fallback):
    """The wording of a carried option for this person: what they typed next to it ("Anderes: …"), else the
    option's own label."""
    from app.items.base import typed_text
    from app.items.choice import typed

    src = row.get("src") or {}
    if not src.get("text") or answers is None:
        return fallback
    text = typed_text(typed(answers.get(src["item"])).get(src["code"]))
    # what somebody typed is no placeholder: every "{" before a "{" is split off, so also "{{{pid}}}" keeps none
    # (a single replace("{{", "{ {") left "{ {{pid}}")
    return re.sub(r"\{(?=\{)", "{ ", text) if text else fallback


def applies(item, answers):
    """False if the question carries options and too few are left for this person (see the module text)."""
    from app import rows as row_logic

    rows = (item.config or {}).get(key_of(item) or "") or []
    if not carried(rows):
        return True
    gone = row_logic.hidden(rows, answers, item.survey)
    if not [r for r in carried(rows) if r["id"] not in gone]:
        return False
    return item.type not in NEEDS_TWO or len(rows) - len(gone) >= 2


def problems(item):
    """What blocks the start."""
    from app.lang import de_en

    config = item.config or {}
    carry = config.get("carry")
    if not carry:
        return []
    if source(item) is None:
        return [de_en("die Frage, aus der die Optionen übernommen werden, gibt es nicht mehr oder sie ist keine Auswahlfrage",
                      "the question the options are taken from no longer exists or is not a choice question")]
    if ref_of(carry) not in {ref for ref, _ in sources(item)}:
        return [de_en("die Frage, aus der die Optionen übernommen werden, muss auf einer früheren Seite stehen (und außerhalb einer Schleife)",
                      "the question the options are taken from must be on an earlier page (and outside a loop)")]
    rows = config.get(key_of(item)) or []
    taken = {r["code"] for r in carried(rows)}
    twice = [r["code"] for r in rows if not r.get("src") and r["code"] in taken]
    if twice:
        return [de_en("der Code „{code}“ einer eigenen Zeile gehört schon zu einer übernommenen Option; bitte einen anderen wählen",
                      "the code “{code}” of one of your own rows already belongs to an option carried over; please choose another",
                      code=twice[0])]
    return []


def remap(item_type, config, twin):
    """The settings for a copy of the questionnaire: the question of the copy; without it the carried rows go."""
    key = TARGETS.get(item_type)
    carry = config.get("carry")
    rows = config.get(key) or [] if key else []
    if not key or not (carry or carried(rows)):
        return config
    new = twin(carry["item"]) if carry else None
    if new is None:
        return {**{k: v for k, v in config.items() if k != "carry"}, key: [r for r in rows if not r.get("src")]}
    return {**config, "carry": {**carry, "item": new},
            key: [{**r, "src": {**r["src"], "item": new}} if r.get("src") else r for r in rows]}


def editor_view(item, lang=None):
    """What the editor's part "Optionen übernehmen" shows (items/_carry.html)."""
    from app.items.base import plain

    key = key_of(item)
    config = item.config or {}
    carry = config.get("carry") or {}
    offered = sources(item, lang)
    texts = item.text(TEXTS[key], lang) or {}
    rows = [(code, plain(texts.get(r["id"], "")) or code) for r in carried(config.get(key) or [])
            for code in [r["code"] if r["src"].get("col") else r["src"]["code"]]]
    return {"sources": offered, "known": {ref for ref, _ in offered}, "item": ref_of(carry),
            "which": carry.get("which") or "chosen", "choices": which(), "rows": rows, "statements": key == "statements"}
