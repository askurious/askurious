"""Video / audio stimulus with a playback log (experimental until 2026-10-16, now for everyone; concept
video-audio-stimulus-konzept.md, E1-E15 as recommended, Ralf 2026-10-08).

An uploaded video or audio file plays on the page itself, with a control bar of its own (participant.js
[data-media]) that keeps the rules of the element: how freely one may seek ("seek": free, back = back but not
beyond what was seen, none = only play and pause, once = play once without pausing) and when the page may go on
("advance": any = always, with a soft hint once while the stimulus has not ended; end = only after the end;
share = after `share` per cent of it was seen). The file lies with the images of the survey (an Asset with key ""
static, or one per value of a loop column, config "by": image.asset_key), its kind, type, size and length in
config["files"][key]. Upload in
pieces of CHUNK_BYTES (editor.item_media_upload), so the limit of a page (10 MB) and nginx stay as they are.

The page sends the log of the player as JSON in the field i<id>: [[ms since the page was shown, event, a, b, c]]
with the events of EVENTS. The server checks it (kinds, numbers, order, positions within the length) and works out
the figures of the export from it; nobody can send a figure, only events. A segment ("seg", from, to, hidden) is
a stretch that played without a jump; the script closes one at every pause, jump, end, change of the window's
visibility and when the page is sent. Without JavaScript the browser's own player shows and no log comes: the
figures stay empty (None), never 0.

  answer = {"log": [[ms, event, a, b, c], ...], "dur": seconds of the file, "key": asset key, "cut": events left out,
            "visits": [[id of one showing of the page, its events, its cut], ...] (since 2026-10-23; DS-03)}
"""
import json
import math
import re

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import AnswerError, ItemType, in_lang, r_int, r_one, r_text
from app.lang import Both, de_en
from app.ui_i18n import tr

KINDS = ("video", "audio")
# what an upload may be: its first bytes decide, not the name or what the browser says (sniff)
MIMES = {"video/mp4": ("video", "mp4"), "video/webm": ("video", "webm"), "audio/mp4": ("audio", "m4a"),
         "audio/mpeg": ("audio", "mp3"), "audio/ogg": ("audio", "ogg"), "audio/wav": ("audio", "wav"),
         "audio/webm": ("audio", "weba")}
EXTENSIONS = {ext for _, ext in MIMES.values()}
SEEK = ("back", "free", "none", "once")
ADVANCE = ("any", "end", "share")
DEFAULT_SHARE = 90
CHUNK_BYTES = 5 * 1024 * 1024
VISIT = re.compile(r"[A-Za-z0-9]{8,32}")  # the id of one showing of the page (participant.js), DS-03
MAX_EVENTS = 500  # per element and page; what goes beyond is counted ("cut")
MAX_SECONDS = 4 * 3600  # length of a file the editor accepts (longer: a film, not a stimulus)
EVENTS = {  # event: number of values after it
    "play": 1, "pause": 1, "seek": 2, "end": 1, "seg": 3, "rate": 1, "mute": 1, "stall": 1, "full": 1,
    "hide": 0, "show": 0, "error": 1,
}
COMPLETE = 0.99  # share seen that counts as "to the end" even without the event (the last frames)
MAX_TRANSCRIPT = 20000
MAX_CAPTIONS = 200000  # characters of WebVTT per language


def sniff(head):
    """The type of a media file from its first bytes (at least 16), or None."""
    if len(head) < 12:
        return None
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        return "audio/mp4" if brand in (b"M4A ", b"M4B ", b"F4A ") else "video/mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"  # Matroska/WebM; an audio-only WebM is told apart by the editor's player (kind)
    if head[:4] == b"OggS":
        return "audio/ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[:3] == b"ID3" or (head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    return None


