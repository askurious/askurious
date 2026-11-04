"""How the versions of a stimulus differ, worked out from the versions alone (concept "Befragungsmaterial mit sichtbarer
Randomisierung"; Philipp 2026-11-04: nobody writes a base vignette, so the tool has to find one).

No version is the base. `align` takes the texts of all conditions and returns what they share and where they part:

    [shared words] [fork: A and B | C] [shared words] ...

The shared text is a common subsequence of all versions, in words. A shared run shorter than MIN_WORDS between two forks
is not kept: a lone "von" or "die" says nothing and only cuts the text to pieces. Inside a fork, versions that resemble
each other (gain and loss against the control) are aligned among themselves, so "mit Unterstützung" and "trotz
Unterstützung" part only at the word that differs, although the control has no such sentence.

Formatting counts: the same words set in bold are a different stimulus, so a token is (word, style). The module knows
nothing about PDFs; app/study_conditions.py draws the result."""
import difflib
import html
import re
from dataclasses import dataclass

from app.items.base import inline_rich

MIN_WORDS = 3  # a shared run shorter than this between two differences does not count as shared
MAX_TOKENS = 12_000  # longer versions are compared as a whole: the alignment takes quadratic time
SIMILAR = 0.5  # share of words from which versions inside a fork are aligned among themselves

STYLES = {"strong": "b", "b": "b", "em": "i", "i": "i", "u": "u", "sub": "sub", "sup": "sup", "code": "code",
          "small": "small"}
_PIECE = re.compile(r"<(/?)([a-z]+)>|([^<]+)")
# a line break, other space, one Chinese character or Hangul syllable (no spaces between words there), a word, a mark
_TOKEN = re.compile(r"\n+|[^\S\n]+|[⺀-鿿가-힯]|\w+|[^\w\s]")


@dataclass(frozen=True)
class Seg:
    """Part of the aligned text. Either `tokens` that all of `levels` have, or `branches`: where they part, as
    [(levels of the branch, [Seg] of the branch)]."""
    levels: tuple
    tokens: tuple = ()
    branches: tuple = ()


def parse(text):
    """[(word, style)] of a text of the questionnaire with its formatting marks (app/items/base.py)."""
    rich = str(inline_rich(text or "")).replace("<br>", "\n")
    out, active = [], []
    for match in _PIECE.finditer(rich):
        if match.group(3) is None:
            name = STYLES.get(match.group(2))
            if name and match.group(1):
                if name in active:
                    active.remove(name)
            elif name:
                active.append(name)
            continue
        style = tuple(sorted(set(active)))
        out += [(word, style) for word in _TOKEN.findall(html.unescape(match.group(3)))]
    return out


def _is_word(token):
    return any(ch.isalnum() for ch in token[0])


def _words(tokens):
    return sum(1 for token in tokens if _is_word(token))


def _pairs(a, b):
    """[(index in a, index in b)] of what the two have in common, in order."""
    return [(m.a + i, m.b + i) for m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()
            for i in range(m.size)]


def _common(seqs):
    """(tokens that all sequences hold in the same order, [[where each of them lies] per sequence])."""
    kept = list(seqs[0])
    changed = True
    while changed:  # taking one sequence in can end a match with another: until all of them agree
        changed = False
        for seq in seqs:
            hits = _pairs(kept, seq)
            if len(hits) < len(kept):
                kept, changed = [kept[a] for a, _ in hits], True
    return kept, [[b for _, b in _pairs(kept, seq)] for seq in seqs]


def _runs(kept, places, seqs):
    """[(start per sequence, end per sequence, tokens)] of the shared runs worth keeping: unbroken in every sequence,
    and MIN_WORDS long unless it stands at the very start or the very end of the text."""
    runs, start = [], 0
    for i in range(1, len(kept) + 1):
        if i == len(kept) or any(p[i] != p[i - 1] + 1 for p in places):
            runs.append((start, i))
            start = i
    out = []
    for a, b in runs if kept else []:
        begin, end = [p[a] for p in places], [p[b - 1] + 1 for p in places]
        at_edge = not any(begin) or all(e == len(seq) for e, seq in zip(end, seqs))
        if _words(kept[a:b]) >= MIN_WORDS or (at_edge and _words(kept[a:b])):
            out.append((begin, end, tuple(kept[a:b])))
    return out


