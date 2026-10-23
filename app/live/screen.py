"""The screen and speaker view: results, texts, audience questions, moderation."""
import time
from collections import Counter

from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required

from app import units
from app.extensions import db
from app.live.base import (BINS, moderated, BOARD_TOP, CONTENT, MAX_MARKS, MAX_WORDS, POLL_SECONDS, WALL_MAX, _qr, _responses,
                           _survey, block_questions, blocks, bp, by_blocks, is_slide, join_url, options, pages,
                           qa_status, questions, state, type_label)
from app.live.scores import (_bad, _current, _key, _slide_image, deadline, leaderboard, qa_rows, quiz_of, reactions,
                             review, shared_places, team_board, emoji_counts, emoji_rows)
from app.ui_i18n import tr


@bp.route("/<code>/screen")
@login_required
def screen(code):
    survey = _survey(code)
    return render_template("live/screen.html", survey=survey, join=join_url(survey), qr=_qr(survey), poll=POLL_SECONDS)


@bp.route("/<code>/speaker")
@login_required
def presenter(code):
    """The presenter view for the own laptop (concept, improvement 7): the screen's question and results small, the
    next question, the notes, a clock, the same steering; the projector shows the screen (/screen)."""
    survey = _survey(code)
    return render_template("live/screen.html", survey=survey, join=join_url(survey), qr=_qr(survey), poll=POLL_SECONDS,
                           presenter=True)


@bp.route("/<code>/result.json")
@login_required
def results_json(code):
    survey = _survey(code)
    # the presenter view asks with view=speaker: only it learns what comes next, and only with the right to steer
    # (concept live-naechste-folie-konzept.md, E7: the projector would give away the next quiz question)
    speaker = request.args.get("view") == "speaker" and survey.can(current_user, "editor")
    return jsonify(_screen_state(survey, speaker=speaker))


@bp.route("/<code>/slides")
@login_required
def slides(code):
    """All questions with their results, one per page, to print or save as PDF (package G): the current run, or
    another one (?durchgang=<id>). Drawn by live.js with the views of the screen."""
    from app.items.base import plain

    survey = _survey(code)
    st = state(survey)
    runs = st["runs"] + [st["run"]]
    if not survey.can(current_user, "editor"):
        # one earlier run alone, or two side by side, filters the aggregates as ?durchgang= on the results page does:
        # "Ansehen" sees only the current run, the one the screen shows (AZ-1; Abgleich 2026-10-15, live filter)
        if request.args.get("vergleich") or request.args.get("durchgang", str(st["run"]["id"])) != str(st["run"]["id"]):
            abort(403)
        runs = [st["run"]]
    run = next((r for r in runs if str(r["id"]) == request.args.get("durchgang", "")), st["run"])
    # another run beside it (improvement 4): every slide twice, side by side
    other = next((r for r in runs if str(r["id"]) == request.args.get("vergleich", "") and r["id"] != run["id"]), None)
    lang = survey.default_language
    pages = []
    from app.live.ink import ink_of

    ink = ink_of(survey, run["id"])
    for item in questions(survey):
        if is_slide(item):  # a presentation slide: its picture with the pen notes of this run (live_ink.js, E9)
            pages.append({"question": "", "kind": "slide", "item": item.id, "image": _slide_image(survey, item),
                          "alt": plain(item.text("alt", lang)), "ink": ink.get(item.id, [])})
        elif item.type in CONTENT:
            pages.append({"question": "", "kind": "content", "html": render_template(
                "participant/_item.html", survey=survey, item=item, lang=lang, answers={}, errors={}, variables={})})
        else:
            page = {"question": plain(item.text("question", lang)), **results(survey, item, run["id"])}
            if other is not None:
                page["compare"] = results(survey, item, other["id"])
            pages.append(page)
    return render_template("live/slides.html", survey=survey, pages=pages, run=run, runs=list(reversed(runs)), other=other,
                           has_slides=any(is_slide(i) for i in questions(survey)), may_edit=survey.can(current_user, "editor"))


