"""One sample model for every page (UI/UX audit 2026-10-07, S3).

Stored are only what the test needs and the expected exclusions: "verwertbar nötig" comes from the power analysis
of the study plan (app/study_plan.power_summary) or, without one, from the sample target the calculator or the
researcher entered; "Ausschlüsse %" from the study plan (default study_plan.EXCLUSIONS, as the calculator). From
them follow the completions (= the sample target and the end of data collection) and, for an online panel with a
response rate, the invitations. Study plan, calculator, data collection, quota assistant, export and methods
section show the same line through the macro sample_line (templates/_sample.html):

    84 verwertbar · 94 Abschlüsse · bei 10 % Ausschlüssen

The pre-launch check (study_plan.findings) compares the stored targets against the same function.
"""
import math

from app.lang import de_en

RESPONSE_FOR = ("panel",)  # recruitments that invite people: only an online panel asks for a response rate


def completions(usable, exclusions):
    """The completions to plan for so that `usable` remain after `exclusions` % are left out (rounded up; the same
    formula as samplesize.js)."""
    share = 1 - min(max(exclusions or 0, 0), 90) / 100
    return math.ceil(usable / share - 1e-9)


def usable_of(completed, exclusions):
    """The usable participations that `completed` completions leave at `exclusions` % (rounded down): the inverse
    of completions(), so that a target made from n usable ones gives n back."""
    share = 1 - min(max(exclusions or 0, 0), 90) / 100
    return max(0, math.floor(completed * share + 1e-9))


def invitations(completed, response):
    """The invitations that bring `completed` completions at a response rate of `response` %, None without a rate."""
    if not response:
        return None
    return math.ceil(completed / (response / 100) - 1e-9)


def numbers(usable, exclusions, response=None, completed=None):
    """The model from its stored values: {"usable", "completions", "exclusions", "response", "invite"}. completed:
    a target given as completions (it stays as it is; usable then follows from it). Neither: the numbers are None
    (the calculator fills them in the browser)."""
    if usable is None and completed is None:
        return {"usable": None, "completions": None, "exclusions": exclusions, "response": response or None,
                "invite": None}
    if completed is None:
        completed = completions(usable, exclusions)
    elif usable is None:
        usable = usable_of(completed, exclusions)
    return {"usable": usable, "completions": completed, "exclusions": exclusions, "response": response or None,
            "invite": invitations(completed, response)}


def of(survey):
    """The sample model of a survey, None while nothing gives a number: numbers() plus "basis" ("power": the power
    analysis of the study plan sets the usable participations; "target": the sample target of the plan; "end":
    the end of data collection after completions), "by" (the hypotheses whose power analysis sets the number),
    "target" (the stored sample target of the plan, or None) and "end" (survey.max_responses, or None)."""
    from app import study_plan

    plan = study_plan.plan_of(survey) if survey is not None else None
    if plan is None:
        return None
    sample = plan["sample"]
    excl = sample.get("exclusions")
    excl = study_plan.EXCLUSIONS if excl is None else excl
    response = sample.get("response") if sample.get("recruitment") in RESPONSE_FOR else None
    target, end = sample.get("target"), getattr(survey, "max_responses", None)
    ps = study_plan.power_summary(survey) if getattr(survey, "id", None) else None
    if ps:
        out = {**numbers(ps["n"], excl, response), "basis": "power", "by": ps["by"]}
    elif target:
        out = {**numbers(None, excl, response, completed=target), "basis": "target", "by": []}
    elif end:
        out = {**numbers(None, excl, response, completed=end), "basis": "end", "by": []}
    else:
        return None
    out.update(target=target, end=end)
    return out


def for_calculator(survey):
    """What the calculator (/sample-size/) shows of the survey's model next to its own result: the expected
    exclusions and the response rate of a panel (both None without a survey)."""
    if survey is None:
        return {"exclusions": None, "response": None}
    from app import study_plan

    sample = study_plan.plan_of(survey)["sample"]
    return {"exclusions": sample.get("exclusions"),
            "response": sample.get("response") if sample.get("recruitment") in RESPONSE_FOR else None}


def text(m, lang=None):
    """The line of the model as plain text, in the language of the page or `lang`: "84 verwertbar · 94 Abschlüsse ·
    bei 10 % Ausschlüssen" ("84 usable · 94 completions · at 10% exclusions"), with " · 188 einladen bei 50 %
    Rücklauf" for a panel with a response rate. The same words as the macro sample_line."""
    from app import lang as ui_lang
    from app import units

    lang = lang or ui_lang.current()
    en = lang == "en"
    n = (lambda x: units.whole_text(x, lang))
    pct = (lambda x: f"{x}%" if en else f"{x} %")
    parts = [f"{n(m['usable'])} {'usable' if en else 'verwertbar'}",
             f"{n(m['completions'])} {'completions' if en else 'Abschlüsse'}",
             (f"at {pct(m['exclusions'])} exclusions" if en else f"bei {pct(m['exclusions'])} Ausschlüssen")]
    if m.get("invite"):
        parts.append(f"invite {n(m['invite'])} at a {pct(m['response'])} response rate" if en
                     else f"{n(m['invite'])} einladen bei {pct(m['response'])} Rücklauf")
    return " · ".join(parts)


def basis_text(m):
    """Where the usable participations come from, for the hint beside the line."""
    if m["basis"] == "power":
        return de_en("aus der Poweranalyse ({h})", "from the power analysis ({h})", h=", ".join(f"H{i}" for i in m["by"]))
    if m["basis"] == "target":
        return de_en("aus dem Stichprobenziel", "from the sample target")
    return de_en("aus dem Feldende", "from the end of data collection")


def reserve_text(m):
    """A stored sample target that differs from the completions of the power analysis, said in one sentence (UI/UX
    audit 2026-10-09, X5): "Ziel 120 liegt über den 94 nötigen Abschlüssen (Reserve +26)."; "" where they agree or
    where no power analysis sets the number."""
    if not m or m.get("basis") != "power" or not m.get("target") or m.get("completions") is None:
        return ""
    from app import lang as ui_lang
    from app import units

    target, need = m["target"], m["completions"]
    if target == need:
        return ""
    lang = ui_lang.current()
    t, n, d = (units.whole_text(v, lang) for v in (target, need, abs(target - need)))
    if target > need:
        return de_en("Ziel {t} liegt über den {n} nötigen Abschlüssen (Reserve +{d}).",
                     "Target {t} is above the {n} completions needed (reserve +{d}).", t=t, n=n, d=d)
    return de_en("Ziel {t} liegt unter den {n} nötigen Abschlüssen (es fehlen {d}).",
                 "Target {t} is below the {n} completions needed ({d} short).", t=t, n=n, d=d)


def lead_text(m):
    """The short text in front of the line: where the target is set."""
    return de_en("Feldende:", "End of data collection:") if m["basis"] == "end" else de_en("Studienplan:", "Research plan:")
