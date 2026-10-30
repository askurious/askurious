"""Interviews kodieren: coded passages when their transcript is corrected (Ralf 2026-10-30).

1. Saving a changed transcript (app/interview_transcribe.py, edit) first looks which coded passages of all analyses
   of the guide could no longer be placed in the new text (`lost_after`). While the page saves by itself, it then asks
   ("N kodierte Stellen lassen sich danach nicht mehr zuordnen", "Trotzdem speichern" / "Abbrechen",
   app/autosave.py ask); a form sent without script saves as before and says so afterwards. Quotes and names are
   shown only of passages the person may see in that analysis (blind double coding included), else the number.
2. Finding a passage again (`near`, used by transcript_coding_view.place): after the exact wording and the same words
   with other spaces, a passage of about the same length (±20 % of the words) close to the old place, from one of
   its first two words to one of its last two, that reads at least SIMILAR alike letter by letter (difflib; passages of
   at least MIN_WORDS words only, a few words would match too much).
   When the transcript is saved, every passage found is stored at its new place with the wording found
   (`refit`); one whose wording changed carries "adjusted" (the time), shown as "angepasst nach Korrektur".
3. "Neu zuordnen" (transcript_coding_view.reassign): a passage that can no longer be placed gets a new place,
   keeping its id, category, author, memos and its use as anchor or further example."""
import difflib
import re

from app.extensions import db
from app.models import CodingCategory, utcnow

SIMILAR = 0.85
MIN_WORDS = 3
WINDOW = 3000  # characters around the old place that are searched
MAX_PAIRS = 60  # candidates compared at most
_WORD = re.compile(r"\S+")


def _plain(word):
    return word.strip(".,;:!?…\"'„“”‚‘’()[]-–—").lower()


def near(text, quote, old):
    """(start, end) of a passage close to `old` that reads like `quote`, or None. A corrected passage nearly always
    keeps a word at its start and one at its end: candidates run from where the first or second word of the quote
    stands to where its last or second-to-last word stands, with about the same number of words (±20 %). The wording
    must then be at least SIMILAR alike, letter by letter up to 40 words (so that one corrected word in a short passage
    still counts), word by word beyond.
    Only a few candidates are compared, so this stays quick on long transcripts (it runs for every passage not found)."""
    words = quote.split()
    n = len(words)
    if n < MIN_WORDS or n > 400:
        return None
    lo, hi = max(0, old - WINDOW), min(len(text), old + len(quote) + WINDOW)
    tokens = [(m.start(), m.end(), _plain(m.group())) for m in _WORD.finditer(text, lo, hi)]
    if not tokens:
        return None
    first, second, last, before_last = (_plain(w) for w in (words[0], words[1], words[-1], words[-2]))
    starts = set()
    for i, t in enumerate(tokens):
        if t[2] == first:
            starts.add(i)
        if t[2] == second:
            starts.update((i, i - 1))  # the first word changed, or gone
    ends = set()
    for j, t in enumerate(tokens):
        if t[2] == last:
            ends.add(j)
        if t[2] == before_last:
            ends.update((j, j + 1))  # the last word changed, or gone
    shortest, longest = max(1, int(n * 0.8)), int(n * 1.2 + 0.999)
    pairs = [(i, j) for i in starts if 0 <= i < len(tokens) for j in ends
             if 0 <= j < len(tokens) and shortest <= j - i + 1 <= longest]
    if len(pairs) > MAX_PAIRS:  # a quote of very common words: the candidates nearest the old place
        pairs = sorted(pairs, key=lambda p: abs(tokens[p[0]][0] - old))[:MAX_PAIRS]
    letters = n <= 40  # a long passage word by word: one corrected word in a hundred still reads 0.99 alike
    matcher = difflib.SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(" ".join(words) if letters else words)  # the fixed side: difflib keeps what it learnt about it
    best = None
    for i, j in pairs:
        start, stop = tokens[i][0], tokens[j][1]
        matcher.set_seq1(" ".join(text[start:stop].split()) if letters else text[start:stop].split())
        if matcher.real_quick_ratio() < SIMILAR or matcher.quick_ratio() < SIMILAR:
            continue
        ratio = matcher.ratio()
        if ratio < SIMILAR:
            continue
        score = (round(ratio, 3), -abs(start - old))
        if best is None or score > best[0]:
            best = (score, (start, stop))
    return best[1] if best else None


def _projects(survey):
    from app.transcript_coding import projects

    return projects(survey)


