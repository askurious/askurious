"""The second coder of the page "Kategorisieren" (concept offene-antworten-kodieren-konzept.md, step 5; K5 Krippendorff's
alpha, K6 20 % of the answers, at least 50; Ralf 2026-10-10: stage 2).

A person of the team with the role "Bearbeiten" (not the one who starts it) codes a random sample of the answers
blind: on their page they see only the texts of the sample, never the first coding, and while it is open the page
"Kategorisieren" and the categories of that column on the results page are closed to them. A unit is one answer text
(the page codes per text, so does the second coder). The sample is kept as the units of each text
(survey.codings[key]["second"]["ids"]: per text the participations by their pid and the unit, never a text): a deleted
participation takes its unit along, the text stays in the sample as long as another participation gives it (audit
2026-10-10, B-14; the pid, not the row number, which SQLite may give again: L-13). Only participations of the source
"real" are drawn (B-5). The second coder's categories are in response.coding["<key>~2"].

When the second coder finishes, the first coding of the sample is frozen ("<key>~1") and compared with theirs:
percentage agreement and Krippendorff's alpha (nominal; with several categories per answer one alpha per category,
each a yes/no, and one over all of them) with a 95 % bootstrap interval over the answers. The disagreements are listed
on the page "Kategorisieren" to clarify; the clarified version is the first coding, and alpha stays the one of the
comparison (Krippendorff, 2019, ch. 12)."""
import math
import random
import zlib
from datetime import datetime, timezone

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import coding, experimental
from app.coding import bp
from app.extensions import db
from app.lang import de_en
from app.models import Response

SHARE, MINIMUM = 0.2, 50  # K6
DRAWS = 1000  # bootstrap draws for the interval of alpha
KRIPPENDORFF = "Krippendorff, K. (2019). Content analysis: An introduction to its methodology (4. Aufl.). SAGE."
HAYES = ("Hayes, A. F., & Krippendorff, K. (2007). Answering the call for a standard reliability measure for coding "
         "data. Communication Methods and Measures, 1(1), 77–89. https://doi.org/10.1080/19312450709336664")


# --- the measure -------------------------------------------------------------------------------------------------

def alpha_nominal(pairs):
    """Krippendorff's alpha for two coders and nominal values without missing ones: pairs = [(value of coder 1,
    value of coder 2)] per unit. From the coincidence matrix: alpha = 1 - (n - 1) * D_o / D_e with n = 2 * units,
    D_o the pairable values that differ and D_e the products of the margins of different values. None when it is
    not defined (under two units, or every value the same)."""
    if len(pairs) < 2:
        return None
    margins = {}
    for a, b in pairs:
        margins[a] = margins.get(a, 0) + 1
        margins[b] = margins.get(b, 0) + 1
    n = 2 * len(pairs)
    observed = sum(2 for a, b in pairs if a != b)  # o_ab + o_ba
    expected = n * n - sum(m * m for m in margins.values())  # sum over c != k of n_c * n_k
    if not expected:
        return None
    return 1 - (n - 1) * observed / expected


def _measures(units, cats, single):
    """{"alpha", "per_cat": {id: alpha}, "agree"} of units [(first, second)] (sets of category ids)."""
    if not units:
        return {"alpha": None, "per_cat": {}, "agree": None}
    agree = sum(1 for a, b in units if a == b) / len(units)
    if single:
        pairs = [(min(a) if a else 0, min(b) if b else 0) for a, b in units]
        return {"alpha": alpha_nominal(pairs), "per_cat": {}, "agree": agree}
    per = {c: alpha_nominal([(c in a, c in b) for a, b in units]) for c in cats}
    pooled = alpha_nominal([(c in a, c in b) for a, b in units for c in cats])
    return {"alpha": pooled, "per_cat": per, "agree": agree}


def _interval(units, cats, single, seed):
    """95 % percentile bootstrap interval of the (pooled) alpha, drawing answers with replacement; None below ten."""
    if len(units) < 10:
        return None
    rng = random.Random(seed)
    found = []
    for _ in range(DRAWS):
        draw = [units[rng.randrange(len(units))] for _ in units]
        value = _measures(draw, cats, single)["alpha"]
        if value is not None:
            found.append(value)
    if len(found) < DRAWS / 2:
        return None
    found.sort()
    return found[int(0.025 * len(found))], found[min(len(found) - 1, int(math.ceil(0.975 * len(found))) - 1)]


