"""The drawing of the model in the study plan (concept modell-zeichnen-konzept.md, Ralf
2026-10-11: F1 to F10 as recommended). The list of hypotheses stays what is stored; the drawing is a second view of
it (static/model.js): every arrow is a hypothesis of the list, and what the drawing changes goes into the rows of the
form, which saves as before. This module gives the drawing what it reads from the server: the variables that can
take part, and the line under the drawing that says what the model asks of the data (sem_model)."""
from app.lang import de_en


def _short(text, limit):
    text = " ".join((text or "").split())
    return (text[:limit - 2].rstrip() + " …") if len(text) > limit else text


def _info(survey, code, thing):
    """One variable as the drawing shows it: {"code", "name", "question", "shape": "latent" | "box", "level":
    "metric" | "ordinal" | "nominal", "groups" (number of groups, 0 for numbers), "rand" (a randomisation), "sem"
    (can enter the structural equation model), "why" (else: why not), "info" (the second line of the node),
    "statements" ([text] of a construct), "parts" (subscales)}."""
    from app import sem_model, study_plan

    level = study_plan._level(thing)
    var = sem_model.variable(survey, code)
    rand = isinstance(thing, dict)
    if rand:
        name = thing.get("name") or code
        question = ""
    else:
        name = study_plan.construct_name(thing) or _short(sem_model._question(thing), 40) or code
        question = sem_model._question(thing)
    out = {"code": code, "name": name, "question": question, "level": level, "rand": rand,
           "groups": study_plan._groups(thing) if level == "nominal" else 0, "sem": "error" not in var,
           "why": "", "shape": "latent" if var.get("kind") == "latent" else "box", "statements": [], "parts": 1}
    if "error" in var:
        de, en = sem_model.ERRORS.get(var["error"], sem_model.ERRORS["level"])
        out["why"] = de_en(de, en)
    if out["shape"] == "latent":
        parts = var["parts"]
        out["parts"] = len(parts)
        out["statements"] = [_short(text, 90) for _, _, items in parts for _, text, _ in items][:40]
        k = sum(len(items) for _, _, items in parts)
        out["info"] = (de_en("{p} Subskalen · {k} Aussagen", "{p} subscales · {k} statements", p=len(parts), k=k)
                       if len(parts) > 1 else de_en("{k} Aussagen", "{k} statements", k=k))
    elif rand:
        out["info"] = de_en("Randomisierung · {k} Bedingungen", "Randomisation · {k} conditions", k=out["groups"])
    elif level == "nominal":
        out["info"] = de_en("{k} Gruppen", "{k} groups", k=out["groups"])
    elif level == "ordinal":
        out["info"] = de_en("ordinal", "ordinal")
    else:
        out["info"] = de_en("metrisch", "metric")
    return out


def variables(survey):
    """The variables a hypothesis can name and the drawing can place: the elements and randomisations of
    study_plan.measures with a level of measurement (open texts and the like stay out)."""
    from app import study_plan

    out = []
    for code, _ in study_plan.measures(survey):
        thing = study_plan._item(survey, code)
        if thing is None or study_plan._level(thing) is None:
            continue
        out.append(_info(survey, code, thing))
    return out


def status(survey):
    """The line under the drawing: {"tested" (hypotheses in the model), "q" (free parameters), "df", "need" (complete
    participations the estimate asks for), "error" (why the model cannot be estimated, in words) or None}; None
    when no hypothesis uses the model."""
    from app import sem, sem_model

    if not sem_model.in_use(survey):
        return None
    try:
        model = sem_model.build(survey)
    except Exception:  # a plan the model cannot read: the line says nothing then
        return None
    if model is None:
        return None
    out = {"tested": sum(1 for e in (model.get("hyps") or {}).values() if not e.get("errors")),
           "q": None, "df": None, "need": None, "error": None, "pls": sem_model.method_of(survey) == "pls"}
    if model.get("error"):
        out["error"] = sem_model.error_text({"error": model["error"]})
        return out
    spec = model["spec"]
    try:
        out["q"] = sem_model.free_parameters(spec)
        out["df"] = sem.model_df(spec)
    except (sem.SemError, ValueError, ArithmeticError):
        return out
    if out["pls"]:
        arrows = max([sum(1 for _, b in spec["paths"] if b == v) for v in model["nodes"]] or [0])
        out["need"] = max(sem_model.MIN_N, sem_model.PLS_TIMES * arrows)
    else:
        out["need"] = max(sem_model.MIN_N, sem_model.PER_PARAMETER * out["q"])
    if out["df"] < 0 and not out["pls"]:
        out["error"] = de_en("Das Modell hat mehr freie Parameter als die Daten Werte liefern (df < 0): nicht "
                             "identifiziert. Weniger Pfade oder Konstrukte mit mehr Aussagen.",
                             "The model has more free parameters than the data give values (df < 0): not identified. "
                             "Fewer paths or constructs with more statements.")
    return out


def data(survey, editable):
    """What static/model.js reads from the page (script data-model-data)."""
    from app import sem_model, study_plan

    # lists of pairs, not objects: the page's JSON sorts the keys of an object, the choices keep their order
    return {"vars": variables(survey),
            "dirs": {kind: list(labels.items()) for kind, labels in study_plan.directions_all().items()},
            "analyses": [[k, v] for k, v in study_plan.analyses(survey).items() if k],
            "max": study_plan.MAX_HYPOTHESES, "editable": bool(editable), "file": f"{survey.code}-modell",
            # PLS takes a moderator with two groups as an interaction term too (sem_model._moderation)
            "pls": sem_model.method_of(survey) == "pls"}
