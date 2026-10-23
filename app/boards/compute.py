"""Building and caching the state of a dashboard, step by step during fieldwork."""
import hashlib
import json
import math
import os
import time
from datetime import datetime, timedelta

from flask import current_app

from app.boards.base import (FORMAT, MIN_CELL, _CELL_MIN, _TILE_MIN, _coded, _coding_entry, _fill, _fixed_order, _num,
                             _pass_table, _pass_title, _question, _sbs_data, cell_min_of, item_ref, min_tile_of,
                             sbs_columns, shape_of, speaking)
from app.boards.tiles import (SPLIT_SHAPES, _cats, _compare, _conjoint, _dates, _factor_labels, _field, _groups,
                              _highlight, _level_means, _maxdiff, _multi, _nominal, _numbers, _paired, _prices, _quota,
                              _ranks, _reduce, _scale, _states, _words, by_frequency, pool_of, split_vars, tiles_of)
from app.extensions import db
from app.models import Response, Survey, utcnow
from app.ui_i18n import tr


def build(board, lang=None, only=None, until=None):
    """The state of the dashboard in a language of its survey (default: the survey's own): {"n", "mixed", "lang",
    "tiles": [...]}, every number already as a link may show it. only: (URL parameter, value) of a panel
    provider; then the figures of the fieldwork and the quotas count its participations alone, and the tiles
    of questions are left out (the difference between two links would show small groups). until: the step of a
    link (step_cutoff): only the participations that counted by then."""
    lang = lang or board.survey.default_language
    token, cells = _TILE_MIN.set(min_tile_of(board)), _CELL_MIN.set(cell_min_of(board))
    try:
        with speaking(lang):
            return _build(board, lang, only, until)
    finally:
        _TILE_MIN.reset(token)
        _CELL_MIN.reset(cells)


def _personal_params(survey):
    """Names of the URL parameters whose values are links per person: the ones with named values (panel.add_values)
    and the one a "Eigene Teilnahmen je Link" dashboard was made for."""
    from app import panel

    return set(panel.value_labels(survey)) | {(b.settings or {}).get("value_links") for b in survey.boards} - {None}


