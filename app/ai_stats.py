"""AI usage for the admin page "KI-Nutzung aller Konten": requests by purpose, model and item type over a day, a week
or a month (rolling), with tokens as reported by OpenAI and costs estimated from a price table that admins
keep on the page (instance/ai_prices.json, never overwritten by a deploy). The calls themselves are logged
by app/ai.py (AiCall, kept for two years)."""
import json
import os
from datetime import timedelta

from flask import current_app

from app.extensions import db
from app.lang import de_en, english

PURPOSES = {
    "consent": "Einwilligungstext",
    "construct_ideas": "Konstrukt-Ideen (ganzer Fragebogen)",
    "scale_items": "Skalen-Items formulieren",
    "image_prompt_review": "Bild-Prompts prüfen (Wahlexperimente)",
    "text_review": "Texte prüfen (eine Frage)",
    "survey_review": "Texte prüfen (ganzer Fragebogen)",
    "methods": "Methodenteil",
    "translation": "Übersetzung",
    "image": "Bild (einzeln)",
    "image_set": "Bildvarianten (Schleife, Conjoint)",
    "vignette_factors": "Vignetten: Faktoren vorschlagen",
    "vignette_levels": "Vignetten: Stufen ergänzen",
    "vignette_prohibitions": "Vignetten: unplausible Kombinationen",
    "vignette_text": "Vignetten: Vignettentext",
    "ainstein_chat": "AInstein: Gespräch zum Forschungs-Canvas",
    "ainstein_plan": "AInstein: Hypothesen und Konstrukte ableiten",
    "coding_scheme": "Offene Antworten: Kategorien vorschlagen",
    "coding_assign": "Offene Antworten: Zuordnung vorschlagen",
    "paper_read": "Papierfragebogen: Handschrift lesen",
    "interview_collect": "Interviewleitfaden: Fragen sammeln",
    "interview_check": "Interviewleitfaden: Fragen prüfen",
    "interview_sort": "Interviewleitfaden: Fragen sortieren",
    "interview_subsume": "Interviewleitfaden: Fragen subsumieren",
    "interview_extend": "Interviewleitfaden: Leitfrage ergänzen",
    "interview_review": "Interviewleitfaden: ganzen Leitfaden prüfen",
    "interview_short": "Interviewleitfaden: Kurzfragebogen vorschlagen",
    "interview_post": "Interviewleitfaden: Postskript vorschlagen",
    "interview_categories": "Interviewleitfaden: Kategoriensystem",
    "live_questions": "Live-Abstimmung: Fragen vorschlagen",
    "diary_design": "Tagebuchstudie: Design vorschlagen",
    "diary_questions": "Tagebuchstudie: Moment-Fragen vorschlagen",
    "diary_check": "Tagebuchstudie: Plan prüfen",
    "blog_draft": "Blog: Entwurf aus den Neuerungen",
    "blog_translation": "Blog: englische Fassung",
    "blog_proofreading": "Blog: Textkorrekturen",
    "text": "Text (sonstiges)",
    "other": "Sonstiges",
}
PURPOSES_EN = {
    "consent": "Consent text",
    "construct_ideas": "Construct ideas (whole questionnaire)",
    "scale_items": "Writing scale items",
    "image_prompt_review": "Checking image prompts (choice experiments)",
    "text_review": "Checking texts (one question)",
    "survey_review": "Checking texts (whole questionnaire)",
    "methods": "Methods section",
    "translation": "Translation",
    "image": "Image (single)",
    "image_set": "Image variants (loop, conjoint)",
    "vignette_factors": "Vignettes: suggest factors",
    "vignette_levels": "Vignettes: add levels",
    "vignette_prohibitions": "Vignettes: implausible combinations",
    "vignette_text": "Vignettes: vignette text",
    "ainstein_chat": "AInstein: conversation on the research canvas",
    "ainstein_plan": "AInstein: derive hypotheses and constructs",
    "coding_scheme": "Open answers: propose categories",
    "coding_assign": "Open answers: suggest an assignment",
    "paper_read": "Paper questionnaire: reading handwriting",
    "interview_collect": "Interview guide: collect questions",
    "interview_check": "Interview guide: check questions",
    "interview_sort": "Interview guide: sort questions",
    "interview_subsume": "Interview guide: subsume questions",
    "interview_extend": "Interview guide: extend a guide question",
    "interview_review": "Interview guide: check the whole guide",
    "interview_short": "Interview guide: suggest a short questionnaire",
    "interview_post": "Interview guide: suggest a postscript",
    "interview_categories": "Interview guide: category system",
    "live_questions": "Live poll: suggest questions",
    "diary_design": "Diary survey: suggest a design",
    "diary_questions": "Diary survey: suggest moment questions",
    "diary_check": "Diary survey: check the plan",
    "blog_draft": "Blog: draft from what is new",
    "blog_translation": "Blog: English version",
    "blog_proofreading": "Blog: text corrections",
    "text": "Text (other)",
    "other": "Other",
}
# the periods of Administration > Befragungen (app/admin_stats.py), with "seit" a date (Ralf, 2026-10-19)
DEFAULT = "7"
OLD_PERIODS = {"day": "24h", "week": "7", "month": "30"}  # the keys of links from before 2026-10-19


