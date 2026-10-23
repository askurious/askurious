"""Dummy data: simulated participations through the real flow (source "dummy").

Each simulated participant goes through runtime.step like a person would: randomization,
loops, branches, and conjoint tasks are all real; answers are random but valid. Useful to
test analysis scripts before fieldwork. Dummy responses never count as participants.

The simulation itself takes milliseconds, so each dummy then gets a plausible completion time
around the survey's estimated duration (a few of them speeders); page times are spread over it.
"""
import random
from datetime import timedelta

from app import rows, runtime
from app.extensions import db
from app.models import Response

MAX_PAGES = 2000
SPEEDER_SHARE = 0.08     # share of dummies that rush through (30..45 % of the typical time)
MIN_SECONDS = 20         # floor for the typical time, e.g. a survey with hardly any items


def _random_fields(item, rng, meta, n, answers=None):
    """Form fields for one item with a random, valid answer."""
    c, key = item.config, f"i{item.id}"
    t = item.type
    if t in ("single_choice", "multiple_choice"):
        codes = [o["code"] for o in item.kind.visible(item, answers)]  # carried options: the ones this dummy sees
        if not codes:
            return {}
        if t == "single_choice":
            picked = [rng.choice(codes)]
        else:
            lo = c.get("min") or 1
            hi = min(c.get("max") or len(codes), len(codes))
            picked = rng.sample(codes, rng.randint(min(lo, hi), hi))
        # an option with a text field ("Anderes: ...") gets a text when it is chosen
        texts = {f"{key}_t_{o['id']}": f"Dummy-Antwort {n}" for o in item.kind.text_options(item) if o["code"] in picked}
        return {key: picked[0] if t == "single_choice" else picked, **texts}
    if t == "assign":
        import json

        cats = [cc["code"] for cc in c.get("categories", [])]
        els = [e["id"] for e in c.get("elements", [])]
        rng.shuffle(els)
        return {key: json.dumps([{"s": e, "c": rng.choice(cats), "rt": rng.randint(450, 2200)} for e in els])} if cats else {}
    if t == "ranking":
        codes = [o["code"] for o in item.kind.visible(item, answers)]
        rng.shuffle(codes)
        return {key: codes, f"{key}_touched": "1"} if codes else {}
    if t == "image_mark" and not item.kind.on_areas(item):  # the hot spot modes: dummy_fields of the type
        import json

        lo = c.get("min") or 1
        n = rng.randint(lo, max(lo, min(c.get("max") or 5, 8)))
        def pt():
            return [round(rng.random(), 4), round(rng.random(), 4)]
        if c.get("mode") == "lines":
            marks = [[pt() for _ in range(rng.randint(2, 6))] for _ in range(n)]
        else:
            marks = [pt() for _ in range(n)]
        return {key: json.dumps(marks)}
    if t == "map_marker":
        import json

        lo, hi = item.kind.limits(item)
        lat, lon = (c.get("center") or [51.0, 10.0])[:2]
        spread = 60 / 2 ** (c.get("zoom") or 6)  # roughly the start view
        markers = []
        for _ in range(rng.randint(max(lo, 1), max(lo, 1, min(hi, 5)))):
            q = {x["id"]: (rng.randint(1, x.get("points", 5)) if x["kind"] == "scale"
                           else rng.randint(1, len(x.get("options") or [1])) if x["kind"] == "choice" else f"Dummy-Antwort {n}")
                 for x in c.get("questions", [])}
            markers.append({"lat": max(-89.9, min(89.9, lat + rng.uniform(-spread, spread) / 2)),
                            "lon": max(-179.9, min(179.9, lon + rng.uniform(-spread, spread))), "q": q})
        return {key: json.dumps(markers)}
    if t == "slider":
        lo, hi, step = c.get("min", 0), c.get("max", 100), c.get("step", 1) or 1
        return {f"{key}_{r['id']}": str(lo + step * rng.randint(0, int((hi - lo) / step))) for r in c.get("rows", [])}
    if t == "constant_sum":
        # the total of this dummy: fixed, or what it answered to the earlier question
        codes, total = [o["code"] for o in item.kind.visible(item, answers)], item.kind.total_for(item, answers or {}) or 0
        cuts = sorted(rng.randint(0, total) for _ in codes[1:])
        shares = [b - a for a, b in zip([0] + cuts, cuts + [total])]
        return {**{f"{key}_{code}": str(v) for code, v in zip(codes, shares)}, f"{key}_touched": "1"} if codes else {}
    if t == "position2d":
        return {f"{key}_{o['code']}": f"{rng.uniform(0, 100):.1f},{rng.uniform(0, 100):.1f}" for o in c.get("options", [])}
    if t == "association":
        words = ["Zukunft", "Risiko", "Chance", "Arbeit", "Daten", "Kontrolle", "Fortschritt", "Angst", "Hilfe", "Technik"]
        fields = {}
        for k in range(1, rng.randint(max(c.get("min", 1), 1), c.get("slots", 3)) + 1):
            fields[f"{key}_{k}"] = rng.choice(words)
            fields[f"{key}_{k}_val"] = str(rng.randint(-2, 2))
        return fields
    if t == "psm":
        lo = c.get("min") if c.get("min") is not None else 1
        hi = c.get("max") if c.get("max") is not None else lo + 200
        prices = sorted(rng.sample(range(int(lo), int(hi) + 1), 4)) if hi - lo >= 3 else []
        fields = {f"{key}_{k}": str(p) for k, p in zip(("tc", "c", "e", "te"), prices)}
        if c.get("nms"):
            high = rng.randint(2, 5)  # buying at the "cheap" price is at least as likely as at the "expensive" one
            fields.update({f"{key}_nms_c": str(high), f"{key}_nms_e": str(rng.randint(1, high))})
        return fields
    if t == "likert":
        # simulated persons are attentive: an attention check gets the answer it asks for, 19 times out of 20
        checks = item.kind.checks(item)
        return {f"{key}_{s['id']}": str(checks[s["id"]] if s["id"] in checks and rng.random() < 0.95
                                        else rng.randint(1, c["points"])) for s in c.get("statements", [])
                if s["id"] not in rows.hidden(c.get("statements", []), answers, item.survey)}  # only the statements this dummy sees
    if t == "semantic":
        gone = rows.hidden(c.get("pairs", []), answers, item.survey)
        return {f"{key}_{p['id']}": str(rng.randint(1, c["points"])) for p in c.get("pairs", []) if p["id"] not in gone}
    if t == "rating":
        lo, hi = item.kind.bounds(item)
        if c["style"] == "slider":
            step = float(c.get("step") or 1)
            return {key: str(lo + step * rng.randint(0, int((hi - lo) / step)))}
        return {key: str(rng.randint(int(lo), int(hi)))}
    if t == "text_input":
        v = c.get("validation", "none")
        if v in ("integer", "decimal"):
            lo = c.get("min") if c.get("min") is not None else 0
            hi = c.get("max") if c.get("max") is not None else lo + 100
            return {key: str(rng.randint(int(lo), int(hi))) if v == "integer" else f"{rng.uniform(lo, hi):.2f}"}
        if v == "email":
            return {key: f"dummy{n}@example.org"}
        if v == "plz_de":
            from app.items.postal_code import random_code

            return {key: random_code(rng)}
        if v == "regex":
            return {}  # cannot invent a matching value; the page is then passed without checks
        return {key: f"Dummy-Antwort {n}"}
    if t == "postal_code":
        from app.items.postal_code import random_code

        return {key: random_code(rng)}  # only existing codes, so the map shows them
    if t == "consent":
        from app.items.consent import CONFIRMATIONS

        return {key: "1", **{f"{key}_c_{k}": "1" for k in CONFIRMATIONS}}
    # respondent: lets a type keep per-person values between pages (e.g. MaxDiff utilities)
    return item.kind.dummy_fields(item, rng, {**(meta or {}), "respondent": n}, answers) or {}