@bp.route("/<code>/qa.json")
@login_required
def qa_json(code):
    """All questions from the audience of the current run, with their status, for "Präsentieren" ("Ansehen": only
    those the phones see as well)."""
    survey = _survey(code)
    st = state(survey)
    rows = qa_rows(survey, st["run"]["id"], public=not survey.can(current_user, "editor"))
    return jsonify(enabled=options(survey)["qa"], screen=bool(st.get("qa_screen")),
                   questions=[{**r, "label": qa_status()[r["status"]]} for r in rows])


def audience_questions(survey, run_name="", shown_only=False):
    """The questions from the audience of all runs (or of the run with that name), oldest first: [{run, text, votes,
    status, label, at}] (export, results page). shown_only: only those the phones see too (open, answered), for
    "Ansehen"."""
    from app.models import LiveQuestion

    st = state(survey)
    names = {r["id"]: r["name"] for r in st["runs"] + [st["run"]]}
    gone = {int(k): v for k, v in (st.get("merged") or {}).items()}  # runs merged into another one (control._merge_run)
    places = {i.id: n for n, i in enumerate(questions(survey), start=1)}  # the slide's place: "folie" in the CSV
    out = []
    for r in db.session.scalars(db.select(LiveQuestion).filter_by(survey_id=survey.id).order_by(LiveQuestion.id)):
        name = names.get(r.run_id, "")
        if (run_name and name != run_name) or (shown_only and r.status not in ("open", "answered")):
            continue
        origin = r.origin_run_id if r.origin_run_id is not None else r.run_id
        out.append({"run": name, "origin": gone.get(origin) or names.get(origin, ""), "origin_id": origin, "text": r.text, "votes": r.votes, "status": r.status, "label": qa_status().get(r.status, r.status),
                    "at": r.created_at, "slide": places.get(r.item_id, "")})
    return out


@bp.route("/<code>/audience-questions.csv")
@login_required
def qa_csv(code):
    """The questions from the audience of all runs, as CSV (UTF-8 with BOM, semicolons, for Excel). From
    "Bearbeiten" on: it holds the waiting and hidden ones too."""
    import csv
    import io

    from flask import Response as FlaskResponse

    from app.export import safe_text

    survey = _survey(code, need="editor")
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["durchgang", "durchgang_ursprung", "frage", "stimmen", "status", "zeit", "folie"])
    for q in audience_questions(survey):
        writer.writerow([safe_text(q["run"]), safe_text(q["origin"]), safe_text(q["text"]), q["votes"], q["status"],
                         q["at"].isoformat(timespec="minutes"), q["slide"]])
    return FlaskResponse("\ufeff" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                         headers={"Content-Disposition": f'attachment; filename="fragen-publikum-{survey.code}.csv"'})


@bp.route("/<code>/reactions.csv")
@login_required
def reactions_csv(code):
    """The emojis of all runs counted per slide, as CSV (UTF-8 with BOM, semicolons): one row per run, slide and
    emoji; "folie" is the place in the list, empty before any slide (Ralf, 2026-10-16). Former taps on "Unklar"
    count as 😕."""
    import csv
    import io

    from flask import Response as FlaskResponse

    from app.export import safe_text

    survey = _survey(code, need="editor")
    st = state(survey)
    places = {i.id: n for n, i in enumerate(questions(survey), start=1)}
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["durchgang", "folie", "emoji", "anzahl"])
    for run in st["runs"] + [st["run"]]:
        per = emoji_counts(survey, run["id"])
        for item_id in sorted(per, key=lambda i: places.get(i, 0)):
            for emoji, n in emoji_rows(per[item_id]):
                writer.writerow([safe_text(run["name"]), places.get(item_id, ""), emoji, n])
    return FlaskResponse("\ufeff" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                         headers={"Content-Disposition": f'attachment; filename="reaktionen-{survey.code}.csv"'})


@bp.route("/<code>/moderation.json")
@login_required
def moderation_json(code):
    """The short texts of the question on the screen with their status, for the moderation on "Präsentieren"
    ("Ansehen": only the shown ones, not the waiting, hidden or filtered texts)."""
    from app.items.base import plain

    survey = _survey(code)
    st = state(survey)
    item = _current(survey, st)
    if not has_texts(item):
        return jsonify(item=None)
    entries = texts(survey, item, st["run"]["id"])
    if not survey.can(current_user, "editor"):
        entries = [e for e in entries if e["status"] == "shown"]
    return jsonify(item=item.id, question=plain(item.text("question", survey.default_language)), wall=item.id in st.get("wall", []),
                   premod=options(survey)["premod"], entries=entries)


