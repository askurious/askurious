"""Crowd (feedback, experimental): previous participants as abstract avatars that walk to their
answer on one question; the participant's own avatar is highlighted.

Each avatar is one previous (completed) participation: with more than N, a random sample per
participant that stays the same for all crowd elements of the survey, so from one question to
the next the same crowd walks from its last to its new position; the browser keeps the
positions for the session (sessionStorage, see participant.js). With fewer than N, the crowd is
topped up with figures drawn from the real answers; with fewer than MIN_N real answers all
figures are invented (a bell around the middle), none of the real ones is shown. Participants
see a note whenever figures are simulated, and the export counts them (<code>_sim).
Answers on a few categories get a stable jitter per avatar within their bin, plus a depth.

If the question is on the same page, the crowd walks once the participant has answered and the
own avatar follows changes of the answer. Before that it is hidden, unless an earlier crowd
(e.g. the previous loop pass) left positions and the option "keep_previous" is on (default):
then the figures stand there already, so the crowd does not pop up after the answer. That shows
the previous distribution before answering, which can anchor answers on similar questions.
Like all live comparisons: only from MIN_N answers on; exported is the number shown."""
import hashlib

from app import subkeys
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en
from app.items.base import r_flag, r_int  # rules for survey files, see base.py
from app.items.base import plain, whole
from app.items.sample_compare import MIN_N, SampleCompare
from app.ui_i18n import tr

MAX_AVATARS = 150


def _hash01(*parts):
    """A stable number in [0, 1) from the given parts."""
    h = hashlib.sha1(":".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:8], 16) / 0x100000000