class _Form(dict):
    """Minimal MultiDict stand-in for the item parsers (get / getlist)."""

    def get(self, key, default=None):
        value = super().get(key, default)
        return value[0] if isinstance(value, list) and value else value

    def getlist(self, key):
        value = super().get(key, [])
        return value if isinstance(value, list) else [value]


def generate(survey, n, lang=None, seed=None, first=0):
    """Create n completed dummy responses. Returns the number created.
    first: how many dummies exist already; the simulated persons are numbered on from there."""
    from app import duration

    from app import quotas

    rng = random.Random(seed)
    lang = lang or survey.default_language
    tally = quotas.dummy_tally(survey) if survey.quotas else None  # the dummies' own count of quota places
    typical = max(duration.estimate(survey, lang)["typical"], MIN_SECONDS)
    # with a study plan the variables of its hypotheses get answers that hang together as planned (app/dummy_plan.py)
    from app import dummy_plan

    planned = dummy_plan.rows(survey, n, seed if seed is not None else rng.randrange(2 ** 31))
    created = 0
    for k in range(first, first + n):
        response = Response(survey_id=survey.id, is_preview=True, source="dummy", language=lang, answers={},
                            qversion=survey.qversion)
        if getattr(survey, "geo_ip", False):
            from app import geoip

            response.geo_country, response.geo_state = geoip.dummy(rng)  # drawn, not looked up
        runtime.ensure_layout(survey, response, rng)
        db.session.add(response)
        db.session.flush()
        for _ in range(MAX_PAGES):
            if response.status != Response.ACTIVE:
                break
            view = runtime.step(survey, response, _Form(), lang, "GET", quota_tally=tally)
            if view.external:
                # an external section: back at once, with a value for each declared one and a time outside
                from app import external

                back = {"pos": view.external["token"], "ext_back": "1", "ext_seconds": str(rng.randint(40, 400)),
                        **external.dummy_fields(view.external["node"], rng)}
                runtime.step(survey, response, _Form(back), lang, "POST", quota_tally=tally)
                continue
            if view.finished or view.redirect:
                continue
            fields = {"nav": "next", "nudge_ack": "1"}
            # the design version of a conjoint per question (items/conjoint.py: choices from a utility model)
            meta = {**(view.context["page_meta"] or {}), "cj_state": view.context.get("cj_state") or {},
                "pid": runtime.builtin_variables(response)["pid"]}  # the seed the page drew from (IAT order)
            mine = planned[k - first] if planned else {}
            for item in view.context["page_items"]:
                drawn = _random_fields(item, rng, meta, k + 1, view.context["answers"])
                # only fields the item asks of this dummy (a hidden statement stays unanswered)
                drawn.update({f: v for f, v in mine.get(item.id, {}).items() if f in drawn})
                fields.update(drawn)
            result = runtime.step(survey, response, _Form(fields), lang, "POST", quota_tally=tally)
            if not result.redirect:  # e.g. a regex field: pass the page without checks
                runtime.step(survey, response, _Form(fields), lang, "POST", validate=False, quota_tally=tally)
        if response.status == Response.COMPLETED:
            _simulate_time(response, rng, typical)
            created += 1
        # stored one by one: a single transaction for all of them would keep the database locked for as long as
        # the simulation runs, and participants who answer meanwhile could not store their pages
        db.session.commit()
    return created