def purposes():
    """PURPOSES in the language of the page; keys unchanged."""
    if not english():
        return PURPOSES
    return {key: PURPOSES_EN.get(key, label) for key, label in PURPOSES.items()}


def periods():
    """{key: label} of the switch in the language of the page, the same as on Befragungen."""
    from app import admin_stats

    return admin_stats.period_labels()


def period_key(key):
    """The key of the switch for a link's "period", DEFAULT for one it does not know."""
    from app import admin_stats

    key = OLD_PERIODS.get(key, key)
    return key if key in admin_stats.PERIODS else DEFAULT


def _price_file():
    return current_app.config.get("AI_PRICES_FILE") or os.path.join(current_app.instance_path, "ai_prices.json")


def prices():
    """{"text": {model: {"in": € per 1M tokens, "out": …}}, "image": {model: {size: € per image}}}"""
    try:
        with open(_price_file()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return {"text": data.get("text") or {}, "image": data.get("image") or {}}


def save_prices(data):
    os.makedirs(os.path.dirname(_price_file()), exist_ok=True)
    with open(_price_file(), "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)


def cost(call, table):
    """(estimated €, whether every call in it has a price) of a group of calls with the same model, size and outcome
    (_rows); € None if the model (and size) has no price yet. A text call without token counts adds nothing."""
    if call.kind == "text":
        p = table["text"].get(call.model)
        if not p or call.missing == call.n:
            return None, False
        k = ((call.input_tokens or 0) * (p.get("in") or 0) + (call.output_tokens or 0) * (p.get("out") or 0)) / 1e6
        return k, call.missing == 0
    p = (table["image"].get(call.model) or {}).get(call.size or "")
    return (p * (call.images or 0), True) if p is not None else (None, False)


def _group(calls, key, table):
    out = {}
    for c in calls:
        g = out.setdefault(key(c), {"calls": 0, "failed": 0, "in": 0, "out": 0, "images": 0, "cost": 0.0, "priced": True})
        g["calls"] += c.n
        g["failed"] += 0 if c.ok else c.n
        g["in"] += c.input_tokens or 0
        g["out"] += c.output_tokens or 0
        g["images"] += c.images or 0
        k, whole = cost(c, table)
        if k is not None:
            g["cost"] += k
        if not whole:
            g["priced"] = g["priced"] and not c.ok  # failed calls cost nothing
    for g in out.values():
        ok = g["calls"] - g["failed"]
        g["in_avg"], g["out_avg"] = (g["in"] / ok, g["out"] / ok) if ok else (0, 0)
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["cost"], -kv[1]["calls"])))