def _signature(pairs, cats, single):
    """What the interval of alpha rests on, in the order drawn (the bootstrap draws by position)."""
    data = [[sorted(a), sorted(b)] for a, b in pairs]
    return zlib.crc32(repr((data, list(cats), bool(single))).encode())


def _kept_interval(second, key, pairs, cats, single):
    """The interval of alpha: the one kept with the second coding when it was computed for these very pairs, else
    computed now. It is deterministic (a fixed seed), and its 1000 draws took up to a second and more on every view
    of the page, the coding guide and the methods section (performance audit 2026-10-10, P4)."""
    kept = second.get("interval") if isinstance(second, dict) else None
    sig = _signature(pairs, cats, single)
    if isinstance(kept, dict) and kept.get("sig") == sig:
        ci = kept.get("ci")
        return tuple(ci) if isinstance(ci, list) and len(ci) == 2 else None
    return _interval(pairs, cats, single, seed=zlib.crc32(key.encode()))


def _interval_entry(pairs, cats, single, key):
    """{"sig", "ci"} as survey.codings[key]["second"]["interval"] keeps it."""
    ci = _interval(pairs, cats, single, seed=zlib.crc32(key.encode()))
    return {"sig": _signature(pairs, cats, single), "ci": list(ci) if ci else None}


# --- the sample --------------------------------------------------------------------------------------------------

def state(survey, key):
    """survey.codings[key]["second"] or None: {"by", "name", "ids", "at", "done"}."""
    found = coding.scheme(survey, key)
    second = (found or {}).get("second")
    return second if isinstance(second, dict) and second.get("ids") is not None else None


def blind_for(survey, key, user):
    """True while `user` is the second coder of `key` and has not finished: the first coding stays out of sight."""
    second = state(survey, key)
    return bool(second and not second.get("done") and getattr(user, "id", None) == second.get("by"))


def sample_size(units):
    return min(units, max(MINIMUM, math.ceil(SHARE * units)))


def candidates(survey, user):
    """[(user id, name)] of the members who may be the second coder: role "Bearbeiten" or more, not `user`."""
    out = []
    for m in survey.members:
        if m.role in ("editor", "admin") and m.account is not None and m.account.id != getattr(user, "id", None):
            out.append((m.account.id, m.account.name or m.name or "?"))
    return out


def _clear(survey, key):
    """Takes the second coder's categories and the frozen first coding of `key` out of every participation."""
    for r in db.session.scalars(db.select(Response).filter(Response.survey_id == survey.id)):
        if r.coding and any(k.endswith((coding.SECOND, coding.FROZEN)) for k in r.coding):
            subs = coding.subkeys(r.coding, key)
            kept = {k: v for k, v in r.coding.items()
                    if not (coding.base_of(k) in subs and k.endswith((coding.SECOND, coding.FROZEN)))}
            if kept != r.coding:
                r.coding = kept or None


def start(survey, item, column, key, coder_id, user):
    found = coding.scheme(survey, key)
    if found is None:
        return de_en("Legen Sie zuerst Kategorien an.", "Create categories first.")
    names = dict(candidates(survey, user))
    if coder_id not in names:
        return de_en("Wählen Sie eine andere beteiligte Person mit der Rolle „Bearbeiten“.",
                     "Choose another person involved with the role “Edit”.")
    rows = coding.answers(survey, item, column, key, real=True)
    if len(rows) < 2:
        return de_en("Dafür braucht es mindestens zwei verschiedene Antworten.", "This needs at least two different answers.")
    picked = random.Random().sample(rows, sample_size(len(rows)))
    _clear(survey, key)
    coding._store(survey, key, {**found, "second": {
        "by": coder_id, "name": names[coder_id], "ids": [[list(p) for p in row["pids"]] for row in picked],
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "done": False}})
    return None


def stop(survey, key):
    """Ends the second coding without a result: the sample and the second coder's categories go."""
    found = coding.scheme(survey, key)
    if not found:
        return
    _clear(survey, key)
    coding._store(survey, key, {k: v for k, v in found.items() if k != "second"})