def _build(board, lang, only=None, until=None):
    from app import export

    survey = board.survey
    pool = pool_of(survey)
    if only:
        # a provider's link counts its real participations, whatever the data pool is: begun, completed and the
        # ones in the analysis are then figures of the same participations
        pool = {**pool, "sources": ["real"]}
    # a link per person (a named value of "Links je Person") never shows quotas: a cell going from 0 to 1 tells the
    # answer of the one person recruited, and the quota of "Höchstzahl je Link" lists the codes of all the other
    # links (R1, R2 of the audit 2026-10-09); a panel provider's link keeps them, all but that one (_public, V2)
    personal = bool(only) and only[0] in _personal_params(survey)
    tiles = [t for t in tiles_of(board) if not (only and t["key"].startswith("item:"))
             and not (personal and t["key"].startswith("quota:"))]
    table, excluded = None, 0
    if any(t["key"].startswith(("item:", "field:")) for t in tiles):
        table = export.rows(survey, pool["all"], pool["sources"], pool["speeder"], pool["speed_real"],
                            speed_sources=pool["sources"], with_hb=False, with_quality=False)
        if only:
            table = table.keep(lambda r: r.get("source", "real") == "real" and r.get(f"url_{only[0]}") == only[1])
        if until is not None:
            people = table.numbered
            table = table.keep(lambda r: in_step(people.get(r["resp_id"]), until, pool))
        # counted, not taken as the difference to the completed ones: with unfinished participations in the data
        # pool that difference was negative (D-4 of the maths audit 2026-10-03)
        excluded = sum(1 for r in table if r.get("qc_marked") == 1)
        table = table.keep(lambda r: r.get("qc_marked") != 1)
    items = {i.id: i for b in survey.blocks for i in b.items}
    splits = split_vars(survey, lang) if any(t.get("by") for t in tiles) else {}
    from app import coding

    # the state is one file for every viewer (members, public links, a lecture hall): while a blind second coding
    # runs, its column has no tile for anyone, whoever computed the state (audit 2026-10-10, R1)
    coded = coding.plan(survey, hide=coding.running(survey)) if survey.codings else {}
    out = []
    for tile in tiles:
        kind, _, ref = tile["key"].partition(":")
        if kind == "item":
            item_id, row_id = item_ref(tile["key"])
            item = items.get(item_id)
            entry = _coding_entry(coded, item, row_id) if item is not None else None
            shape = shape_of(item)[0] if item is not None else None
            if entry is not None:  # categories of an open answer: counts only, also of a gap text (cloze)
                shape = "cats" if entry[3]["single"] else "multi"
            if shape is None:
                continue
            maker = {"cats": _cats, "multi": _multi, "scale": _scale, "num": _numbers, "states": _states, "dist": _dates,
                    "score": _paired if item.type == "paired" else _maxdiff, "conjoint": _conjoint, "points": _prices, "words": _words, "highlight": _highlight,
                    "nums": _ranks if item.type in ("ranking", "constant_sum") else _numbers}.get(shape)
            make, variables = maker, None
            if entry is not None:
                make = _coded(entry)
            elif shape == "sbs":  # one column of a side-by-side matrix
                col = next((c for c in sbs_columns(item, lang) if c[0] == row_id and c[2]), None)
                if col is None:
                    continue
                shape = col[2]

                def make(item, people, lang, cid=row_id[2:]):
                    return _sbs_data(item, cid, people, lang)
            elif row_id and row_id.startswith("f-"):  # vignettes: the mean per level of a factor
                def make(item, people, lang, aid=row_id[2:]):
                    return _level_means(item, aid, people, lang)
            elif row_id:  # one pass of a loop: its columns, and its values in the texts
                from app import loops

                number = next((n for n, rid in export._loop_rows(item) or [] if rid == row_id), None)
                if number is None:
                    continue
                variables = loops.variables(item.block, row_id, lang)

                def make(item, people, lang, maker=maker, number=number):
                    return maker(item, _pass_table(people, number), lang)
            by = splits.get(tile["by"]) if tile.get("by") else None
            try:
                groups = _groups(make, item, by, table, lang) if by is not None and shape in SPLIT_SHAPES else None
                split = _compare(shape, groups, lang, make(item, table, lang) if shape == "scale" else None) if groups else None
                if split:
                    data = {"shape": shape, "few": False, "n": sum(g["n"] for g in split["groups"]), "split": split,
                            "by_title": _question(by.item, lang) if tile["by"].startswith("item:") else by.title}
                else:
                    data = _reduce(make(item, table, lang))
                    data["unsplit"] = by is not None and not data["few"]  # asked for, but too few answers per group
            except Exception:
                from flask import current_app

                if current_app.debug or current_app.testing:
                    raise
                # one tile that cannot be computed must not take the dashboard down, least of all behind a public link
                current_app.logger.exception("Dashboard %s: tile %s left out", board.id, tile["key"])
                continue
            if variables is not None:
                data = _fill(data, variables)
            if entry is not None:
                data["title"] = f"{_question(item, lang)} · {tr(lang, 'Kategorien', 'Categories')}" + (
                    f" · {entry[0]}" if len(coded.get(item.id) or []) > 1 else "")
            elif row_id and row_id.startswith("f-"):  # vignettes: the question and the factor it is split by
                from app import loops

                factor = next((label for a, label in _factor_labels(survey, loops.node_of(item.block), lang)
                               if a["id"] == row_id[2:]), "")
                data["title"] = f"{_question(item, lang)} · {factor}"
            elif item.type == "side_by_side":
                data["title"] = f"{_question(item, lang)} · {next(c[1] for c in sbs_columns(item, lang) if c[0] == row_id)}"
            else:
                data["title"] = _pass_title(item, row_id, lang) if row_id else _question(item, lang)
            data["code"] = entry[0] if entry is not None else item.code  # under the title where two tiles share it (X65)
        elif kind == "quota":
            data = _quota(survey, int(ref), only, until)
            if data is None:
                continue
        elif kind == "text":
            from app.items.base import rich

            heading = tile["kind"] == "heading"
            data = {"shape": "text", "few": False, "title": " ".join(tile["text"].split())[:200] if heading else "",
                    "html": "" if heading else str(rich(tile["text"]))}
        else:
            data = _field(survey, ref, table, pool, only, until, excluded)
        if tile["title"]:
            data["title"] = tile["title"]
        if data.get("rows") and not data.get("split") and _nominal(data):
            data["rows"] = by_frequency(data["rows"])
            if kind == "item" and entry is not None:  # "Sonstiges" before "nicht verwertbar", as on the results page
                _fixed_order(data["rows"], entry)
        if data.get("ranks") and not data["few"] and not data.get("split"):  # a ranking reads from the top
            sign = -1 if data["ranks"] == "norm" else 1  # normalised ranks: 1 = top
            data["rows"] = sorted(data["rows"], key=lambda r: (r["few"], sign * (r.get("mean") or 0)))
        out.append({**tile, **data})
    # two tiles with the same title (e.g. one question per price condition) are told apart by their variable name
    # (UI/UX audit 2026-10-09, X65)
    from collections import Counter

    same = Counter(t["title"] for t in out if t.get("title") and t.get("shape") != "text")
    for t in out:
        if t.get("code") and same.get(t.get("title"), 0) > 1:
            t["subtitle"] = t["code"]
    return {"n": len(table) if table is not None else None, "mixed": pool["sources"] != ["real"] or bool(pool["all"]),
            "lang": lang, "only": list(only) if only else None, "tiles": out}