def results(survey, item, run_id):
    """{"kind": "bars" | "words", "n", "rows": [(label, count[, extra])]} of one question in one run."""
    from app.items.base import plain
    from app.items.choice import selected

    lang = survey.default_language
    answers = [(r.answers or {}).get(str(item.id)) for r in _responses(survey, run_id)]
    answers = [a for a in answers if a not in (None, "", [])]
    if item.type in ("single_choice", "multiple_choice"):
        counts = Counter()
        for a in answers:
            chosen = selected(a)
            for code in (chosen if isinstance(chosen, list) else [chosen]):
                counts[code] += 1
        rows = [(plain(o["label"]) or o["code"], counts.get(o["code"], 0)) for o in item.kind.options(item, lang)]
        if not quiz_of(state(survey), item):  # answers without an order: the most frequent first (a quiz keeps its order)
            rows.sort(key=lambda r: -r[1])
        # what was typed at "Anderes, und zwar …": under the bars as posts, moderated like short texts (Ralf, 2026-10-16)
        notes = [(e["text"], e["count"], {"key": e["key"]}) for e in texts(survey, item, run_id) if e["status"] == "shown"]
        return {"kind": "bars", "n": len(answers), "rows": rows, **({"notes": notes[:WALL_MAX]} if notes else {})}
    if item.type == "rating":
        lo, hi = item.kind.bounds(item)
        counts = Counter(a for a in answers if isinstance(a, (int, float)))
        points = [lo + k for k in range(int(hi - lo) + 1)] if item.kind.is_integer(item) else sorted(counts)
        rows = [(str(int(p)) if float(p).is_integer() else str(p), counts.get(p, 0)) for p in points]
        mean = units.round_half_up(sum(a * c for a, c in counts.items()) / max(1, sum(counts.values())), 1) if counts else None
        if len(points) > 11 or not item.kind.is_integer(item):  # e.g. a slider from 0 to 100: ten classes, not a bar per value
            width = (hi - lo) / BINS or 1
            classes = Counter()
            for value, n in counts.items():
                classes[units.class_of(value, lo, width, BINS)] += n  # a value on an edge: the upper class (R-4)
            rows = list(zip(_classes(lo, width, lang), [classes.get(k, 0) for k in range(BINS)]))
            return {"kind": "bars", "n": len(answers), "rows": rows, "mean": mean}
        style = (item.config or {}).get("style")
        if style == "smileys" and len(rows) > 1:  # the faces the participants chose from, as the bars' labels
            rows = [(label, n, {"face": item.kind.smiley(k, len(rows))}) for k, (label, n) in enumerate(rows)]
        elif style == "stars":
            rows = [(label, n, {"stars": k + 1}) for k, (label, n) in enumerate(rows)]
        return {"kind": "bars", "n": len(answers), "rows": rows, "mean": mean}
    if item.type == "semantic":  # per pair: the share of answers on each point between the two poles, and the mean
        points = int(item.config.get("points", 7))
        rows = []
        for pair in item.kind.pairs(item, lang):
            values = [a.get(pair["id"]) for a in answers if isinstance(a, dict)]
            values = [v for v in values if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= points]
            rows.append({"left": plain(pair["left"]), "right": plain(pair["right"]),
                         "counts": [values.count(k) for k in range(1, points + 1)], "mean": _mean(values)[0]})
        return {"kind": "semdiff", "n": len(answers), "points": points, "rows": rows}
    if item.type == "likert":  # the mean of each statement on the scale, the scale's ends as labels
        labels = [plain(l) for l in item.kind.labels(item, lang)]
        rows = [(plain(st["text"]) or st["export_code"], *_mean([a.get(st["id"]) for a in answers if isinstance(a, dict)]))
                for st in item.kind.statements(item, lang)]
        return {"kind": "means", "n": len(answers), "rows": rows, "lo": 1, "hi": item.config["points"],
                "ends": [labels[0], labels[-1]] if labels else ["", ""]}
    if item.type == "ranking":  # the mean rank of each option, the best first
        opts = item.kind.options(item, lang)
        ranks = {o["code"]: [] for o in opts}
        for a in answers:
            for n, code in enumerate(a if isinstance(a, list) else [], start=1):
                if code in ranks:
                    ranks[code].append(n)
        rows = sorted(((plain(o["label"]) or o["code"], *_mean(ranks[o["code"]])) for o in opts),
                      key=lambda r: (r[1] is None, r[1] or 0))
        return {"kind": "ranks", "n": len(answers), "rows": rows, "lo": 1, "hi": max(len(opts), 1)}
    if item.type == "constant_sum":  # the mean points of each option, the most first
        opts = item.kind.options(item, lang)
        rows = sorted(((plain(o["label"]) or o["code"], *_mean([a.get(o["code"]) for a in answers if isinstance(a, dict)]))
                       for o in opts), key=lambda r: -(r[1] or 0))
        total = max([sum(v for v in a.values() if isinstance(v, (int, float))) for a in answers if isinstance(a, dict)] or [100])
        return {"kind": "means", "n": len(answers), "rows": rows, "lo": 0, "hi": total or 100, "ends": ["", ""]}
    if item.type == "slider":
        lo, hi = float(item.config.get("min", 0)), float(item.config.get("max", 100))
        sliders = item.kind.rows(item, lang)
        if len(sliders) > 1:  # several sliders: the mean of each on the slider's range
            rows = [(plain(r["text"]) or r["export_code"], *_mean([a.get(r["id"]) for a in answers if isinstance(a, dict)]))
                    for r in sliders]
            return {"kind": "means", "n": len(answers), "rows": rows, "lo": lo, "hi": hi,
                    "ends": [plain(item.t(lang).get("left", "")), plain(item.t(lang).get("right", ""))]}
        values = [a.get(sliders[0]["id"]) for a in answers if isinstance(a, dict) and sliders] if sliders else []
        values = [v for v in values if isinstance(v, (int, float))]
        width = (hi - lo) / BINS or 1
        counts = Counter(units.class_of(v, lo, width, BINS) for v in values)  # a value on an edge: the upper class (R-4)
        rows = list(zip(_classes(lo, width, lang), [counts.get(k, 0) for k in range(BINS)]))
        mean = _mean(values)[0]
        median = sorted(values)[len(values) // 2] if len(values) % 2 else (
            (sorted(values)[len(values) // 2 - 1] + sorted(values)[len(values) // 2]) / 2 if values else None)
        return {"kind": "bars", "n": len(answers), "rows": rows, "mean": mean,  # _mean: already to one place
                "median": units.round_half_up(median, 1)}
    if item.type == "image_mark":  # all marks over the image: spots as a heat map, lines as faint strokes
        from flask import url_for as link

        asset = item.kind.asset_for(item, {})
        marks = [m for a in answers if isinstance(a, list) for m in a if isinstance(m, list)][:MAX_MARKS]  # no hot spot codes
        return {"kind": "marks", "n": len(answers), "mode": item.config.get("mode", "points"), "marks": marks,
                "color": item.config.get("color") or "#ffd400",
                "image": link("media.serve", survey_uid=survey.uid, filename=asset.filename) if asset else None}
    if item.type == "position2d":  # the mean place of each term in the field, with the axes' words
        t = item.t(lang)
        points = []
        for o in item.kind.options(item, lang):
            places = [a.get(o["code"]) for a in answers if isinstance(a, dict) and isinstance(a.get(o["code"]), list)]
            if places:
                points.append({"label": plain(o["label"]) or o["code"], "n": len(places),
                               "x": units.round_half_up(sum(p[0] for p in places) / len(places), 1),
                               "y": units.round_half_up(sum(p[1] for p in places) / len(places), 1)})
        return {"kind": "points", "n": len(answers), "points": points,
                "axes": {k: plain(t.get(k, "")) for k in ("x_label", "x_low", "x_high", "y_label", "y_low", "y_high")}}
    # short texts: only those the moderation lets through (texts()); as a wall of posts or as a word cloud
    entries = [e for e in texts(survey, item, run_id) if e["status"] == "shown"]
    if item.id in state(survey).get("wall", []):
        return {"kind": "wall", "n": len(answers), "rows": [(e["text"], e["count"], {"key": e["key"]}) for e in entries[:WALL_MAX]]}
    # word cloud: what was typed, as it was typed (lower-cased to count "KI" and "ki" together)
    counts, shown = Counter(), {}
    for e in entries:
        word = e["text"][:60]
        counts[word.casefold()] += e["count"]
        shown.setdefault(word.casefold(), (word, e["key"]))
    return {"kind": "words", "n": len(answers),
            "rows": [(shown[k][0], c, {"key": shown[k][1]}) for k, c in counts.most_common(MAX_WORDS)]}


def texts(survey, item, run_id):
    """The short texts sent to a question in a run, most often first: [{"key", "text", "count", "status"}], status
    "shown", "hidden" (by the presenter), "pending" (waiting for approval, "premod") or "filtered" (swear word)."""
    st, opts = state(survey), options(survey)
    hidden, approved = moderated(st, item.id, run_id)
    counts, shown = Counter(), {}
    for r in _responses(survey, run_id):
        value = (r.answers or {}).get(str(item.id))
        if value in (None, "", []):
            continue
        for text in _typed_texts(item, value):
            text = " ".join(str(text).split())[:280]
            if not text:
                continue
            counts[_key(text)] += 1
            shown.setdefault(_key(text), text)
    return [{"key": key, "text": shown[key], "count": count, "status": _status(key, hidden, approved, opts)}
            for key, count in counts.most_common()]


def _typed_texts(item, value):
    """The texts of one answer: the short text itself, or at a choice what was typed at "Anderes, und zwar …"."""
    if item.type in ("single_choice", "multiple_choice"):
        from app.items.choice import typed

        return list(typed(value).values())
    return [value]


def has_texts(item):
    """Whether the moderation of short texts applies to this question: a short text, or a choice with a typed answer."""
    if item is None:
        return False
    return item.type == "text_input" or (item.type in ("single_choice", "multiple_choice") and bool(item.kind.text_options(item)))


def _status(key, hidden, approved, opts):
    """What the moderation makes of a short text (texts())."""
    if key in hidden:
        return "hidden"
    if key in approved:
        return "shown"
    if opts["filter"] and _bad(key):
        return "filtered"
    return "pending" if opts["premod"] else "shown"


def result_clouds(survey, data, viewer=False, run_id=None):
    """The word clouds of the results page (surveys/results.html, Ralf 2026-10-16): each short-text question of the
    vote that the screen shows as a word cloud (not as a wall) gets column["cloud"] = [(word, count)] beside its
    list, from the answers the page counts (its data pool and run), as the screen draws it: only what the moderation
    lets through, "KI" and "ki" together, at most MAX_WORDS. The role "Ansehen" (viewer) gets only words that at
    least VIEWER_MIN people typed, as with the terms of an association. Changes `data` in place."""
    from app.results import VIEWER_MIN

    st, opts = state(survey), options(survey)
    walls = {str(i) for i in st.get("wall", [])}
    for entry in data.get("items", []):
        item = entry.get("item")
        if getattr(item, "type", None) != "text_input" or str(item.id) in walls:
            continue
        hidden, approved = moderated(st, item.id, run_id)
        for column in entry["columns"]:
            if not (column.get("free") and column.get("texts")):
                continue
            counts, shown = Counter(), {}
            for text, n in column["texts"]["rows"]:
                if _status(_key(text), hidden, approved, opts) != "shown":
                    continue
                word = " ".join(str(text).split())[:60]
                counts[word.casefold()] += n
                shown.setdefault(word.casefold(), word)
            rows = [(shown[k], c) for k, c in counts.most_common(MAX_WORDS) if not viewer or c >= VIEWER_MIN]
            if rows:
                column["cloud"] = rows


def _mean(values):
    """(mean rounded half up to one place, number of values) of the numbers among `values`; (None, 0) without any.
    Half up like everywhere (2 and 2.5: 2.3, not 2.2 as round() gives; audit 2026-10-05, R-5)."""
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return (units.round_half_up(sum(nums) / len(nums), 1), len(nums)) if nums else (None, 0)


def _classes(lo, width, lang="de"):
    """The BINS classes of a histogram for the screen ("0–10", ...): all borders whole numbers without places, else
    all with one place ("0,0–1,5", "1,5–3,0"), so the classes read alike; written by units.number (a minus sign, the
    language's decimal mark) and with spaces around the dash where a border is negative ("−1,0 – −0,8"; R-4)."""
    borders = [lo + k * width for k in range(BINS + 1)]
    whole = all(abs(v - round(v)) < 1e-9 for v in borders)
    text = [units.number(v, 0 if whole else 1, lang) for v in borders]
    dash = " – " if any(t.startswith("−") for t in text) else "–"
    return [f"{text[k]}{dash}{text[k + 1]}" for k in range(BINS)]




def _next_slide(survey, st, item, lang):
    """The box "Als Nächstes" in the presenter view (concept live-naechste-folie-konzept.md): a presentation slide as
    its picture, a question as its text, type and first answers, the end after the last slide. "later_block": in "In
    Blöcken" the next slide belongs to a block not running yet (E4)."""
    from app.items.base import plain

    if item is None:
        return {"kind": "end"}
    out = {"id": item.id, "type": type_label(survey, item)}
    if by_blocks(survey):
        block = next((b for b in blocks(survey) if any(i.id == item.id for i in block_questions(b))), None)
        out["later_block"] = block is not None and block.id != st.get("block")
    if is_slide(item):
        return {**out, "kind": "slide", "image": _slide_image(survey, item), "alt": plain(item.text("alt", lang))}
    if item.type in CONTENT:
        return {**out, "kind": "content", "question": plain(item.text("question", lang)) or plain(item.text("alt", lang)),
                "image": _slide_image(survey, item)}
    labels = [plain(o.get("label") or "") for o in (item.kind.options(item, lang) if hasattr(item.kind, "options") else [])]
    labels = [label for label in labels if label]
    # every answer option, not only the first three (Ralf, 2026-10-23: the presenter must see them all)
    return {**out, "kind": "question", "question": plain(item.text("question", lang)), "options": labels, "more": 0}


def _screen_state(survey, speaker=False):
    from app.items.base import plain

    st = state(survey)
    lang = survey.default_language
    item = next((i for i in questions(survey) if i.id == st["screen"]), None)
    out = {"title": survey.title, "current": None, "show": st["show"], "run": st["run"]["name"],
           "wall": item is not None and item.id in st.get("wall", []), "text": item is not None and item.type == "text_input",
           "open": item is not None and item.id not in st["closed"],
           # the presenter's panel: a dot per question, the one on the screen marked, closed ones dimmed
           "outline": [{"id": i.id, "title": plain(i.text("question", lang))[:80], "screen": i.id == st["screen"],
                        "closed": i.id in st["closed"]} for i in questions(survey)]}
    ids = [i for i in questions(survey)]
    at = next((n for n, i in enumerate(ids) if i.id == st["screen"]), None)
    following = ids[at + 1] if at is not None and at + 1 < len(ids) else (ids[0] if at is None and ids else None)
    if speaker:
        out["next"] = _next_slide(survey, st, following, lang) if ids else None
    elif is_slide(following):  # the projector only loads the picture of a next presentation slide ahead (E8), unseen
        out["preload"] = _slide_image(survey, following)
    if by_blocks(survey):  # the presenter's panel: a box per block (the running one marked) with its questions
        out["blocks"] = [{"id": b.id, "title": b.title(lang), "active": b.id == st.get("block"), "answerable": bool(pages(b)),
                          "questions": [{"id": i.id, "title": plain(i.text("question", lang))[:80], "screen": i.id == st["screen"]}
                                        for i in block_questions(b)]}
                         for b in blocks(survey) if block_questions(b)]
    out["qr"] = bool(st.get("qr"))
    out["notes"] = (st.get("notes") or {}).get(str(st["screen"]), "") if st["screen"] else ""
    out["started"] = st.get("started_at")
    out["position"] = [at + 1 if at is not None else 0, len(ids)]
    out["qa"] = options(survey)["qa"]
    # with Q&A on, its open questions stay beside every slide, the most voted first (Ralf, 2026-10-03: permanently
    # visible, not only in turn with the results)
    out["qa_side"] = [r for r in qa_rows(survey, st["run"]["id"], public=True) if r["status"] == "open"][:8] if out["qa"] else []
    out["reactions"] = reactions(survey) if options(survey)["reactions"] else []
    out["quiz"] = any(quiz_of(st, i) for i in questions(survey))
    out["emojis_on"] = options(survey)["reactions"]
    if out["emojis_on"]:  # the emojis of the slide on the screen, counted: only the presenter view shows them (E5)
        out["emojis"] = emoji_rows(emoji_counts(survey, st["run"]["id"]).get(st["screen"], {}))
    if st.get("review_screen"):  # the review at the end, grouped by slide
        rows = review(survey, st["run"]["id"], by=st.get("review_sort", "slides"))
        out["current"] = {"id": "review", "question": tr(lang, "Rückblick", "Review"), "type": "review", "kind": "review",
                          "n": sum(len(r["questions"]) for r in rows), "rows": rows, "sort": st.get("review_sort", "slides"),
                          "empty": tr(lang, "Noch keine Rückmeldungen.", "No feedback yet.")}
        out["show"] = True
        return out
    if st.get("board_screen"):  # the leaderboard of the quiz
        board = leaderboard(survey, st)
        teams = team_board(board)
        out["current"] = {"id": "board", "question": tr(lang, "Rangliste", "Leaderboard"), "type": "board", "kind": "board", "n": len(board),
                          "rows": [{"name": name, "points": total, "place": place}
                                   for (_, name, total, _), place in zip(board[:BOARD_TOP], shared_places([e[2] for e in board]))],
                          "teams": [{"name": t, "points": p, "members": m, "place": place}
                                    for (t, p, m), place in zip(teams, shared_places([e[1] for e in teams]))]}
        out["show"] = True
        return out
    if st.get("qa_screen"):  # the questions from the audience instead of the slide, the most voted first
        rows = qa_rows(survey, st["run"]["id"], public=True)
        out["current"] = {"id": "qa", "question": tr(lang, "Fragen aus dem Publikum", "Questions from the audience"), "type": "qa", "kind": "qa", "n": len(rows),
                          "rows": rows}
        out["show"] = True
        return out
    if is_slide(item):  # a page of a PDF: the picture over the whole screen, the QR code small in a corner
        from app.live.ink import revisions

        rev, active = revisions(survey, st["run"]["id"]).get(item.id, (0, False))
        out["current"] = {"id": item.id, "question": "", "type": item.type, "kind": "slide", "image": _slide_image(survey, item),
                          "alt": plain(item.text("alt", lang)),
                          # the pen notes (app/live/ink.py): their revision, drawn just now; the audience view shows them
                          # unless switched off in the presenter view
                          "ink": {"rev": rev, "active": active, "screen": not st.get("ink_off")}}
        out["corner"] = item.id not in st.get("corner_off", [])
        return out
    if item is not None and item.type in CONTENT:  # a text or image slide, drawn as the phones would draw it
        out["current"] = {"id": item.id, "question": "", "type": item.type, "kind": "content",  # the text is in the slide
                          "html": render_template("participant/_item.html", survey=survey, item=item, lang=lang, answers={},
                                                  errors={}, variables={})}
        return out
    if item is not None:
        res = results(survey, item, st["run"]["id"])
        quiz = quiz_of(st, item)
        if quiz and res.get("kind") == "bars" and item.type != "slider":  # the right answers marked once shown
            codes = [o["code"] for o in item.kind.options(item, lang)]
            res["rows"] = [(*r[:2], {**(r[2] if len(r) > 2 else {}), "right": code in quiz["correct"]})
                           for r, code in zip(res["rows"], codes)]
        if quiz and item.type == "slider":
            res["target"] = quiz["value"]
        end = deadline(st, item)
        if end:
            out["left"] = max(0, round(end - time.time()))
        out["open"] = out["open"] and not (end and time.time() > end)
        out["current"] = {"id": item.id, "question": plain(item.text("question", lang)), "type": item.type,
                          **(res if st["show"] else {"n": res["n"]})}
    return out