def _number(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError
    return round(float(v), 3)


def read_log(raw, dur):
    """The checked events of a page ([ms, event, values...]) and how many were left out, or raise ValueError for a
    log that no player writes (unknown events, positions outside the file, time running backwards)."""
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, list):
        raise ValueError
    end = (dur or MAX_SECONDS) + 1  # the last frame may report a little more than the length
    out, last = [], 0
    for row in data[:MAX_EVENTS]:
        if not isinstance(row, list) or len(row) < 2 or row[1] not in EVENTS or len(row) != 2 + EVENTS[row[1]]:
            raise ValueError
        ms = row[0]
        if isinstance(ms, bool) or not isinstance(ms, int) or ms < last or ms > 24 * 3600 * 1000:
            raise ValueError
        last = ms
        kind = row[1]
        if kind in ("play", "pause", "end", "stall"):
            values = [_number(row[2], 0, end)]
        elif kind == "seek":
            values = [_number(row[2], 0, end), _number(row[3], 0, end)]
        elif kind == "seg":
            a, b = _number(row[2], 0, end), _number(row[3], 0, end)
            if b < a:
                raise ValueError
            values = [a, b, 1 if row[4] else 0]
        elif kind == "rate":
            values = [_number(row[2], 0.0625, 16)]
        elif kind in ("mute", "full"):
            values = [1 if row[2] else 0]
        elif kind == "error":
            values = [int(_number(row[2], 0, 99))]
        else:
            values = []
        out.append([ms, kind, *values])
    return out, max(len(data) - MAX_EVENTS, 0)


def figures(answer):
    """{started, seen, complete, seconds, plays, pauses, seeks, hidden, muted, stalls} of a stored answer, or None
    where there is no log (no JavaScript, not shown)."""
    if not isinstance(answer, dict) or not isinstance(answer.get("log"), list):
        return None
    log, dur = answer["log"], answer.get("dur") or 0
    count = {k: 0 for k in EVENTS}
    segs, muted, hidden = [], 0, 0.0
    for row in log:
        count[row[1]] += 1
        if row[1] == "seg":
            segs.append((row[2], row[3]))
            if row[4]:
                hidden += row[3] - row[2]
        elif row[1] == "mute" and row[2]:
            muted = 1
    played = sum(b - a for a, b in segs)
    union, reach = 0.0, None
    for a, b in sorted(segs):
        if reach is None or a > reach:
            union += b - a
            reach = b
        elif b > reach:
            union += b - reach
            reach = b
    seen = min(union / dur, 1.0) if dur else None
    return {"started": int(count["play"] > 0), "seen": round(seen, 3) if seen is not None else None,
            "complete": int(count["end"] > 0 or (seen or 0) >= COMPLETE), "seconds": round(played, 1),
            "plays": count["play"], "pauses": count["pause"], "seeks": count["seek"], "hidden": round(hidden, 1),
            "muted": muted, "stalls": count["stall"]}


FIGURES = [  # column suffix, German label, English label
    ("started", "abgespielt (1 = ja)", "played (1 = yes)"),
    ("seen", "Anteil gesehen bzw. gehört (0–1)", "share seen or heard (0–1)"),
    ("complete", "bis zum Ende (1 = ja)", "to the end (1 = yes)"),
    ("seconds", "Abspielzeit in Sekunden", "playing time in seconds"),
    ("plays", "Anzahl Starts", "number of starts"),
    ("pauses", "Anzahl Pausen", "number of pauses"),
    ("seeks", "Anzahl Sprünge", "number of jumps"),
    ("hidden", "Sekunden, in denen es im Hintergrund lief", "seconds it played in the background"),
    ("muted", "stumm oder leise gestellt (1 = ja)", "muted (1 = yes)"),
    ("stalls", "wie oft das Laden stockte", "how often loading stalled"),
]