def _picked(second, key):
    """[[(participation, sub key)]] of the sample, one list per text: the participation by its pid (a str) or, in
    a sample of an earlier version, by its row number (an int); a plain number (before units) is the key."""
    out = []
    for entry in second.get("ids") or []:
        if isinstance(entry, int):
            out.append([(entry, key)])
        elif isinstance(entry, (list, tuple)) and len(entry) == 2 and isinstance(entry[0], int):
            out.append([(entry[0], str(entry[1]))])
        elif isinstance(entry, (list, tuple)):
            out.append([(u[0], str(u[1])) for u in entry if isinstance(u, (list, tuple)) and len(u) == 2
                        and isinstance(u[0], (int, str))])
    return out


def _sample(survey, item, column, key):
    """[(first unit, [(participation, sub key) of every unit of the text that still has it])] of the sample, in the
    order drawn; the participations only of this survey (L-13)."""
    second = state(survey, key)
    if not second:
        return []
    picked = _picked(second, key)
    refs = {ref for group in picked for ref, _ in group}
    found = db.session.scalars(db.select(Response).filter(
        Response.survey_id == survey.id,
        db.or_(Response.id.in_({r for r in refs if isinstance(r, int)}),
               Response.pid.in_({r for r in refs if isinstance(r, str)})))).all()
    by_id, by_pid = {r.id: r for r in found}, {r.pid: r for r in found if r.pid}
    uplan = coding.unit_plan(item, column, key)
    out = []
    for group in picked:
        held = []
        for ref, sub in group:
            r = by_pid.get(ref) if isinstance(ref, str) else by_id.get(ref)
            text = dict(coding.units(item, uplan, r)).get(sub, "") if r is not None else ""
            if text:
                held.append((r, sub, text))
        if not held:
            continue
        r, sub, text = held[0]
        c = r.coding or {}
        out.append(({"id": r.id, "sub": sub, "norm": coding.norm(text), "text": text, "first": c.get(sub),
                     "second": c.get(sub + coding.SECOND), "frozen": c.get(sub + coding.FROZEN)},
                    [(r2, sub2) for r2, sub2, _ in held]))
    return out


def units_of(survey, item, column, key):
    """[{"id", "sub", "norm", "text", "first", "second", "frozen"}] of the sample that still exists, in the order
    drawn (a unit: an answer, a word of an association task, a pass of a loop): per text the first participation
    that still gives it."""
    return [u for u, _ in _sample(survey, item, column, key)]


