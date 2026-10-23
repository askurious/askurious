"""Figures of a run used by the screen and the phones: quiz points, leaderboard, Q&A, emojis per slide."""
import time
from collections import Counter

from flask import url_for

from app import units
from app.extensions import db
from app.live.base import (BAD_WORDS, CONTENT, POINTS, QUIZ_TYPES, REACTION_CHOICES, REACTION_SHOWN, UNCLEAR_EMOJI,
                           _responses, questions)
from app.models import utcnow
from app.ui_i18n import tr


# --- results --------------------------------------------------------------------------------------------------


def _counts(survey, run_id):
    """({item id: number of answers}, number of participants) in a run, for the editing page."""
    out, people = Counter(), 0
    for r in _responses(survey, run_id):
        people += not (r.params or {}).get("live_extra")  # a further word of the same device is no other person
        for k, v in (r.answers or {}).items():
            if v not in (None, "", []) and str(k).isdigit():
                out[int(k)] += 1
    return out, people


def _key(text):
    return " ".join(str(text).split())[:280].casefold()


def _bad(text):
    """A word of BAD_WORDS in the text, also written with look-alike letters: full-width, ligatures, mathematical
    letters and the like count as the plain ones (NFKC; Abgleich 2026-10-15, TN-13)."""
    import re
    import unicodedata

    return any(w in BAD_WORDS for w in re.findall(r"[\w']+", unicodedata.normalize("NFKC", text).casefold()))


def _current(survey, st):
    return next((i for i in questions(survey) if i.id == st["screen"]), None)


def quiz_of(st, item):
    """The quiz settings of a question, or None: {"correct": [codes]} or {"value", "tol"}, and "seconds"."""
    entry = (st.get("quiz") or {}).get(str(item.id)) if item is not None and item.type in QUIZ_TYPES else None
    if not entry or (item.type == "slider" and entry.get("value") is None) or (item.type != "slider" and not entry.get("correct")):
        return None
    return entry


def deadline(st, item):
    """When answers to a quiz question with a countdown end (time.time()), or None."""
    quiz = quiz_of(st, item)
    opened = (st.get("opened") or {}).get(str(item.id)) if item is not None else None
    return opened + quiz["seconds"] if quiz and quiz.get("seconds") and opened else None


def is_right(item, quiz, value):
    from app.items.choice import selected

    if value in (None, "", []):
        return False
    if item.type == "slider":
        v = next(iter(value.values()), None) if isinstance(value, dict) else None
        return isinstance(v, (int, float)) and abs(v - quiz["value"]) <= (quiz.get("tol") or 0)
    chosen = selected(value)
    chosen = set(chosen if isinstance(chosen, list) else [chosen])
    return chosen == set(quiz["correct"]) if item.type == "multiple_choice" else bool(chosen & set(quiz["correct"]))


def points(item, quiz, value, seconds_taken):
    """POINTS for a right answer; with a countdown from POINTS / 2 (at the end) to POINTS (at once)."""
    if not is_right(item, quiz, value):
        return 0
    if quiz.get("seconds") and seconds_taken is not None:
        return units.round_half_up(POINTS / 2 + POINTS / 2 * max(0.0, 1 - seconds_taken / quiz["seconds"]))
    return POINTS


_CACHE, _CACHE_LOCK = {}, __import__("threading").Lock()


def _cached(key, seconds, make):
    """make() kept for a few seconds per server process: what every phone asks for at once (the leaderboard after a
    quiz question) is computed once per process and moment, not once per phone (measured 2026-10-03: 200 phones,
    p95 8 s without it)."""
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < seconds:
            return hit[1]
    value = make()
    with _CACHE_LOCK:
        if len(_CACHE) > 500:
            _CACHE.clear()
        _CACHE[key] = (now, value)
    return value


def leaderboard(survey, st, fresh=False):
    """The leaderboard, kept for 3 s (_cached) unless fresh: the screen shows it, every phone asks for its place."""
    key = ("board", survey.id, st["run"]["id"], st.get("at"))
    return _leaderboard(survey, st) if fresh else _cached(key, 3, lambda: _leaderboard(survey, st))


def _leaderboard(survey, st):
    """[(response id, name, points)] of the current run, best first; a person without a nickname is
    "Anonym" in the language of the vote (decision L3: the nickname is voluntary and only for this list, never in the export)."""
    items = [i for i in questions(survey) if quiz_of(st, i)]
    out = []
    for r in _responses(survey, st["run"]["id"]):
        if (r.params or {}).get("live_extra"):
            continue
        times = (r.layout or {}).get("live_times") or {}
        total = sum(points(i, quiz_of(st, i), (r.answers or {}).get(str(i.id)), times.get(str(i.id))) for i in items)
        out.append((r.id, (r.layout or {}).get("live_nick") or tr(survey.default_language, "Anonym", "Anonymous"), total,
                    (r.layout or {}).get("live_team") or ""))
    return sorted(out, key=lambda e: (-e[2], e[0]))


def team_board(board):
    """[(team, mean points, members)] of the people who chose a team, best first (the mean, so a large team has no
    head start)."""
    teams = {}
    for _, _, total, team in board:
        if team:
            teams.setdefault(team, []).append(total)
    return sorted(((t, units.round_half_up(sum(v) / len(v)), len(v)) for t, v in teams.items()), key=lambda e: -e[1])


def shared_places(scores):
    """The place of each score in a list sorted best first: 1 + the number of higher scores, so the same points
    share a place ("1, 1, 3": standard competition ranking; audit 2026-10-05, R-6)."""
    out = []
    for n, score in enumerate(scores):
        out.append(out[-1] if n and score == scores[n - 1] else n + 1)
    return out