def sample(state):
    """The state with invented numbers in the tiles that have too few answers yet, marked as examples: the
    designer shows what a display looks like before the fieldwork. Never part of what a link shows."""
    import copy
    import random

    state = copy.deepcopy(state)
    for tile in state["tiles"]:
        if not tile.get("few"):
            continue
        rnd = random.Random(tile["key"])
        shape = tile["shape"]
        if shape == "states":
            from app.items import federal_state

            counts = [rnd.randint(5, 60) for _ in federal_state.STATE_CODES]
            n = sum(counts)
            tile["rows"] = by_frequency([{"code": code, "label": label, "count": c, "share": c / n}
                                         for code, label, c in zip(federal_state.STATE_CODES, tile["labels"], counts)])
        elif shape == "score":
            scores = sorted((rnd.uniform(-0.7, 0.8) for _ in tile["rows"]), reverse=True)
            for row, score in zip(tile["rows"], scores):
                row["score"] = score
            n = 48
        elif shape == "conjoint":
            for attribute in tile["attributes"]:
                for level in attribute["levels"]:
                    level["share"] = rnd.uniform(0.15, 0.6)
                attribute["importance"] = rnd.uniform(0.1, 0.5)
            total = sum(a["importance"] for a in tile["attributes"]) or 1
            for attribute in tile["attributes"]:
                attribute["importance"] /= total
            tile["importances"] = sorted(({"label": a["label"], "importance": a["importance"]} for a in tile["attributes"]),
                                         key=lambda r: -r["importance"])
            tile["method"] = tile.get("method") or "logit"
            n = 48
        elif shape == "points":
            for row, value in zip(tile["rows"], ("9,90", "11,50", "7,20", "14,80")):
                row["value"] = value
            n = 48
        elif shape == "highlight":
            k = len(tile["cats"])
            for token, is_word in zip(tile["tokens"], tile.get("word", [])):
                if is_word:
                    counts = [rnd.choice((0, 0, rnd.randint(5, 30))) for _ in range(k)]
                    token.update(counts=counts, shares=[c / 60 for c in counts])
            tile["top"] = [[(t["text"], t["counts"][i]) for t in sorted((t for t in tile["tokens"] if t.get("counts") and t["counts"][i]),
                                                                       key=lambda t: -t["counts"][i])[:8]] for i in range(k)]
            n = 60
        elif shape == "words":
            words = ("Vertrauen", "Zukunft", "Hilfe", "Sorge", "neugierig", "praktisch", "teuer", "Datenschutz")
            counts = sorted((rnd.randint(5, 40) for _ in words), reverse=True)
            tile["rows"] = [{"label": w, "count": c, "share": c / 60, "size": c / counts[0]} for w, c in zip(words, counts)]
            n = 60
        elif shape == "dist":
            counts = [9, 14, 22, 17, 11, 7]
            n = sum(counts)
            tile["rows"] = [{"label": str(2020 + i), "count": c, "share": c / n} for i, c in enumerate(counts)]
        elif shape in ("cats", "multi"):
            counts = [rnd.randint(8, 60) for _ in tile["labels"]]
            n = sum(counts) if shape == "cats" else max(counts + [1]) + 12
            tile["rows"] = [{"label": label, "count": c, "share": c / n} for label, c in zip(tile["labels"], counts)]
            if _nominal(tile):
                tile["rows"] = by_frequency(tile["rows"])
        elif shape == "scale":
            k = len(tile["levels"])
            for row in tile["rows"]:
                counts = [rnd.randint(6, 40) for _ in range(k)]
                n = sum(counts)
                mean = sum((tile["first"] + i) * c for i, c in enumerate(counts)) / n
                row.update(few=False, n=n, mean=mean, sd=1.0, segs=[{"a": i, "b": i, "count": c, "share": c / n} for i, c in enumerate(counts)])
            n = max(r["n"] for r in tile["rows"]) if tile["rows"] else 0
        else:
            lo, hi = tile.get("bounds") or (0, 100)
            for row in tile["rows"]:
                row.update(few=False, n=48, mean=lo + (hi - lo) * rnd.uniform(0.3, 0.8), sd=(hi - lo) / 6)
                row["median"] = row["mean"]
            if shape == "num":
                counts = [6, 14, 17, 8, 3 + 2]
                step = (hi - lo) / 5
                tile["bins"] = [{"label": f"{_num(lo + i * step, 0)} bis unter {_num(lo + (i + 1) * step, 0)}", "count": c, "share": c / 50}
                                for i, c in enumerate(counts)]
            n = 48
        tile.update(few=False, sample=True, n=n)
    return state