def finish(survey, item, column, key):
    """The second coder is done: every unit must have their categories; the first coding of the sample is frozen."""
    second = state(survey, key)
    sample = _sample(survey, item, column, key)
    if any(u["second"] is None for u, _ in sample):
        return de_en("Noch nicht alle Antworten der Stichprobe haben eine Kategorie.",
                     "Not every answer of the sample has a category yet.")
    for _, held in sample:  # every unit of the text, so it is still compared when the first participation goes
        for r, sub in held:
            c = dict(r.coding or {})
            if c.get(sub) is not None:
                c[sub + coding.FROZEN] = list(c[sub])
                r.coding = c
    found = coding.scheme(survey, key)
    # the interval of alpha, computed once here (P4): the frozen first coding against the second coder's
    pairs = [(frozenset(u["first"]), frozenset(u["second"])) for u, _ in sample if u["first"] is not None]
    cats = [c["id"] for c in found["cats"]]
    coding._store(survey, key, {**found, "second": {**second, "done": True,
                                                    "done_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                    "interval": _interval_entry(pairs, cats, found["single"], key)}})
    return None


def remember(survey, item, column, key):
    """After a change of the scheme that changed the compared coding (a category removed or merged: "rescored"),
    the interval of alpha is computed once more and kept (P4); nothing while no finished second coding exists."""
    second = state(survey, key)
    if not second or not second.get("done"):
        return
    found = coding.scheme(survey, key)
    cats = [c["id"] for c in found["cats"]]
    usable = [u for u in units_of(survey, item, column, key) if u["frozen"] is not None and u["second"] is not None]
    pairs = [(frozenset(u["frozen"]), frozenset(u["second"])) for u in usable]
    kept = second.get("interval")
    if isinstance(kept, dict) and kept.get("sig") == _signature(pairs, cats, found["single"]):
        return
    coding._store(survey, key, {**found, "second": {**second, "interval": _interval_entry(pairs, cats, found["single"], key)}})


def report(survey, item, column, key):
    """The comparison once the second coder has finished: {"n", "skipped", "agree", "alpha", "ci", "per_cat":
    [(name, alpha)], "differ": [unit], "by"}; None before."""
    second = state(survey, key)
    if not second or not second.get("done"):
        return None
    found = coding.scheme(survey, key)
    cats = [c["id"] for c in found["cats"]]
    names = {c["id"]: c["name"] for c in found["cats"]}
    units = units_of(survey, item, column, key)
    usable = [u for u in units if u["frozen"] is not None and u["second"] is not None]
    pairs = [(frozenset(u["frozen"]), frozenset(u["second"])) for u in usable]
    m = _measures(pairs, cats, found["single"])
    differ = [{**u, "frozen_names": [names.get(c, "?") for c in u["frozen"]],
               "second_names": [names.get(c, "?") for c in u["second"]],
               "clarified": u["first"] is not None and sorted(u["first"]) != sorted(u["frozen"])}
              for u in usable if set(u["frozen"]) != set(u["second"])]
    return {"n": len(usable), "skipped": len(units) - len(usable), "agree": m["agree"], "alpha": m["alpha"],
            "ci": _kept_interval(second, key, pairs, cats, found["single"]),
            "per_cat": [(names[c], m["per_cat"][c]) for c in cats if c in m["per_cat"]],
            "differ": differ, "by": second.get("name"), "single": found["single"],
            "rescored": bool(second.get("rescored"))}


# --- routes ------------------------------------------------------------------------------------------------------

def _open(survey_id, key):
    from app import surveys

    survey = surveys.get_survey(survey_id, need="editor")
    experimental.require("coding", survey)
    found = coding.find(survey, key)
    if found is None:
        abort(404)
    return survey, found


@bp.route("/<sid:survey_id>/categorise/<key>/second/start", methods=["POST"])
@login_required
def second_start(survey_id, key):
    survey, (item, column, _) = _open(survey_id, key)
    try:
        coder = int(request.form.get("coder", ""))
    except ValueError:
        coder = None
    problem = start(survey, item, column, key, coder, current_user)
    if problem:
        flash(problem, "error")
    else:
        db.session.commit()
        flash(de_en("Zweitkodierung angelegt. Die gewählte Person findet sie auf der Ergebnisseite unter „Kategorisieren“.",
                    "Second coding set up. The person chosen finds it on the results page under “Categorise”."), "success")
    return redirect(url_for("coding.page", survey_id=survey.id, key=key) + "#zweitkodierung")


@bp.route("/<sid:survey_id>/categorise/<key>/second/stop", methods=["POST"])
@login_required
def second_stop(survey_id, key):
    survey, _ = _open(survey_id, key)
    stop(survey, key)
    db.session.commit()
    flash(de_en("Zweitkodierung beendet.", "Second coding ended."), "success")
    return redirect(url_for("coding.page", survey_id=survey.id, key=key) + "#zweitkodierung")


@bp.route("/<sid:survey_id>/categorise/<key>/second")
@login_required
def second_page(survey_id, key):
    """The second coder's page: only the texts of the sample, none of the first coding."""
    survey, (item, column, label) = _open(survey_id, key)
    second = state(survey, key)
    if not second or second.get("by") != current_user.id or second.get("done"):
        return redirect(url_for("coding.page", survey_id=survey.id, key=key))
    found = coding.scheme(survey, key)
    units = units_of(survey, item, column, key)
    return render_template("surveys/coding_second.html", survey=survey, item=item, column=column, label=label,
                           key=key, scheme=found, units=units, done=sum(1 for u in units if u["second"] is not None))


@bp.route("/<sid:survey_id>/categorise/<key>/second/finish", methods=["POST"])
@login_required
def second_finish(survey_id, key):
    survey, (item, column, _) = _open(survey_id, key)
    second = state(survey, key)
    if not second or second.get("by") != current_user.id:
        abort(403)
    problem = finish(survey, item, column, key)
    if problem:
        flash(problem, "error")
        return redirect(url_for("coding.second_page", survey_id=survey.id, key=key))
    db.session.commit()
    flash(de_en("Zweitkodierung abgeschlossen. Danke!", "Second coding finished. Thank you!"), "success")
    return redirect(url_for("coding.page", survey_id=survey.id, key=key) + "#zweitkodierung")