def qa_rows(survey, run_id, public=False):
    """The questions from the audience in a run: the most voted first, answered ones after the open ones.
    public: only open and answered ones (phones, screen); else all with their status (Präsentieren)."""
    from app.models import LiveQuestion

    rows = db.session.scalars(db.select(LiveQuestion).filter_by(survey_id=survey.id, run_id=run_id)).all()
    if public:
        rows = [r for r in rows if r.status in ("open", "answered")]
    rows.sort(key=lambda r: (r.status == "answered", -r.votes, r.id))
    return [{"id": r.id, "text": r.text, "votes": r.votes, "status": r.status} for r in rows]


def emoji_counts(survey, run_id):
    """{slide id (None: before any slide): {emoji: taps}} in a run, counted by the database. A tap on the former button
    "Unklar" counts as its emoji 😕 (UNCLEAR_EMOJI)."""
    from app.models import LiveFeedback

    rows = db.session.execute(db.select(LiveFeedback.item_id, LiveFeedback.kind, db.func.count(LiveFeedback.id)).filter(
        LiveFeedback.survey_id == survey.id, LiveFeedback.run_id == run_id).group_by(LiveFeedback.item_id, LiveFeedback.kind))
    out = {}
    for item_id, kind, n in rows:
        emoji = kind[2:] if kind.startswith("e:") else UNCLEAR_EMOJI if kind == "unclear" else None
        if emoji:
            counts = out.setdefault(item_id, {})
            counts[emoji] = counts.get(emoji, 0) + n
    return out


def emoji_rows(counts):
    """[[emoji, taps]] of one slide, the most often first (ties in the order of REACTION_CHOICES)."""
    order = list(REACTION_CHOICES)
    return [[e, n] for e, n in sorted(counts.items(), key=lambda x: (-x[1], order.index(x[0]) if x[0] in order else 99))]


def _forget_feedback(survey, run_id):
    """The emojis per slide and the presenter's pen notes of a run (deleted run, "Alles zurück")."""
    from app.models import LiveFeedback, LiveInk

    db.session.execute(db.delete(LiveFeedback).where(LiveFeedback.survey_id == survey.id, LiveFeedback.run_id == run_id))
    db.session.execute(db.delete(LiveInk).where(LiveInk.survey_id == survey.id, LiveInk.run_id == run_id))


def _slide_image(survey, item):
    """The address of an image slide's picture (a thumbnail in the review), or None."""
    if item.type != "image":
        return None
    asset = item.kind.asset_for(item, {})
    return url_for("media.serve", survey_uid=survey.uid, filename=asset.filename) if asset else None


def review(survey, run_id, by="slides"):
    """The review at the end of a talk (concept konzept-live-feedback-praesentation-2026-10-12.md): per slide with
    feedback the emojis sent while it was on the screen, each counted, and the questions asked meanwhile, open and
    answered ones, the most voted first. Questions and emojis before any slide (or on a slide since deleted) stand first,
    as "Allgemein". by="unclear": the slides with the most emojis first. [{"id", "pos", "title", "image", "emojis":
    [[emoji, n]], "unclear" (all emojis of the slide), "questions": [{"id", "text", "votes", "status"}]}]"""
    from app.items.base import plain
    from app.models import LiveQuestion

    lang = survey.default_language
    items = questions(survey)
    known = {i.id for i in items}
    counts = {}
    for item_id, per in emoji_counts(survey, run_id).items():
        key = item_id if item_id in known else None
        for emoji, n in per.items():
            counts.setdefault(key, {})[emoji] = counts.get(key, {}).get(emoji, 0) + n
    asked = {}
    rows = db.session.scalars(db.select(LiveQuestion).filter(
        LiveQuestion.survey_id == survey.id, LiveQuestion.run_id == run_id, LiveQuestion.status.in_(("open", "answered"))))
    for r in sorted(rows, key=lambda r: (r.status == "answered", -r.votes, r.id)):
        asked.setdefault(r.item_id if r.item_id in known else None, []).append(
            {"id": r.id, "text": r.text, "votes": r.votes, "status": r.status})

    def row(item, pos):
        key = item.id if item is not None else None
        emojis = emoji_rows(counts.get(key, {}))
        title = (plain(item.text("question", lang)) if item is not None and item.type not in CONTENT else "")[:120]
        return {"id": key, "pos": pos, "image": _slide_image(survey, item) if item is not None else None, "named": bool(title),
                "title": title or (tr(lang, "Folie {n}", "Slide {n}", n=pos) if item is not None else tr(lang, "Allgemein", "General")),
                "emojis": emojis, "unclear": sum(n for _, n in emojis), "questions": asked.get(key, [])}

    out = [row(None, 0)] + [row(item, n) for n, item in enumerate(items, start=1)]
    out = [r for r in out if r["unclear"] or r["questions"]]
    if by == "unclear":
        out.sort(key=lambda r: (-r["unclear"], r["pos"]))
    return out


def reactions(survey):
    """[(id, emoji)] of the last REACTION_SHOWN seconds, for the screen (it shows each id once)."""
    from datetime import timedelta

    from app.models import LiveReaction

    since = utcnow() - timedelta(seconds=REACTION_SHOWN)
    rows = db.session.execute(db.select(LiveReaction.id, LiveReaction.emoji).filter(
        LiveReaction.survey_id == survey.id, LiveReaction.created_at >= since).order_by(LiveReaction.id).limit(60))
    return [[i, e] for i, e in rows]