def lost_after(survey, row, new_text):
    """The coded passages of all analyses that stand in the transcript now and could not be placed in `new_text`:
    (number, [{"analysis", "category", "quote"}] of those the person of this request may see)."""
    from app import transcript_coding_double
    from app.transcript_coding import actor, interviews, resolver
    from app.transcript_coding_view import codings, place, transcript

    old_text, count, shown = transcript(row), 0, []
    for project in _projects(survey):
        items = codings(row, project.id)
        gone = [c for c in items if place(old_text, c) is not None and place(new_text, c) is None]
        if not gone:
            continue
        count += len(gone)
        who = actor(survey, project)
        if who is None or row.id not in {r.id for r in interviews(project, who)}:
            continue
        seen = {c.get("id") for c in transcript_coding_double.visible(project, who, row, items)}
        resolve = resolver(project)
        names = {c.id: c.name for c in db.session.scalars(db.select(CodingCategory).filter_by(project_id=project.id))}
        for c in gone:
            if c.get("id") in seen:
                quote = c.get("quote") or ""
                shown.append({"analysis": project.name, "category": names.get(resolve(c.get("cat")), ""),
                              "quote": quote[:120] + ("…" if len(quote) > 120 else "")})
    return count, shown


def refit(survey, row, new_text):
    """Before `new_text` is stored: every coding found in it moves to its place there, with the wording found; anchor
    and further examples that quote it follow. Codings not found stay as they are (they show as "nicht mehr
    zuzuordnen"). Returns the new list of codings; the caller stores it with the transcript."""
    from app.transcript_coding_view import place

    meta = row.interview or {}
    out, changed = [], {}
    for c in meta.get("codings") or []:
        if not isinstance(c, dict):
            out.append(c)
            continue
        where = place(new_text, c)
        if where is None:
            out.append(c)
            continue
        quote = new_text[where[0]:where[1]]
        moved = {**c, "start": where[0], "end": where[1], "quote": quote}
        if quote != c.get("quote"):
            moved["adjusted"] = utcnow().isoformat(timespec="seconds")
            changed[c.get("id")] = quote
        out.append(moved)
    if changed:
        _follow(survey, row, changed)
    return out


def _follow(survey, row, changed):
    """The anchor examples and further examples that quote a coding whose wording changed."""
    from app.transcript_coding_view import QUOTE_MAX

    ids = [p.id for p in _projects(survey)]
    if not ids:
        return
    for cat in db.session.scalars(db.select(CodingCategory).where(CodingCategory.project_id.in_(ids))):
        anchor = cat.anchor or {}
        if anchor.get("interview") == row.id and anchor.get("coding") in changed:
            cat.anchor = {**anchor, "quote": changed[anchor["coding"]][:QUOTE_MAX]}
            cat.updated_at = utcnow()
        examples = cat.examples or []
        if any(isinstance(e, dict) and e.get("interview") == row.id and e.get("coding") in changed for e in examples):
            cat.examples = [{**e, "quote": changed[e["coding"]][:QUOTE_MAX]}
                            if isinstance(e, dict) and e.get("interview") == row.id and e.get("coding") in changed else e
                            for e in examples]
            cat.updated_at = utcnow()


def ask_text(count, shown):
    """The question before saving: (title, note) for the app dialog."""
    from app.lang import de_en

    title = de_en("{n} kodierte Stellen lassen sich danach nicht mehr zuordnen", "{n} coded passages can no longer be placed afterwards",
                  n=count) if count != 1 else de_en("1 kodierte Stelle lässt sich danach nicht mehr zuordnen",
                                                     "1 coded passage can no longer be placed afterwards")
    lines = [f"{s['analysis']} · {s['category']}: „{s['quote']}“" if s["category"] else f"{s['analysis']}: „{s['quote']}“" for s in shown[:8]]
    if len(shown) > 8:
        lines.append(de_en("… und {n} weitere", "… and {n} more", n=len(shown) - 8))
    if count > len(shown):
        lines.append(de_en("{n} davon in Auswertungen, die Sie nicht sehen.", "{n} of them in analyses you do not see.",
                           n=count - len(shown)))
    lines.append(de_en("Sie erscheinen in der Kodieransicht unter „Nicht mehr zuzuordnen“ und lassen sich dort neu zuordnen.",
                       "They appear in the coding view under “Can no longer be placed” and can be placed anew there."))
    return title, "\n".join(lines)