def _similar(a, b):
    a, b = [t for t in a if _is_word(t)], [t for t in b if _is_word(t)]
    return bool(a and b) and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= SIMILAR


def _clusters(groups):
    """The groups of a fork, those that resemble each other together, in the order of their first level."""
    clusters = []
    for group in groups:
        near = [c for c in clusters if any(_similar(group[1], other[1]) for other in c)]
        merged = [group] + [g for c in near for g in c]
        clusters = [c for c in clusters if c not in near] + [merged]
    return sorted((sorted(c, key=lambda g: g[0]) for c in clusters), key=lambda c: c[0][0])


def _fork(groups):
    """[Seg] of a stretch where the versions part: `groups` is [(levels, tokens)]."""
    distinct = {}
    for levels, tokens in groups:
        distinct.setdefault(tuple(tokens), []).extend(levels)
    groups = [(tuple(sorted(levels)), list(tokens)) for tokens, levels in distinct.items()]
    levels = tuple(sorted(lv for group in groups for lv in group[0]))
    if len(groups) == 1:
        return [Seg(levels, tuple(groups[0][1]))] if groups[0][1] else []
    clusters = _clusters(groups) if len(groups) > 2 else [[g] for g in sorted(groups, key=lambda g: g[0])]
    if len(clusters) == 1:  # they all resemble each other, and _segments has just found nothing more to share
        clusters = [[g] for g in sorted(groups, key=lambda g: g[0])]
    branches = []
    for cluster in clusters:
        lv = tuple(sorted(x for group in cluster for x in group[0]))
        inner = _segments(cluster) if len(cluster) > 1 else ([Seg(lv, tuple(cluster[0][1]))] if cluster[0][1] else [])
        branches.append((lv, tuple(inner)))
    return [Seg(levels, branches=tuple(branches))]


def _segments(groups):
    """[Seg] of [(levels, tokens)], one group per different version."""
    seqs = [g[1] for g in groups]
    levels = tuple(sorted(lv for g in groups for lv in g[0]))
    if sum(len(s) for s in seqs) > MAX_TOKENS:
        return _fork(groups) if len({tuple(s) for s in seqs}) > 1 else [Seg(levels, tuple(seqs[0]))]
    kept, places = _common(seqs)
    out, pos = [], [0] * len(seqs)
    for begin, end, tokens in _runs(kept, places, seqs):
        if any(b > p for b, p in zip(begin, pos)):
            out += _gap(groups, pos, begin)
        out.append(Seg(levels, tokens))
        pos = end
    if any(p < len(s) for p, s in zip(pos, seqs)):
        out += _gap(groups, pos, [len(s) for s in seqs])
    return out


def _gap(groups, begin, end):
    return _fork([(g[0], g[1][b:e]) for g, b, e in zip(groups, begin, end)])


def align(texts):
    """[Seg] of the versions, one text per level. Versions that are the same count as one."""
    return _fork_top([parse(text) for text in texts])


def _fork_top(seqs):
    distinct = {}
    for level, tokens in enumerate(seqs):
        distinct.setdefault(tuple(tokens), []).append(level)
    groups = [(tuple(levels), list(tokens)) for tokens, levels in distinct.items()]
    if len(groups) == 1:
        return [Seg(groups[0][0], tuple(groups[0][1]))] if groups[0][1] else []
    return _segments(groups)


def flatten(segs, level):
    """[(tokens, how many levels share them)] of one level in its reading order."""
    out = []
    for seg in segs:
        if seg.branches:
            for levels, inner in seg.branches:
                if level in levels:
                    out += flatten(inner, level)
        elif level in seg.levels:
            out.append((seg.tokens, len(seg.levels)))
    return out


