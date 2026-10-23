"""Search over the transcripts of a guide's interviews ("Interviews erheben", evaluation 2; Ralf 2026-10-04).

Keyword in context: every place a word or phrase stands, with the words around it, the interview and the time mark of
its paragraph (#hh:mm:ss-0#, Dresing & Pehl 2018), so the place can be found in the recording and in the transcript.
A "*" at the end of a word also finds its continuations ("pfleg*": pflegen, Pflegedienst). Word frequencies per
interview and over all of them, without function words, time marks and speaker labels. Both can be limited to the
interviewed person's paragraphs ("B:"), as the transcription rules mark them.

Lexical counts describe the material; they do not replace coding (Kuckartz & Rädiker, 2022)."""
import re
from collections import Counter

from flask import Blueprint, render_template, request
from flask_login import login_required
from markupsafe import Markup, escape

from app import interview_data
from app.lang import de_en, english

bp = Blueprint("interview_search", __name__, url_prefix="/guide")

STAMP = re.compile(r"#\d{2}:\d{2}:\d{2}-\d#")
SPEAKER = re.compile(r"^\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöü0-9]{0,11})\s*:\s*")
WORD = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß'-]*")
CONTEXT = 70
MAX_HITS = 500
TOP = 40
SPEAKERS = {"": "alle Absätze", "B": "nur die befragte Person (B:)", "I": "nur die Interviewenden (I:)"}
SPEAKERS_EN = {"": "all paragraphs", "B": "only the interviewee (B:)", "I": "only the interviewers (I:)"}


def speakers():
    """SPEAKERS in the language of the page."""
    return SPEAKERS_EN if english() else SPEAKERS
# function words of German and English that say little about the content
STOP = set("""
aber alle allem allen aller alles als also am an ander andere anderem anderen anderer anderes auch auf aus bei bin bis
bist da dabei damit dann das dass dein deine dem den denn der des dessen deshalb die dies diese diesem diesen dieser
dieses doch dort du durch ein eine einem einen einer eines einfach einmal er es etwas euch euer eure für gar gegen
gewesen ging gibt hab habe haben hat hatte hatten hier hin hinter ich ihm ihn ihnen ihr ihre im immer in ins ist ja
jede jedem jeden jeder jetzt kann kein keine können könnte machen man mich mir mit muss musste nach nicht nichts noch
nun nur ob oder ohne schon sehr sein seine sich sie sind so solche soll sollte sondern sonst über um und uns unser
unter viel vom von vor war waren warum was weil weiter welche wenn wer werde werden wie wieder will wir wird wo wurde
wurden zu zum zur zwar zwischen ähm äh hm mhm genau okay ok naja halt eben mal ne
a about all also an and any are as at be been but by can could did do does for from had has have he her his how i if
in into is it its just me my no not of on or our she so some than that the their them then there they this to too
up was we were what when which who will with would you your yeah um uh
""".split())


def transcripts(survey):
    """[(row, label, text)] of the interviews with a transcript (no Probeinterviews)."""
    out = []
    for r in interview_data.interviews(survey):
        text = ((r.interview or {}).get("transcript") or {}).get("text")
        if text and r.source == "real":
            out.append((r, (r.interview or {}).get("label", str(r.id)), text))
    return out


def paragraphs(text, speaker=""):
    """[(text, time mark or "")] of a transcript, limited to one speaker's paragraphs if asked."""
    out = []
    for para in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        para = para.strip()
        if not para:
            continue
        who = SPEAKER.match(para)
        if speaker and not (who and who.group(1).upper().startswith(speaker)):
            continue
        stamps = STAMP.findall(para)
        out.append((para, stamps[-1] if stamps else ""))
    return out


def pattern(query):
    """A regular expression for the query: words in order, "*" at the end of a word for any continuation."""
    words = [w for w in query.split() if w.strip("*")][:8]
    if not words:
        return None
    parts = [re.escape(w.rstrip("*")) + (r"\w*" if w.endswith("*") else r"\b") for w in words]
    return re.compile(r"(?<!\w)" + r"\s+".join(parts), re.IGNORECASE)