def _walk(survey, response, rng, lang, shift=None):
    """Answer the pages of one simulated participation as generate() does; shift(item, fields): the fields of one
    item as answered (a diary person's own level, generate_diary). True if it completed."""
    for _ in range(MAX_PAGES):
        if response.status != Response.ACTIVE:
            break
        view = runtime.step(survey, response, _Form(), lang, "GET")
        if view.finished or view.redirect or view.external:
            if view.external:
                break
            continue
        fields = {"nav": "next", "nudge_ack": "1"}
        meta = {**(view.context["page_meta"] or {}), "cj_state": view.context.get("cj_state") or {},
                "pid": runtime.builtin_variables(response)["pid"]}  # the seed the page drew from (IAT order)
        for item in view.context["page_items"]:
            drawn = _random_fields(item, rng, meta, 1, view.context["answers"])
            fields.update(shift(item, drawn) if shift else drawn)
        result = runtime.step(survey, response, _Form(fields), lang, "POST")
        if not result.redirect:
            runtime.step(survey, response, _Form(fields), lang, "POST", validate=False)
    return response.status == Response.COMPLETED


def generate_diary(survey, n, lang=None, seed=None, first=0):
    """Dummy persons of a diary study (app/diary.py): each answers the entry part and then a share of their prompts
    so far (compliance drawn between 40 and 100 %), a few minutes after each prompt, plus a self-started report now and
    then; numeric answers lean on the person's own level, so persons differ (ICC) and days vary. Returns the number of
    persons."""
    import hashlib
    import secrets
    from datetime import timezone

    from app import diary
    from app.models import Enrolment, new_pid, utcnow

    rng = random.Random(seed)
    lang = lang or survey.default_language
    period = int((survey.sampling or {}).get("period_days") or 14)
    created = 0
    for k in range(first, first + n):
        now = utcnow()
        person = Enrolment(survey_id=survey.id, secret_hash=hashlib.sha256(secrets.token_bytes(16)).hexdigest(),
                           pid=new_pid(survey.code), source="dummy", status=Enrolment.ACTIVE, language=lang,
                           tz="Europe/Berlin", created_at=now)
        today = diary.local_today(person, now)
        person.first_day = today - timedelta(days=min(period, 7) - 1)
        # an open-ended study (diary.open_end) has no last day
        person.last_day = None if diary.open_end(survey) else person.first_day + timedelta(days=period - 1)
        db.session.add(person)
        db.session.flush()
        level = {}

        def shift(item, drawn):
            if item.type not in diary.TIME_TYPES:
                return drawn
            out = {}
            for f, v in drawn.items():
                try:
                    x = int(v)
                except (TypeError, ValueError):
                    out[f] = v
                    continue
                own = level.setdefault(f, x)  # the first answer is the person's level
                out[f] = str(round(0.65 * own + 0.35 * x))
            return out

        entry = Response(survey_id=survey.id, is_preview=True, source="dummy", language=lang, answers={},
                         qversion=survey.qversion, enrolment_id=person.id, occasion={"part": diary.ENTRY, "n": 1, "tz": person.tz})
        runtime.ensure_layout(survey, entry, rng)
        db.session.add(entry)
        db.session.flush()
        if not _walk(survey, entry, rng, lang):
            db.session.commit()
            continue
        start = datetime_of(person.first_day - timedelta(days=1), 18)
        entry.started_at, entry.completed_at = start - timedelta(minutes=8), start
        rate = rng.uniform(0.4, 1.0)
        count = {}
        for key, due in diary.prompts(survey, person, until=now):
            if rng.random() > rate:
                continue
            count[key] = count.get(key, 0) + 1
            local = due.replace(tzinfo=timezone.utc).astimezone(diary.zone(person.tz)).date()
            r = Response(survey_id=survey.id, is_preview=True, source="dummy", language=lang, answers={},
                         qversion=survey.qversion, enrolment_id=person.id,
                         occasion={"part": key, "n": count[key], "day": diary.day_index(person, local),
                                   "due": due.isoformat(), "notified": due.isoformat(), "tz": person.tz,
                                   "trigger": (diary.module(survey, key) or {}).get("trigger", {}).get("t", "fixed")})
            runtime.ensure_layout(survey, r, rng)
            db.session.add(r)
            db.session.flush()
            if _walk(survey, r, rng, lang, shift):
                r.started_at = due + timedelta(seconds=rng.randint(20, 900))
                r.completed_at = r.started_at + timedelta(seconds=rng.randint(30, 150))
        db.session.commit()
        created += 1
    return created


def datetime_of(day, hour):
    """Naive UTC of a local hour on `day` (Europe/Berlin, the dummies' zone)."""
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo

    return datetime.combine(day, time(hour), ZoneInfo("Europe/Berlin")).astimezone(timezone.utc).replace(tzinfo=None)


def _simulate_time(response, rng, typical):
    """Move started_at back by a simulated completion time: log-normal around the typical time,
    a few speeders; recorded page times (paradata "_t") are scaled to add up to it."""
    if rng.random() < SPEEDER_SHARE:
        seconds = typical * rng.uniform(0.3, 0.45)
    else:
        seconds = typical * rng.lognormvariate(0, 0.35)
    seconds = max(round(seconds), 5)
    response.started_at = response.completed_at - timedelta(seconds=seconds)
    times = (response.paradata or {}).get("_t")
    if times:
        weights = {key: rng.uniform(0.5, 1.5) for key in times}
        total = sum(weights.values())
        response.paradata = {**response.paradata, "_t": {key: round(seconds * w / total, 1) for key, w in weights.items()}}