@register
class SampleCrowd(SampleCompare):
    key = "sample_crowd"
    import_rules = {"sources": SampleCompare.import_rules["sources"], "subscales": SampleCompare.import_rules["subscales"],
                    "show_own": r_flag, "keep_previous": r_flag,
                    "avatars": r_int(MIN_N, MAX_AVATARS), "lock_answers": r_flag}
    label = Both("Rückmeldung als Menschenmenge", "Crowd feedback")
    short = Both("Die anderen als Figuren", "The others as figures")
    description = Both(f"Experimentell: bisherige Teilnehmende als Figuren, die zu ihrer Antwort laufen; "
                       f"die eigene hervorgehoben (ab {MIN_N})",
                       f"Experimental: participants so far as figures walking to their answer; one's own highlighted "
                       f"(from {MIN_N})")
    order = 57
    fact_sheet = FactSheet(
        "feedback",
        keywords=["Figuren", "Figures", "Rückmeldung", "Feedback", "Animation"],
        can=[("Die bisherigen Teilnehmenden als Figuren, die eigene Antwort hervorgehoben.",
              "Participants so far as figures, one’s own answer highlighted."),
             ("Ab 5 Antworten.", "From 5 answers.")],
        good_for=[("Anschauliche Rückmeldung für Laien, z. B. auf Veranstaltungen oder in der Lehre",
                   "Vivid feedback for laypeople, e.g. at events or in teaching")],
        not_for=[("Genaue Werte und Verteilungen (dann Stichprobenvergleich)",
                  "Exact values and distributions (then sample comparison)")],
        designs=["perception", "delphi"],
        examples=[("Wie viele denken wie Sie? Die Menge läuft zu ihren Antworten.",
                   "How many think like you? The crowd walks to their answers.")],
    )
    displays = {}

    def default_config(self):
        return {"sources": [], "show_own": True, "avatars": 40, "keep_previous": True, "lock_answers": True}

    def default_texts(self, lang):
        return {"question": tr(lang, "Hier stehen die anderen Teilnehmenden", "Where the other participants stand")}

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        texts[lang] = {**texts.get(lang, {}), "question": form.get("question", "").strip()}
        item.texts = texts
        if structural:
            ids, subs = self.read_sources([form.get("source", "")])
            n = form.get("avatars", "")
            item.config = {**item.config, "sources": ids, "subscales": subs,
                           "show_own": bool(form.get("show_own")),
                           "keep_previous": bool(form.get("keep_previous")),
                           "avatars": min(max(whole(n), MIN_N), MAX_AVATARS) if whole(n) is not None else 40}

    def validate(self, item):
        problems = []
        srcs = self.sources(item)
        if not srcs:
            problems.append(de_en("keine Frage ausgewählt", "no question chosen"))
        elif srcs[0].id not in {c.id for c in self.candidates(item)}:
            problems.append(de_en("die Frage muss vor diesem Element stehen (aus einer Schleife nur im selben Block)",
                                  "the question must come before this element (from a loop only in the same block)"))
        return problems

    # --- the scale: bins or a continuous axis -------------------------------------------

    def scale(self, src, lang):
        """{"bins": [labels] or None (continuous), "lo", "hi", "ends": (left, right), "live": {...}}.
        `live` describes the form field of the source for answers on the same page."""
        kind, c = src.kind, src.config or {}
        if src.type == "single_choice":
            opts = kind.options(src, lang)
            return {"bins": [o["label"] for o in opts], "codes": [o["code"] for o in opts], "ends": ("", ""),
                    "live": {"name": f"i{src.id}", "type": "code"}}
        if src.type == "rating":
            lo, hi = kind.bounds(src)
            t = src.t(lang)
            ends = (t.get("left", ""), t.get("right", ""))
            if c.get("style") != "slider" and hi - lo <= 10:
                return {"bins": [str(v) for v in range(int(lo), int(hi) + 1)], "lo": lo, "ends": ends,
                        "live": {"name": f"i{src.id}", "type": "bin", "lo": lo}}
            return {"bins": None, "lo": float(lo), "hi": float(hi), "ends": ends,
                    "live": {"name": f"i{src.id}", "type": "value"}}
        units = c.get("statements") or c.get("pairs") or []
        points = c.get("points", 5)
        if len(units) == 1:  # one statement or pair: its points are the bins
            if src.type == "likert":
                labels = kind.labels(src, lang)
                ends = ("", "")
            else:
                pair = kind.pairs(src, lang)[0]
                labels = [""] * points
                ends = (pair["left"], pair["right"])
            reverse = src.type == "likert" and units[0].get("reverse")
            return {"bins": labels, "lo": 1, "ends": ends, "reverse": bool(reverse),
                    "live": {"name": f"i{src.id}_{units[0]['id']}", "type": "bin", "lo": 1}}
        # matrix: the person's scale mean (reverse coding applied), continuous from 1 to points
        _, _, _, left, right = self.axis(src, lang)
        return {"bins": None, "lo": 1.0, "hi": float(points), "ends": (left, right), "live": None}

    def base(self, src, scale, answer):
        """Where an answer lies before jitter: ("bin", index) or ("val", 0..1); None if missing."""
        if answer is None or answer == "" or answer == {}:
            return None
        if scale["bins"] is not None:
            if src.type == "single_choice":
                answer = src.kind.chosen(answer)
                return ("bin", scale["codes"].index(answer)) if answer in scale["codes"] else None
            v = next(iter(answer.values()), None) if isinstance(answer, dict) else answer
            if not isinstance(v, (int, float)):
                return None
            b = int(v - scale["lo"])
            return ("bin", b) if 0 <= b < len(scale["bins"]) else None
        v = self._number(src, answer, scale.get("sub"))
        if v is None:
            return None
        return ("val", min(max((v - scale["lo"]) / ((scale["hi"] - scale["lo"]) or 1), 0.0), 1.0))

    def _number(self, src, answer, sub=None):
        """The number a figure stands at on a continuous axis: a rating as answered (its ends stay as the question
        shows them, and the own answer taken live from the page is the raw value too; audit 2026-10-12, M-6: the
        recoded value of a reversed slider stood beside the unswapped ends), else value()."""
        if src.type == "rating":
            return float(answer) if isinstance(answer, (int, float)) and not isinstance(answer, bool) else None
        return self.value(src, answer, sub)

    @staticmethod
    def place(scale, base, jitter):
        """x in [0, 1]: the centre of the bin (or the value) plus a stable jitter."""
        kind, v = base
        if kind == "bin":
            return (v + 0.5 + (jitter - 0.5) * 0.7) / len(scale["bins"])
        return min(max(0.03 + 0.94 * v + (jitter - 0.5) * 0.03, 0.0), 1.0)

    def position(self, src, scale, answer, jitter):
        b = self.base(src, scale, answer)
        return self.place(scale, b, jitter) if b is not None else None

    @staticmethod
    def invented(src, scale, u, v):
        """A made-up answer (uniform u, v in [0, 1)) when there are too few real ones: a bell around
        the middle of the scale; single choice evenly spread."""
        import math

        z = math.sqrt(-2 * math.log(max(u, 1e-9))) * math.cos(2 * math.pi * v)  # Box-Muller
        if scale["bins"] is not None:
            k = len(scale["bins"])
            if src.type == "single_choice":
                return ("bin", min(int(u * k), k - 1))
            return ("bin", min(max(int(round((k - 1) / 2 + z * k / 5)), 0), k - 1))
        return ("val", min(max(0.5 + z * 0.18, 0.0), 1.0))

    # --- the crowd -------------------------------------------------------------------------

    def locks_on_page(self, item, source, page_items, locked_ids):
        """Does the crowd wait for "Antwort festlegen" before its figures may reach the page (app/feedback_lock.py)?"""
        from app import feedback_lock

        return feedback_lock.waiting(item, source, page_items, locked_ids)

    def crowd(self, item, lang, own_answers, preview=False, seed=0, row=None):
        """Figures for the crowd: a random sample of real participations (per participant, but the
        same for all crowd elements of a survey); topped up to N figures by drawing from the real
        answers; with fewer than MIN_N real answers only invented figures (none of the real ones)."""
        from app import balance

        src = next(iter(self.sources(item)), None)
        if src is None:
            return None
        scale = {**self.scale(src, lang), "sub": self.effective_subscale(item, src)}  # one subscale, not all mixed
        survey, uid, want = item.survey, item.survey.uid, item.config.get("avatars", 40)
        key = self.pool_key(item, src, row)  # inside a loop: the answers of the same pass
        # ids and this one answer of the completed participations, kept for a few seconds (app/balance.py)
        pool = sorted(balance.answers(survey, key, preview), key=lambda p: _hash01(uid, seed, item.id, p[0]))
        real = []
        for rid, answer in pool:
            b = self.base(src, scale, answer)
            if b is not None:
                real.append((rid, b))

        def figure(fid, base):
            return {"id": fid, "x": round(self.place(scale, base, _hash01(uid, seed, fid, "x", item.id)), 4),
                    "d": round(_hash01(uid, seed, fid, "depth"), 3), "h": round(_hash01(uid, seed, fid, "hue"), 3)}

        avatars = []
        if len(real) >= MIN_N:
            for rid, b in real[:want]:
                fid = subkeys.mac("crowd", f"crowd:{uid}:{item.id}:{seed}:{rid}")[:16]  # a key of its own (CFG-08)
                avatars.append(figure(fid, b))
            for i in range(want - len(avatars)):  # top up: draw from the real answers
                pick = real[int(_hash01(uid, seed, "draw", i, item.id) * len(real))][1]
                avatars.append(figure(f"sim{i}", pick))
        else:
            for i in range(want):
                base = self.invented(src, scale, _hash01(uid, seed, "u", i, item.id), _hash01(uid, seed, "v", i, item.id))
                avatars.append(figure(f"sim{i}", base))
        simulated = sum(1 for a in avatars if a["id"].startswith("sim"))
        own_answer = (own_answers or {}).get(src.id)
        own = self.position(src, scale, own_answer, 0.5) if own_answer is not None else None
        return {"source": src, "scale": scale, "n": len(real), "enough": len(real) >= MIN_N,
                "avatars": avatars, "simulated": simulated, "own": own, "show_own": item.config.get("show_own", True),
                "own_text": self.own_text(src, scale, own_answer, lang) if own_answer is not None else ""}

    def own_text(self, src, scale, answer, lang):
        """Where the own figure stands, in words: the figure alone shows it only by its colour (accessibility D8).
        The answer category, else its number on the scale; a scale mean with one decimal."""
        from app.units import number

        b = self.base(src, scale, answer)
        if b is None:
            return ""
        if b[0] == "bin":
            k = len(scale["bins"])
            label = plain(scale["bins"][b[1]]) if scale["bins"][b[1]] else ""
            return label or tr(lang, "{n} von {k}", "{n} of {k}", n=b[1] + 1, k=k)
        v = self._number(src, answer, scale.get("sub"))
        return number(v, 1, lang) if v is not None else ""

    # --- export: real answers shown and simulated figures ------------------------------------

    def parse_answer(self, item, form, lang="de", meta=None):
        shown = super().parse_answer(item, form, lang, meta) or {}
        from flask import g, has_request_context

        from app.models import display_seed

        response = g.get("participation") if has_request_context() else None
        if shown and response is not None:
            crowd = self.crowd(item, lang, {}, preview=bool(response.is_preview), seed=display_seed(response),
                               row=(meta or {}).get("row"))
            if crowd is not None:
                shown["sim"] = crowd["simulated"]
        elif shown:
            # Synthetic researcher runs have no participation context; still bound their declared count.
            sim = whole(form.get(f"i{item.id}_sim", ""))
            if sim is not None:
                shown["sim"] = min(max(sim, 0), item.config.get("avatars", 40))
        return shown or None

    def export_columns(self, item):
        return super().export_columns(item) + [
            (f"{item.code}_sim", "Anzahl simulierter Figuren in der Menschenmenge (ergänzt oder erfunden)")]

    def export_values(self, item, answer):
        return {**super().export_values(item, answer), f"{item.code}_sim": (answer or {}).get("sim")}