@register
class Stimulus(ItemType):
    key = "media"
    label = Both("Video / Audio", "Video / audio")
    short = Both("Video oder Ton abspielen", "Play video or audio")
    description = Both("Video oder Tonaufnahme abspielen, mit Regeln zum Spulen und Weitergehen und "
                       "einem Abspielprotokoll",
                       "Play a video or an audio recording, with rules for seeking and going on and a "
                       "playback log")
    order = 61
    fact_sheet = FactSheet(
        "spatial",
        keywords=["Video", "Audio", "Film", "Ton", "Sound", "Stimulus", "Player"],
        can=[("Hochgeladenes Video oder Audio mit eigener Bedienleiste, Untertiteln und Textfassung.",
              "Uploaded video or audio with its own control bar, captions and a transcript."),
             ("Spulen frei, nur zurück, gar nicht oder nur einmal abspielen; weiter erst nach dem Ende oder einem Anteil.",
              "Seeking free, back only, not at all or play once; go on only after the end or a share."),
             ("Protokoll: wie viel gesehen, bis zum Ende, Abspielzeit, Pausen, Sprünge, im Hintergrund, stumm.",
              "Log: how much was seen, to the end, playing time, pauses, jumps, in the background, muted.")],
        good_for=[("Medienstimuli in Experimenten: Werbespot, Nachrichtenbeitrag, Stimme",
                   "Media stimuli in experiments: advert, news item, voice"),
                  ("Prüfen, ob die Manipulation angekommen ist", "Checking whether the manipulation got through")],
        not_for=[("Lange Filme (eine Datei hat höchstens 50 MB)", "Long films (a file has at most 50 MB)"),
                 ("Ton aufnehmen (dann Sprachnachricht)", "Recording sound (then voice message)")],
        designs=["framing", "ab", "within"],
        examples=[("Framing-Experiment: derselbe Beitrag mit zwei Sprecherstimmen",
                   "Framing experiment: the same item with two voices")],
    )
    result_kind = "auto"  # the columns stay off the page (result_column); results.py shows results() instead
    has_answer_mode = False  # whether the page may go on is "advance"
    allowed_in_separated = False
    long_export = "media"
    long_export_label = Both("Person × Ereignis des Players: Zeit, Ereignis, Stelle im Medium",
                             "Person × player event: time, event, position in the medium")
    long_export_note = ("- `media_*_long.csv`: the playback log, one row per player event: milliseconds since the page "
                        "was shown, event (play, pause, seek, end, seg = a stretch played without a jump, rate, mute, "
                        "stall, full = full screen, hide/show = window in the background, error) and its values "
                        "(positions in seconds of the medium).")
    import_rules = {"seek": r_one(*SEEK), "advance": r_one(*ADVANCE), "share": r_int(10, 100), "by": r_text(41)}

    chunk_bytes = CHUNK_BYTES

    def max_mb(self):
        from flask import current_app

        return int(current_app.config["MEDIA_MAX_MB"])

    def default_config(self):
        return {"seek": "back", "advance": "any", "share": DEFAULT_SHARE}

    def default_texts(self, lang):
        return {"question": "", "transcript": "", "captions": ""}

    # --- settings ------------------------------------------------------------------------------------------

    def seek(self, item):
        return (item.config or {}).get("seek") if (item.config or {}).get("seek") in SEEK else "back"

    def advance(self, item):
        return (item.config or {}).get("advance") if (item.config or {}).get("advance") in ADVANCE else "any"

    def share(self, item):
        n = (item.config or {}).get("share")
        return n if isinstance(n, int) and 10 <= n <= 100 else DEFAULT_SHARE

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        texts[lang] = {**texts.get(lang, {}), "question": form.get("question", "").strip(),
                       "transcript": form.get("transcript", "").strip()[:MAX_TRANSCRIPT],
                       "captions": clean_vtt(form.get("captions", ""))}
        item.texts = texts
        if structural:
            config = dict(item.config or {})
            config["seek"] = form.get("seek") if form.get("seek") in SEEK else "back"
            config["advance"] = form.get("advance") if form.get("advance") in ADVANCE else "any"
            try:
                config["share"] = min(max(int(form.get("share", "")), 10), 100)
            except ValueError:
                config["share"] = config.get("share", DEFAULT_SHARE)
            if "by" in form:  # a file per value of this loop column, or one for all ("")
                config["by"] = form.get("by") if form.get("by") in self.loop_columns(item) else ""
            # "× Datei entfernen" (X31): the file leaves the element; its upload stays until another one replaces it,
            # so that "Rückgängig" (config only) brings it back
            files = dict(config.get("files") or {})
            for key in list(files):
                if form.get(f"file_remove_{key}"):
                    files.pop(key)
            config["files"] = files
            item.config = config

    # --- the file -------------------------------------------------------------------------------------------

    def info(self, item, key=""):
        """{"kind", "mime", "bytes", "seconds", "name"} of the file for `key`, or None."""
        found = ((item.config or {}).get("files") or {}).get(key)
        return found if isinstance(found, dict) else None

    def asset_for(self, item, variables=None):
        key = self.key_for(item, variables)
        if not self.info(item, key):  # taken off with "Datei entfernen" (the upload stays for "Rückgängig")
            return None
        return next((a for a in item.assets if a.key == key and a.status == "approved"), None)

    def present(self, item):
        """The keys of the files the element has: uploaded, approved and not taken off ("Datei entfernen")."""
        files = (item.config or {}).get("files") or {}
        return {a.key for a in item.assets if a.status == "approved" and isinstance(files.get(a.key), dict)}

    # --- a file per value of a loop column (concept E11b: stimulus sampling) ------------------------------

    def loop_columns(self, item):
        """The names of the columns of the loop the element is in ([] outside a loop)."""
        from app import loops

        if item.block is None or not loops.active(item.block):
            return []
        return [c["name"] for c in loops.config(item.block).get("columns", []) if c.get("name")]

    def by(self, item):
        """The loop column whose value picks the file, or "" (one file for every pass)."""
        name = (item.config or {}).get("by") or ""
        return name if name in self.loop_columns(item) else ""

    def key_for(self, item, variables=None):
        """The asset key of the file for these loop values ("" for one file)."""
        from app.items.image import asset_key

        name = self.by(item)
        return asset_key(variables or {}, {name}) if name and variables else ""

    def required_keys(self, item):
        """[(key, value)] of the files the element needs: one per distinct value of its loop column, else [("", "")]."""
        from app import loops
        from app.items.image import asset_key

        name = self.by(item)
        if not name:
            return [("", "")]
        seen = {}
        for row in loops.all_variables(item.block):
            seen.setdefault(asset_key(row, {name}), str(row.get(name, "")))
        return sorted(seen.items(), key=lambda kv: kv[1])

    def key_label(self, item, key):
        return dict(self.required_keys(item)).get(key) or key

    def kind_of(self, item, key=""):
        files = (item.config or {}).get("files") or {}
        found = self.info(item, key) or next((f for f in files.values() if isinstance(f, dict)), None)
        return found.get("kind") if found and found.get("kind") in KINDS else "video"

    def validate(self, item):
        problems = super().validate(item)
        have = self.present(item)
        missing = [value for key, value in self.required_keys(item) if key not in have]
        if missing and not self.by(item):
            problems.append(de_en("noch keine Datei hochgeladen", "no file uploaded yet"))
        elif missing:
            problems.append(de_en("noch ohne Datei: {values}", "no file yet for: {values}", values=", ".join(missing[:5])
                                  + (" …" if len(missing) > 5 else "")))
        return problems

    def check_warnings(self, item):
        """Accessibility (concept E10, WCAG 1.2): a hint, never a stop."""
        out = list(super().check_warnings(item))
        if not self.present(item):
            return out
        lang = item.survey.default_language
        if self.kind_of(item) == "video" and not item.text("captions", lang):
            out.append(de_en("Video ohne Untertitel: Wer nicht hört, versteht den Ton nicht (BITV, WCAG 1.2.2).",
                             "Video without captions: people who cannot hear miss the sound (WCAG 1.2.2)."))
        if not item.text("transcript", lang):
            out.append(de_en("Keine Textfassung: Sie hilft Menschen, die weder sehen noch hören können, und als Ersatz, "
                             "wenn die Datei nicht abspielt (WCAG 1.2.1, 1.2.3).",
                             "No transcript: it helps people who can neither see nor hear, and stands in when the file "
                             "does not play (WCAG 1.2.1, 1.2.3)."))
        return out

    # --- answers --------------------------------------------------------------------------------------------

    uses_answers = True  # what was played before on this page counts for "advance" (parse_answer)

    def parse_answer(self, item, form, lang="de", meta=None, answers=None):
        raw = form.get(f"i{item.id}")
        if not raw:
            return None  # no script: the browser's player, no log
        # the page names the file it played (a loop value); only a file of the element counts
        key = form.get(f"i{item.id}_key", "")
        key = key if key in ((item.config or {}).get("files") or {}) else ""
        info = self.info(item, key) or {}
        dur = info.get("seconds") or 0
        try:
            log, cut = read_log(raw, dur)
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):
            return None  # not a log of this page's player: no figures rather than wrong ones
        answer = {"log": log, "dur": dur, "key": key}
        if cut:
            answer["cut"] = cut
        visit = form.get(f"i{item.id}_visit", "")
        if isinstance(visit, str) and VISIT.fullmatch(visit):
            # which showing of the page the events come from: the same POST sent again replaces them (DS-03)
            answer["visits"] = [[visit, len(log), cut]]
        rule = self.advance(item)
        if rule != "any" and not any(row[1] == "error" for row in log):  # a file that does not play keeps nobody
            # played to the end before (an error elsewhere on the page, "Zurück"): the stored log of the same file
            # counts too, as merge_answer will join them; nobody has to watch it once more (audit 2026-10-10, N-12)
            kept = (answers or {}).get(item.id, (answers or {}).get(str(item.id)))
            f = figures(self.merge_answer(kept, answer) if isinstance(kept, dict) else answer)
            need = 1.0 if rule == "end" else self.share(item) / 100
            if not f["complete"] and (f["seen"] or 0) < need:
                raise AnswerError(self._not_yet(item, lang, rule))
        return answer

    def _not_yet(self, item, lang, rule):
        video = self.kind_of(item) == "video"
        if rule == "end":
            return (tr(lang, "Bitte sehen Sie das Video bis zum Ende an.", "Please watch the video to the end.") if video
                    else tr(lang, "Bitte hören Sie die Aufnahme bis zum Ende an.", "Please listen to the recording to the end."))
        return (tr(lang, "Bitte sehen Sie mehr von dem Video an.", "Please watch more of the video.") if video
                else tr(lang, "Bitte hören Sie mehr von der Aufnahme an.", "Please listen to more of the recording."))

    def display_value(self, item, form, meta=None):
        return None  # a page shown again starts the log anew; what was played before stays in the answer

    def merge_answer(self, old, new):
        """A page sent again (an error elsewhere on it, "Back" and on) adds its events to the earlier ones. The
        events of one showing of the page (its visit id, "visits": [[id, events, cut], ...]) count once: the same
        POST sent again (a retry after a dropped connection) replaces what that showing sent before instead of adding
        its seconds, plays and pauses a second time (audit 2026-10-23, DS-03). Logs without a visit id (sent by a
        script of before) are added as always."""
        if not isinstance(old, dict) or not isinstance(new, dict) or old.get("key") != new.get("key"):
            return new if new is not None else old
        sent = new.get("visits") if isinstance(new.get("visits"), list) else []
        visit = sent[-1][0] if sent and isinstance(sent[-1], list) and len(sent[-1]) == 3 else ""
        visits = [list(v) for v in (old.get("visits") or []) if isinstance(v, list) and len(v) == 3]
        before, old_cut = list(old.get("log") or []), old.get("cut", 0)
        if visit and visits and visits[-1][0] == visit:
            _, events, cut = visits.pop()  # the same showing again: what it sent before goes
            before, old_cut = before[:max(len(before) - int(events or 0), 0)], max(old_cut - int(cut or 0), 0)
        elif visit and any(v[0] == visit for v in visits):
            return old  # an older send of a showing that another one followed: counted already
        base = before[-1][0] if before else 0
        log = before + [[ms + base, *rest] for ms, *rest in new.get("log", [])]
        kept, over = log[:MAX_EVENTS], max(len(log) - MAX_EVENTS, 0)
        out = {**new, "log": kept}
        out.pop("cut", None)
        cut = old_cut + new.get("cut", 0) + over
        if cut:
            out["cut"] = cut
        if visit or visits:
            # every send is noted once the log knows showings, also one without an id, so that a later retry never
            # takes away events of another showing
            out["visits"] = (visits + [[visit, len(kept) - len(before), new.get("cut", 0) + over]])[-MAX_EVENTS:]
        return out

    def soft_warning(self, item, answer, lang):
        """advance "any": once, while the stimulus has not been played to the end (concept E6)."""
        if self.advance(item) != "any" or not isinstance(answer, dict):
            return None
        f = figures(answer)
        if f is None or f["complete"] or any(row[1] == "error" for row in answer.get("log", [])):
            return None
        return (tr(lang, "Das Video ist noch nicht zu Ende. Sie können trotzdem weitergehen.",
                   "The video has not ended yet. You can go on anyway.") if self.kind_of(item) == "video"
                else tr(lang, "Die Aufnahme ist noch nicht zu Ende. Sie können trotzdem weitergehen.",
                        "The recording has not ended yet. You can go on anyway."))

    def is_missing(self, item, answer):
        return False  # nothing to nudge: "advance" decides

    # --- export ---------------------------------------------------------------------------------------------

    def export_columns(self, item):
        return [(f"{item.code}_{suffix}", label) for suffix, label, _ in FIGURES]

    def export_values(self, item, answer):
        f = figures(answer) or {}
        return {f"{item.code}_{suffix}": f.get(suffix) for suffix, _, _ in FIGURES}

    def column_level(self, item, column):
        suffix = column[len(item.code) + 1:] if column.startswith(item.code + "_") else ""
        return "nominal" if suffix in ("started", "complete", "muted") else "metric"

    def result_column(self, item, column):
        return None, "", None  # one block for the element instead (results(), surveys/_media_results.html)

    def codebook_info(self, item, column, lang, ui=None):
        suffix = column[len(item.code) + 1:] if column.startswith(item.code + "_") else ""
        row = next((r for r in FIGURES if r[0] == suffix), None)
        if row is None:
            return None
        notes = [in_lang(ui, "leer: kein Protokoll (ohne JavaScript, nicht gezeigt)",
                         "empty: no log (without JavaScript, not shown)")]
        if suffix == "seen":
            notes.append(in_lang(ui, "Teile, die übersprungen wurden, zählen nicht; Wiederholtes zählt einmal",
                                 "skipped parts do not count; what was repeated counts once"))
        if suffix in ("started", "complete", "muted"):
            return "Auswahl", "1 = ja; 0 = nein", notes
        return "Zahl", "", notes

    def long_columns(self, item):
        return ["n", "ms", "event", "a", "b", "c"]

    def long_rows(self, item, response):
        answer = (response.answers or {}).get(str(item.id))
        if not isinstance(answer, dict):
            return
        for n, row in enumerate(answer.get("log") or [], start=1):
            values = list(row[2:]) + [""] * (3 - len(row[2:]))
            yield [n, row[0], row[1], *values]

    # --- results (concept E12) -----------------------------------------------------------------------------

    def results(self, item, table, lang, passes=()):
        """The results page: of the logs of this element in the data pool (in a loop one per pass) how many started
        and saw it to the end, the mean share seen, and the curve "how many saw up to where": the share of logs that
        played each point (in per cent of the length), one line per file where there are several."""
        logs = []
        for row in table:
            response = table.numbered.get(row.get("resp_id"))
            for key, answer in ((response.answers or {}).items() if response is not None else ()):
                if key.split("#")[0] == str(item.id) and figures(answer) is not None:
                    logs.append(answer)
        n = len(logs)
        found = [figures(a) for a in logs]
        seen = [f["seen"] for f in found if f["seen"] is not None]
        out = {"persons": n, "started": sum(f["started"] for f in found) / n if n else None,
               "complete": sum(f["complete"] for f in found) / n if n else None,
               "seen": sum(seen) / len(seen) if seen else None, "lines": [], "passes": bool(passes)}
        groups = {}
        for a in logs:
            if a.get("dur"):
                groups.setdefault(a.get("key") or "", []).append(a)
        labels = {k: self.key_label(item, k) for k in groups} if len(groups) > 1 else {k: "" for k in groups}
        w, h, x_lo, x_hi, top, base = 600, 240, 44, 584, 12, 204
        steps = [i * 5 for i in range(21)]
        for k, group in sorted(groups.items(), key=lambda kv: labels[kv[0]])[:6]:
            spans = [_union(a) for a in group]
            shares = [sum(any(lo <= p / 100 * a["dur"] <= hi for lo, hi in sp) if p < 100 else
                          any(hi >= a["dur"] * COMPLETE for lo, hi in sp) or any(r[1] == "end" for r in a["log"])
                          for a, sp in zip(group, spans)) / len(group) for p in steps]
            points = " ".join(f"{x_lo + (x_hi - x_lo) * p / 100:.1f},{base - (base - top) * s:.1f}" for p, s in zip(steps, shares))
            out["lines"].append({"label": labels[k], "n": len(group), "points": points,
                                 "end": shares[-1], "mid": shares[10]})
        out["chart"] = {"w": w, "h": h, "x_lo": x_lo, "x_hi": x_hi, "top_y": top, "base_y": base,
                        "y_ticks": [(base - (base - top) * v, v) for v in (0, 0.25, 0.5, 0.75, 1)],
                        "x_ticks": [(x_lo + (x_hi - x_lo) * p / 100, p) for p in (0, 25, 50, 75, 100)]}
        return out

    def questionnaire_lines(self, item, lang):
        info = self.info(item) or {}
        seconds = int(info.get("seconds") or 0)
        what = tr(lang, "Video", "Video") if self.kind_of(item) == "video" else tr(lang, "Audio", "Audio")
        return [f"[{what}, {seconds // 60}:{seconds % 60:02d}]"]

    def dummy_fields(self, item, rng, meta, answers=None):
        """A plausible log: started or not, played on to a random point, sometimes to the end."""
        dur = (self.info(item) or {}).get("seconds") or 60
        if rng.random() < 0.08:
            return {f"i{item.id}": "[]"}
        to = dur if rng.random() < 0.7 else round(rng.uniform(0.1, 0.95) * dur, 1)
        log = [[400, "play", 0], [int(400 + to * 1000), "seg", 0, to, 0]]
        log.append([int(400 + to * 1000), "end", to] if to >= dur else [int(400 + to * 1000), "pause", to])
        return {f"i{item.id}": json.dumps(log)}

    def seconds(self, item, read):
        return ((self.info(item) or {}).get("seconds") or 30) + 5


def _union(answer):
    """The stretches of a log that played, joined where they overlap: [(from, to)] in seconds."""
    out = []
    for a, b in sorted((r[2], r[3]) for r in answer.get("log", []) if r[1] == "seg"):
        if out and a <= out[-1][1] + 0.25:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def clean_vtt(text):
    """Captions as WebVTT: the text as typed, with the header WEBVTT added if missing; "" for none. Nothing in it
    runs: the page hands it to <track> through a blob (participant.js), never as HTML."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:MAX_CAPTIONS]
    if not text:
        return ""
    return text if text.startswith("WEBVTT") else "WEBVTT\n\n" + text