# --- the kept state -------------------------------------------------------------------------------------


def _path(board, lang=None, only=None, live=False):
    """The file of the kept state: one per language (the survey's own language without a suffix) and per
    provider a link is for."""
    survey = board.survey
    suffix = f"-{lang}" if lang and lang != survey.default_language and lang in (survey.languages or []) else ""
    if only:
        suffix += "-p" + hashlib.sha256(f"{only[0]}={only[1]}".encode()).hexdigest()[:12]
    if live:
        # the newest state (the members' view, the designer, a lecture hall link) is made anew with every
        # participation and kept apart: the other links of the dashboard show their state in steps and must not
        # get the newest one through this file
        suffix += "-live"
    return os.path.join(current_app.config["UPLOAD_FOLDER"], "hb", survey.uid, f"board-{board.id}{suffix}.json")  # see maxdiff_hb.folder


def fingerprint(board, lang=None, only=None):
    """A hash of everything the state of this dashboard rests on: what the results rest on
    (results_store.fingerprint: participations of the pool, questionnaire, quality assurance, program), the tiles,
    the counters of the fieldwork and of the quotas, and the language."""
    from app import results_store
    from app.timeutil import to_local

    survey = board.survey
    basis = {
        "format": FORMAT, "results": results_store.fingerprint(survey, pool_of(survey)), "lang": lang or survey.default_language,
        "only": list(only) if only else None,
        "tiles": tiles_of(board), "limits": [min_tile_of(board), cell_min_of(board)],
        "tally": sorted([str(key), n] for key, n in survey.tally().items()), "target": survey.max_responses,
        "quotas": [[q.id, q.name, [[c.id, c.name, c.target, c.done, c.running, c.closed] for c in q.cells]] for q in survey.quotas],
        "day": str(to_local(utcnow()).date()),
        # a hierarchical Bayes estimation finished later gives the importance of a conjoint tile (_importances)
        "hb": _hb_stamps(board),
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


def _hb_stamps(board):
    """When the stored HB estimation of each conjoint question on the dashboard was last written (None: none)."""
    from app import maxdiff_hb

    items = {i.id: i for b in board.survey.blocks for i in b.items if i.type in ("conjoint", "acbc")}
    stamps = {}
    for tile in tiles_of(board):
        item = items.get(item_ref(tile["key"])[0]) if tile["key"].startswith("item:") else None
        if item is not None:
            try:
                stamps[item.id] = os.path.getmtime(maxdiff_hb._paths(item)[0])
            except OSError:
                stamps[item.id] = None
    return sorted(stamps.items())


def _read(board, lang=None, only=None, live=False):
    try:
        with open(_path(board, lang, only, live), encoding="utf-8") as f:
            kept = json.load(f)
    except (OSError, ValueError):
        return None
    return kept if isinstance(kept, dict) and kept.get("format") == FORMAT and isinstance(kept.get("tiles"), list) else None


LOCK_SECONDS = 120  # a lock older than this was left behind by a request that died
LOCK_WAIT, LOCK_STEP = 2.0, 0.2  # how long a request waits for the first state another one computes, and how often it looks
_checked = {}  # (board id, language, provider, newest) -> when this process last compared the fingerprint


def _pool_count(survey):
    """How many participations the data pool of the dashboards holds, counted by the database (before speeders and
    marked ones are taken out: a measure of how much has come in, not the n of a tile)."""
    from app.models import Response

    pool = pool_of(survey)
    return sum(n for (source, _, status), n in survey.tally().items()
               if source in pool["sources"] and (pool["all"] or status == Response.COMPLETED))


def _frame(board, lang, only):
    """A hash of what a state rests on besides the participations: tiles, language, questionnaire (version, texts
    and settings of the questions), quality assurance (marking rule), quotas."""
    from app import quality

    survey = board.survey
    basis = {"format": FORMAT, "lang": lang, "only": list(only) if only else None, "tiles": tiles_of(board),
             "limits": [min_tile_of(board), cell_min_of(board)], "qversion": survey.qversion, "status": survey.status, "pool": pool_of(survey),
             "rules": quality.rules(survey),
             "blocks": [[b.id, b.position, b.titles] for b in survey.blocks],
             "items": [[i.id, i.position, i.type, i.code, i.answer_mode, i.config, i.texts] for b in survey.blocks for i in b.items],
             "quotas": [[q.id, q.name, [[c.id, c.name, c.target, c.closed] for c in q.cells]] for q in survey.quotas]}
    if survey.codings:  # categorising changes no step: the schemes and the assignments (audit 2026-10-10, B-7)
        coded = db.session.execute(db.select(db.cast(Response.coding, db.Text)).filter(
            Response.survey_id == survey.id, Response.coding.is_not(None)).order_by(Response.id)).scalars()
        basis["codings"] = [survey.codings, hashlib.sha256("|".join(c or "" for c in coded).encode()).hexdigest()]
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


def stepped(board, only=None):
    """Whether a link of this dashboard shows its state in steps (step_cutoff): while the survey runs, for a
    dashboard with tiles of questions or of quotas. Two states one participation apart would show what that one
    person answered, or which cell of a quota the person fell into (the rules for small numbers protect a single
    state, not the difference of two). Not for a dashboard of figures of the fieldwork alone, nor for the link of
    a panel provider (no questions; its quotas count its own people, as its /q/ link does): nothing of a person
    is in them. And not for a link in the lecture hall mode (Ralf, 2026-10-02): there every answer shows at
    once, that is what the audience came for; the rules for small numbers hold there as everywhere. Its state
    has a file of its own (state(live=True)). Also once the survey is paused or ended (A-2 of the review of
    2026-10-03): the newest state at that moment could be one person beyond the last step, and pausing again and
    again would show each one; the step goes on only by at least MIN_CELL, as while it ran."""
    return (board.survey.status in (Survey.RUNNING, Survey.CLOSED) and not only
            and any(t["key"].startswith(("item:", "quota:")) for t in tiles_of(board)))


def in_step(response, until, pool):
    """Whether a participation counts in the step that ends at `until`: a completed one by the moment it was
    completed; with unfinished ones in the data pool ("all"), every one begun by then (what is there is shown,
    as the data pool says)."""
    if response is None:
        return False
    moment = response.started_at if pool["all"] else (response.completed_at if response.status == Response.COMPLETED else None)
    return moment is not None and moment <= until


def _step_path(survey):
    return os.path.join(current_app.config["UPLOAD_FOLDER"], "hb", survey.uid, "board-steps.json")  # never board-<id>*: forget()


def _read_step(path):
    """(moment, count, pool) of the kept step of a survey, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            kept = json.load(f)
        return datetime.fromisoformat(kept["at"]), int(kept["count"]), str(kept["pool"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def step_cutoff(survey):
    """The moment the links of a survey show the participations of (S3 of the security audit of 2026-10-03):
    one for the whole survey, kept in a small file, so that every language and every dashboard of its links
    rests on the same participations. Each language and each dashboard used to keep its own count, and the
    variant made anew a moment later showed one participation more than the other: the difference was the
    answer of that one person. A new step begins once the data pool holds at least MIN_CELL participations more
    than the step before counted. The moment lies BOARD_STEP_MARGIN seconds back, so a participation whose
    completion is stamped but not yet stored cannot slip into a step later; the count is taken after that
    moment and so is never too small (the next step is at least MIN_CELL further)."""
    path = _step_path(survey)
    pool = hashlib.sha256(json.dumps(pool_of(survey), sort_keys=True, default=str).encode()).hexdigest()
    count = _pool_count(survey)

    def valid():
        kept = _read_step(path)
        return kept[0] if kept is not None and kept[2] == pool and 0 <= count - kept[1] < MIN_CELL else None

    at = valid()
    if at is not None:
        return at
    lock = path + ".lock"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if time.time() - os.path.getmtime(lock) > LOCK_SECONDS:
                os.remove(lock)
        except OSError:
            pass
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        # another request makes the new step right now: take its moment instead of one of our own
        waited = 0.0
        while waited < LOCK_WAIT and os.path.exists(lock):
            time.sleep(LOCK_STEP)
            waited += LOCK_STEP
        kept = _read_step(path)
        if kept is not None and kept[2] == pool:
            return kept[0]  # the new step, or the one before if the other request died: both are steps
        lock = None
    except OSError:
        lock = None
    try:
        at = valid() if lock else None  # made by another request between our look and the lock
        if at is not None:
            return at
        at = utcnow() - timedelta(seconds=current_app.config.get("BOARD_STEP_MARGIN", 60))
        step = {"at": at.isoformat(), "count": _pool_count(survey), "pool": pool}
        tmp = f"{path}.{os.getpid()}.tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(step, f)
            os.replace(tmp, path)  # readers never see half a file
        except OSError as e:
            current_app.logger.warning("Step of the dashboards of survey %s not kept: %s", survey.id, e)
        return at
    finally:
        if lock:
            try:
                os.remove(lock)
            except OSError:
                pass


def _age(path):
    """Seconds since the file was written; endless if it is not there."""
    try:
        return max(time.time() - os.path.getmtime(path), 0)
    except OSError:
        return math.inf


def _public_seconds():
    """BOARD_PUBLIC_SECONDS: how old the state of a link is at least before it is made anew."""
    return current_app.config.get("BOARD_PUBLIC_SECONDS", 30)


def state(board, fresh=False, lang=None, only=None, steps=False, live=False):
    """The kept state of the dashboard in a language of its survey (default: the survey's own), made anew if
    what it rests on has changed. fresh: look now (the designer after saving); otherwise at most every
    BOARD_CHECK_SECONDS. While another request computes, the state from before is returned; if there is none yet,
    the request waits a moment for the other one's. steps: for a link (see stepped): its state rests on the
    participations up to the step of the survey (step_cutoff), the same for every language and dashboard, and is
    made anew only when that step or the dashboard itself changes; it is also kept for at least
    BOARD_PUBLIC_SECONDS, so a link held by many is not computed with every participation. live: the newest
    state, in a file of its own (the members' view, the designer, a lecture hall link); a link that moves in
    steps never reads it."""
    lang = lang if lang in (board.survey.languages or []) else board.survey.default_language
    only = tuple(only) if only else None
    if steps and (board.survey.data_pool or {}).get("_public_after_erase", 0) > _pool_count(board.survey):
        # Even cached public states cannot be shown after a deletion: hold all languages/links
        # until a new sufficiently large batch exists. Staff can still see their live view.
        return pending(lang, only)
    if not steps:
        from app import coding

        # the second coder sees the categories they code blind nowhere (coding.plan): never a state kept for others
        fresh = fresh or bool(coding.hidden(board.survey))
    kept = _read(board, lang, only, live)
    now = time.monotonic()
    wait = current_app.config.get("BOARD_CHECK_SECONDS", 3)
    if kept is not None and not fresh and now - _checked.get((board.id, lang, only, live), -1e9) < wait:
        return kept
    if kept is not None and steps and not fresh and _age(_path(board, lang, only)) < _public_seconds():
        return kept
    _checked[(board.id, lang, only, live)] = now
    until = step_cutoff(board.survey) if steps and stepped(board, only) else None
    if until is None:
        key = fingerprint(board, lang, only)
        if kept is not None and kept.get("key") == key:
            return kept
    else:
        # the step and what the state rests on besides the participations: never the fingerprint of a newest state,
        # so a look for that (the survey has ended) never takes the state of a step
        key = hashlib.sha256(f"step {until.isoformat()} {_frame(board, lang, only)}".encode()).hexdigest()
        if kept is not None and kept.get("key") == key:
            return kept
    path = _path(board, lang, only, live)
    lock = path + ".lock"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if time.time() - os.path.getmtime(lock) > LOCK_SECONDS:
                os.remove(lock)
        except OSError:
            pass
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        if kept is not None:
            return kept  # someone computes it right now
        # the first state: wait a moment for it instead of computing it a second time (a lecture hall opens the link
        # at once). If it does not come, the page says that it is being computed and asks again (board.js) instead
        # of computing it in parallel (performance audit 2026-10-06, P10: a hall full of new requests each built
        # the state); a lock left by a request that died is cleared after LOCK_SECONDS, then one computes anew
        waited = 0.0
        while waited < LOCK_WAIT:
            time.sleep(LOCK_STEP)
            waited += LOCK_STEP
            kept = _read(board, lang, only, live)
            if kept is not None:
                return kept
        return pending(lang, only)
    except OSError:
        lock = None
    try:
        new = {"format": FORMAT, "key": key, "at": utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
               "until": until.isoformat() if until is not None else None, "frame": _frame(board, lang, only),
               **build(board, lang, only, until=until)}
        if lock:
            tmp = f"{path}.{os.getpid()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(new, f)
                os.replace(tmp, path)  # readers never see half a file
            except OSError as e:
                current_app.logger.warning("State of dashboard %s not kept: %s", board.id, e)
        return new
    finally:
        if lock:
            try:
                os.remove(lock)
            except OSError:
                pass


PENDING = "pending"  # the key of a state that another request is still computing (pending())


def pending(lang, only=None):
    """A state without tiles that says the first one is being computed (state(): another request computes it); the
    page shows "wird berechnet" and asks again soon (board.js)."""
    return {"format": FORMAT, "key": PENDING, "pending": True, "at": utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            "until": None, "lang": lang, "only": list(only) if only else None, "tiles": [], "n": None}


def forget(board):
    """Remove the kept states of the dashboard: every language, every provider."""
    import glob

    for key in [k for k in _checked if k[0] == board.id]:
        _checked.pop(key, None)
    plain = _path(board)  # .../board-<id>.json
    for path in glob.glob(plain + "*") + glob.glob(plain[:-len(".json")] + "-*.json*"):
        try:
            os.remove(path)
        except OSError:
            pass
