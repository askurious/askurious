"""The scales of a diary study measured in two levels (longitudinal and multilevel concept 2026-10-17, E9): per scale of
a module (a Likert matrix with at least MIN_ITEMS scored statements) a two-level confirmatory factor analysis, one
factor within persons over their entries and one between persons (app/sem_twolevel.py), with the reliability on each
level (omega within and between, Geldhof et al., 2014): whether the scale tells moments of a person apart, and
whether it tells persons apart. From MIN_PERSONS persons (set by Askurious, E10). Kept with the course of the study
(surveys/field._diary_scales)."""
import numpy as np

from app import sem, sem_table, sem_twolevel

MIN_ITEMS = 3
MIN_PERSONS = 50
r_ = sem_table.row


def tables(cols):
    """One factor per level, loadings free on each level, the means between persons."""
    within = ([r_("=~", "FW", c, 1 if k == 0 else None) for k, c in enumerate(cols)]
              + [r_("~~", c, c) for c in cols] + [r_("~~", "FW", "FW")])
    between = ([r_("=~", "FB", c, 1 if k == 0 else None) for k, c in enumerate(cols)]
               + [r_("~~", c, c) for c in cols] + [r_("~1", c) for c in cols] + [r_("~~", "FB", "FB")])
    return within, between


def scales(survey, rows):
    """[{"code", "label", "module", "persons", "entries", "fit", "omega_within", "omega_between", "error"}]."""
    from app import diary, scales as scales_mod

    keys = {m["key"] for m in diary.modules(survey)}
    out = []
    for block in survey.blocks:
        part = diary.part_of(block)
        if part not in keys:
            continue
        for item in block.items:
            for sub, codes in scales_mod.groups(item):
                if len(codes) < MIN_ITEMS:
                    continue
                hidden = scales_mod._missing(item, codes)
                ys, persons = [], []
                for r in rows:
                    if r.get("teil") != part or r.get("person") in (None, ""):
                        continue
                    vals = scales_mod.values(item, codes, r, hidden=hidden)
                    if any(v is None for v in vals):
                        continue
                    ys.append(vals)
                    persons.append(str(r["person"]))
                entry = {"code": f"{item.code}:{sub}" if sub else item.code, "label": item.code if not sub else f"{item.code} · {sub}",
                         "module": diary.part_label(part, survey), "persons": len(set(persons)), "entries": len(ys)}
                if entry["persons"] < MIN_PERSONS:
                    entry["error"] = "few"
                    out.append(entry)
                    continue
                cols = [f"s{k + 1}" for k in range(len(codes))]
                within, between = tables(cols)
                try:
                    fit = sem_twolevel.fit(np.array(ys, dtype=float), np.array(persons), cols, within, between)
                except (sem.SemError, np.linalg.LinAlgError, ValueError, ArithmeticError):
                    fit = {"converged": False}
                if not fit.get("converged"):
                    entry["error"] = "estimate"
                    out.append(entry)
                    continue
                entry["fit"] = {k: fit.get(k) for k in ("chi2", "df", "p", "cfi", "rmsea", "srmr_within", "srmr_between",
                                                        "admissible", "problems")}
                entry["omega_within"] = sem_twolevel.omega(fit, "within", "FW", cols)
                entry["omega_between"] = sem_twolevel.omega(fit, "between", "FB", cols)
                out.append(entry)
    return out