def _rows(start, before, step):
    """The calls since `before`, summed by the database (audit 2026-10-10, K6: with own keys ai_call grows by up to
    2000 rows a day per person, and every row was loaded): one row per kind, purpose, model, size, element type,
    outcome, access, provider and time step (`bucket`: its index since `start`, -1 before `start`)."""
    from types import SimpleNamespace

    from sqlalchemy import Integer, case, cast

    from app.models import AiCall

    f, seconds = db.func, step.total_seconds()
    if db.session.get_bind().dialect.name == "postgresql":
        index = cast(f.floor(f.extract("epoch", AiCall.at - start) / seconds), Integer)
    else:  # SQLite; CAST cuts toward zero, and the difference is never negative here
        index = cast((f.julianday(AiCall.at) - f.julianday(start)) * 86400.0 / seconds, Integer)
    bucket = case((AiCall.at >= start, index), else_=-1).label("bucket")
    keys = (AiCall.kind, AiCall.purpose, AiCall.model, AiCall.size, AiCall.item_type, AiCall.ok, AiCall.source,
            AiCall.provider)
    found = db.session.execute(
        db.select(*keys, bucket, f.count(AiCall.id), f.sum(AiCall.input_tokens), f.sum(AiCall.output_tokens),
                  f.sum(AiCall.images), f.sum(case((AiCall.input_tokens.is_(None), 1), else_=0)))
        .filter(AiCall.at >= before).group_by(*keys, bucket)).all()
    return [SimpleNamespace(kind=r[0], purpose=r[1], model=r[2], size=r[3], item_type=r[4], ok=r[5], source=r[6],
                            provider=r[7], bucket=int(r[8]), n=int(r[9]), input_tokens=int(r[10] or 0),
                            output_tokens=int(r[11] or 0), images=int(r[12] or 0), missing=int(r[13] or 0))
            for r in found]


def report(period=DEFAULT, since=None, now=None):
    """The calls of the chosen period (admin_stats.window: "24h", "7", "30", "365" or "since" with a date) and of
    the one of the same length before (none before "seit")."""
    import math

    from app import admin_stats
    from app.models import utcnow

    now = now or utcnow()
    period = period_key(period)
    if period == "since" and since is None:  # "seit" without a date: the standard of this page
        period = DEFAULT
    w = admin_stats.window(period, since, now)
    period, start = w["key"], w["start"]
    before = w["prev"] or start
    # time course: per hour for 24 hours, per day up to two months, per week beyond; stacked by purpose
    step = timedelta(hours=1) if w["step"] == "hour" else timedelta(days=1) if w["step"] == "day" else timedelta(days=7)
    n = max(math.ceil((now - start) / step), 1)
    calls = _rows(start, before, step)
    # calls over a researcher's own key (app/ai_access.py) cost the installation nothing: on their own, without prices
    own = [c for c in calls if c.source == "own" and c.bucket >= 0]
    calls = [c for c in calls if c.source != "own"]
    current = [c for c in calls if c.bucket >= 0]
    previous = [c for c in calls if c.bucket < 0]
    table = prices()
    total = _group(current, lambda c: "all", table).get("all")
    prev = _group(previous, lambda c: "all", table).get("all")
    buckets = [{"start": start + i * step, "by": {}} for i in range(n)]
    for c in current:
        i = min(c.bucket, n - 1)
        k = cost(c, table)[0] or 0
        buckets[i]["by"][c.purpose] = buckets[i]["by"].get(c.purpose, 0) + (k if table_has_any(table) else c.n)
    models = sorted({c.model for c in calls} | set(table["text"]) | set(table["image"]))
    return {
        "period": period, "since": w["since"], "hourly": w["step"] == "hour", "start": start, "now": now, "total": total,
        "prev": prev, "compare": w["prev"] is not None,
        "by_purpose": _group(current, lambda c: c.purpose, table),
        "by_model": _group(current, lambda c: c.model, table),
        "by_item": _group([c for c in current if c.item_type], lambda c: c.item_type, table),
        "own": _group(own, lambda c: c.provider or "custom", {"text": {}, "image": {}}),
        "buckets": buckets, "step": de_en("Stunde", "hour") if w["step"] == "hour" else de_en("Tag", "day") if w["step"] == "day" else de_en("Woche", "week"),
        "unit": "€" if table_has_any(table) else de_en("Aufrufe", "calls"),
        **known_models(table),
        "models": models, "prices": table,
    }