def search(survey, query, speaker="", found=None):
    """{"hits": [{label, row, before, match, after, stamp}], "counts": {label: n}} (at most MAX_HITS). found: the
    transcripts (transcripts()), if read already."""
    rx = pattern(query)
    hits, counts = [], Counter()
    if rx is None:
        return {"hits": hits, "counts": counts}
    for r, label, text in (transcripts(survey) if found is None else found):
        for para, stamp in paragraphs(text, speaker):
            clean = STAMP.sub("", para).strip()
            for m in rx.finditer(clean):
                counts[label] += 1
                if len(hits) < MAX_HITS:
                    before = clean[max(0, m.start() - CONTEXT):m.start()]
                    after = clean[m.end():m.end() + CONTEXT]
                    hits.append({"label": label, "row": r.id, "before": ("…" if m.start() > CONTEXT else "") + before,
                                 "match": m.group(0), "after": after + ("…" if m.end() + CONTEXT < len(clean) else ""), "stamp": stamp})
    return {"hits": hits, "counts": counts}


def words(text, speaker=""):
    counter = Counter()
    for para, _ in paragraphs(text, speaker):
        para = SPEAKER.sub("", STAMP.sub("", para))
        for w in WORD.findall(para):
            w = w.lower().strip("'-")
            if len(w) > 2 and w not in STOP:
                counter[w] += 1
    return counter


# the word counts per transcript and speaker filter, kept per process while the transcript stays the same: they do not
# depend on the search, and counting 40 long interviews took most of the time of every search (code review
# 2026-10-07, I10). Kept per survey: a deletion (erasure, retention, the survey deleted) drops them with forget().
_counted = {}
_COUNTED_MAX = 400


def forget(uid):
    """Drop the word counts of the interviews of the survey with this uid (in this process): what was said in a
    deleted interview does not stay in memory (code review 2026-10-08, R5)."""
    for key in [k for k in _counted if k[0] == uid]:
        _counted.pop(key, None)


def _words_kept(uid, row_id, text, speaker):
    import hashlib

    key = (uid, row_id, speaker, hashlib.sha256(text.encode("utf-8")).hexdigest())
    found = _counted.get(key)
    if found is None:
        if len(_counted) >= _COUNTED_MAX:
            _counted.clear()
        found = _counted[key] = words(text, speaker)
    return found


def frequencies(survey, speaker="", found=None):
    """The most frequent words over all interviews, and per interview: ([(word, n)], {label: [(word, n)]}).
    found: the transcripts (transcripts()), if read already."""
    total, each = Counter(), {}
    for r, label, text in (transcripts(survey) if found is None else found):
        c = _words_kept(survey.uid, r.id, text, speaker)
        total.update(c)
        each[label] = c.most_common(15)
    return total.most_common(TOP), each


def highlight(hit):
    return Markup(f"{escape(hit['before'])}<mark>{escape(hit['match'])}</mark>{escape(hit['after'])}")


@bp.route("/<code>/interviews/search")
@login_required
def page(code):
    from app.interview_results import DRESING_PEHL
    from app.qdc import KUCKARTZ

    survey = interview_data._survey(code, need="editor")
    query = " ".join(request.args.get("q", "").split())[:120]
    speaker = request.args.get("sprecher", "") if request.args.get("sprecher", "") in SPEAKERS else ""
    texts = transcripts(survey)  # read once for the search, the word counts and their number (I10)
    found = search(survey, query, speaker, texts) if query else None
    top, each = frequencies(survey, speaker, texts)
    return render_template("interview/search.html", survey=survey, query=query, speaker=speaker, speakers=speakers(),
                           found=found, top=top, each=each, n=len(texts), highlight=highlight, max_hits=MAX_HITS,
                           sources=[(de_en("Zeitmarken und Sprecherkürzel", "Time marks and speaker labels"), [DRESING_PEHL]),
                                    (de_en("Zählen ersetzt nicht das Kodieren", "Counting does not replace coding"), [KUCKARTZ])])