def _ends_sentence(tokens, i):
    """Whether a sentence or paragraph ends with tokens[i]: a line break, or .!? (a closing quote may follow) before a
    space. "90 %" or "z.B." inside a sentence have no such space; an abbreviation with one only makes a row shorter."""
    word = tokens[i][0]
    if word.startswith("\n"):
        return True
    if word not in (".", "!", "?", "…") or i + 1 >= len(tokens):
        return word in (".", "!", "?", "…")
    following = i + 1
    if tokens[following][0] in ('"', "”", "“", "'", ")"):
        following += 1
    return following >= len(tokens) or not tokens[following][0].strip()


def _sentences(tokens):
    """[(tokens of a sentence with the space after it, whether it is complete)]."""
    out, start, i = [], 0, 0
    tokens = list(tokens)
    while i < len(tokens):
        if _ends_sentence(tokens, i):
            i += 1
            while i < len(tokens) and not tokens[i][0].strip() or (i < len(tokens) and tokens[i][0] in ('"', "”", "“", "'", ")")):
                i += 1
            out.append((tokens[start:i], True))
            start = i
        else:
            i += 1
    if start < len(tokens):
        out.append((tokens[start:], False))
    return out


def _closed(pieces):
    """Whether every level's text so far ends a sentence."""
    for level in pieces:
        words = [t for tokens, _ in level for t in tokens if t[0].strip() or t[0].startswith("\n")]
        if words and not (_ends_sentence(words, len(words) - 1) or words[-1][0] in ('"', "”", ")") and len(words) > 1
                          and _ends_sentence(words[:-1], len(words) - 2)):
            return False
    return True


def rows(segs, n):
    """The aligned text as rows to read: ("same", tokens) for whole sentences all n levels share, ("differ", [pieces per
    level]) for sentences that differ, each in full in every level, pieces as `flatten` gives them.

    A fork cuts a text at the word that differs, which is right for marking and tiring as a layout: it leaves half a
    headline in a row of its own, and a sentence with four differences in nine rows (Philipp 2026-11-04). A shared
    stretch stands on its own only as whole sentences; a part of a sentence stays with what differs in it."""
    out, open_row = [], None

    def close():
        nonlocal open_row
        if open_row is not None:
            out.append(("differ", open_row))
            open_row = None

    def same(tokens):
        if out and out[-1][0] == "same":
            out[-1] = ("same", out[-1][1] + tuple(tokens))
        else:
            out.append(("same", tuple(tokens)))

    for at, seg in enumerate(segs):
        if seg.branches:
            open_row = open_row or [[] for _ in range(n)]
            for level in range(n):
                open_row[level] += flatten([seg], level)
            continue
        if open_row is not None and _closed(open_row):
            close()
        last = at == len(segs) - 1
        for tokens, complete in _sentences(seg.tokens):
            if open_row is not None:
                for level in range(n):
                    open_row[level].append((tuple(tokens), n))
                if complete or last:
                    close()
            elif complete or last:
                same(tokens)
            else:
                open_row = [[(tuple(tokens), n)] for _ in range(n)]
    close()
    return out


def trim(pieces):
    """Pieces without the space at their start and end."""
    pieces = [(list(tokens), shared) for tokens, shared in pieces if tokens]
    while pieces and not pieces[0][0][0][0].strip():
        pieces[0][0].pop(0)
        if not pieces[0][0]:
            pieces.pop(0)
    while pieces and not pieces[-1][0][-1][0].strip():
        pieces[-1][0].pop()
        if not pieces[-1][0]:
            pieces.pop()
    return pieces


def counts(segs, n):
    """(words all n levels share, [words of each level that not all share])."""
    shared = sum(_words(seg.tokens) for seg in segs if not seg.branches and len(seg.levels) == n)
    return shared, [sum(_words(tokens) for tokens, k in flatten(segs, level) if k < n) for level in range(n)]