def known_models(table):
    """The models the price table lists: every model Askurious has called (ever, not only in the period), the
    configured ones (before their first call) and those already priced. Nobody types a model name: a new model
    appears by itself, with empty prices to fill in (Philipp, 2026-10-04)."""
    from app.models import AiCall

    platform = db.or_(AiCall.source.is_(None), AiCall.source != "own")  # own keys' models have no price here
    used_text = set(db.session.scalars(db.select(AiCall.model).filter(AiCall.kind == "text", platform).distinct()))
    used_images = set(db.session.execute(db.select(AiCall.model, AiCall.size).filter(AiCall.kind == "image", AiCall.size.is_not(None))
                                         .distinct()).all())
    cfg = current_app.config
    text = {m for m in used_text | {cfg.get("OPENAI_TEXT_MODEL")} | set(table["text"]) if m}
    images = {(m, s) for m, s in used_images if m and s} | {(m, s) for m, sizes in table["image"].items() for s in sizes}
    if cfg.get("OPENAI_IMAGE_MODEL") and not any(m == cfg["OPENAI_IMAGE_MODEL"] for m, _ in images):
        images.add((cfg["OPENAI_IMAGE_MODEL"], "1536x1024"))  # the size Askurious asks for (ai.generate_image)
    return {"text_models": sorted(text), "image_sizes": sorted(images)}


def table_has_any(table):
    return bool(table["text"] or table["image"])


def chart(r, width=760, height=180):
    """Stacked bars per hour or day as SVG (cost in €, or the number of calls while there are no prices)."""
    from markupsafe import Markup, escape

    from app.timeutil import to_local

    names = purposes()
    used = {p for b in r["buckets"] for p in b["by"]}
    # a purpose missing from PURPOSES still shows, under its key (code review 2026-10-15, K5)
    shown = [p for p in PURPOSES if p in used] + sorted(used - set(PURPOSES))
    colors = ["#2a5db0", "#e07a2e", "#2e9e6b", "#8a5cc2", "#c94f6d", "#7a8691", "#b8a13a", "#4aa3c9"]
    top = max([sum(b["by"].values()) for b in r["buckets"]] + [0])
    if not top:
        return Markup('<p class="muted">{}</p>').format(de_en("Im gewählten Zeitraum gab es keine KI-Aufrufe.",
                                                             "There were no AI calls in the chosen period."))
    n, left, bottom = len(r["buckets"]), 44, height - 22
    bw = (width - left - 4) / n
    parts = [f'<line x1="{left}" y1="{bottom}" x2="{width - 4}" y2="{bottom}" class="aic-axis"/>',
             f'<text x="{left - 6}" y="14" text-anchor="end" class="aic-tick">{escape(_num(top, r["unit"]))}</text>',
             f'<text x="{left - 6}" y="{bottom}" text-anchor="end" class="aic-tick">0</text>']
    for i, b in enumerate(r["buckets"]):
        y = bottom
        for j, p in enumerate(shown):
            v = b["by"].get(p, 0)
            if not v:
                continue
            h = v / top * (bottom - 10)
            y -= h
            parts.append(f'<rect x="{left + i * bw + 1:.1f}" y="{y:.1f}" width="{max(bw - 2, 1):.1f}" height="{h:.1f}" '
                         f'fill="{colors[j % len(colors)]}"><title>{escape(names.get(p, p))}: {escape(_num(v, r["unit"]))}</title></rect>')
        if not r["hourly"] or i % 3 == 0:
            local = to_local(b["start"])
            label = f'{local:%H}' if r["hourly"] else (f'{local:%d/%m}' if english() else f'{local:%d.%m.}')
            if r["hourly"] or n <= 8 or i % 5 == 0:
                parts.append(f'<text x="{left + (i + 0.5) * bw:.1f}" y="{height - 6}" text-anchor="middle" class="aic-tick">{label}</text>')
    legend = "".join(f'<span class="aic-key"><i style="background:{colors[j % len(colors)]}"></i>{escape(names.get(p, p))}</span>'
                     for j, p in enumerate(shown))
    course = escape(de_en("Verlauf", "Over time"))
    return Markup(f'<svg viewBox="0 0 {width} {height}" class="aic-chart" role="img" aria-label="{course}">{"".join(parts)}</svg>'
                  f'<p class="aic-legend">{legend}</p>')


def _num(v, unit):
    from app.units import fixed

    if unit != "€":
        return fixed(v, 0)
    return f"{fixed(v, 2)} €" if english() else f"{fixed(v, 2)} €".replace(".", ",")
