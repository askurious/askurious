"""Interviews kodieren, phase 3: double coding with agreement (app/transcript_coding.py; concept "Interviews kodieren",
phase 3, Ralf 2026-10-27).

The team assigns an interview of an analysis to two or more coders (CodingProject.settings["double"][interview id] =
{"coders": [actor keys], "names": {key: name}, "done": [keys]}); the coders see the assignment in the list of
interviews. Blind: until every assigned coder has reported "Kodierung abgeschlossen", each of them sees only their own
passages in that interview, no counts of the others and no mutual check from it (`blind_for`); the team sees all.

Then the comparison (`differences`): the passages of two coders side by side, matched when they carry the same category
and overlap by at least the minimum (intersection over union, by default 90 %). Unmatched ones are "nur A", "nur B" or,
where a passage of the other coder lies on the same place with another category, "andere Kategorie". Per difference the
team or a coder of the interview decides "A übernehmen", "B übernehmen", "beide behalten" or "keine" and may comment;
the consensual coding is built from the matches and these decisions (Response.interview["consensus"] holds only the
decisions), the single codings stay as they are.

Agreement (`agreement`), per category and overall (all categories pooled): on word level every word of the transcript is
coded or not coded with the category by each coder, which gives a 2 × 2 table, its share of agreement p_o and Cohen's
kappa κ = (p_o − p_e) / (1 − p_e) with p_e from the marginal shares (Cohen, 1960); read after Landis & Koch (1977). On
passage level the share of passages of both coders that find a match. A figure is shown only with two coders and at
least MIN_PASSAGES passages in that category; κ not where p_e = 1 (nobody or everybody coded every word). The procedure of
coding by two people and discussing the differences follows Kuckartz & Rädiker (2022)."""
import csv
import io
import re

from flask import abort, flash, redirect, render_template, request, send_file, url_for

from app.extensions import db
from app.lang import de_en
from app.models import CodingMember, Response, utcnow
from app.transcript_coding import _in, bp, interviews, live, members, resolver, signed_in_or_coder

COHEN = ("Cohen, J. (1960). A coefficient of agreement for nominal scales. Educational and Psychological Measurement, "
         "20(1), 37–46. https://doi.org/10.1177/001316446002000104")
LANDIS_KOCH = ("Landis, J. R., & Koch, G. G. (1977). The measurement of observer agreement for categorical data. "
               "Biometrics, 33(1), 159–174. https://doi.org/10.2307/2529310")
KUCKARTZ = ("Kuckartz, U., & Rädiker, S. (2022). Qualitative Inhaltsanalyse: Methoden, Praxis, Computerunterstützung "
            "(5. Aufl.). Beltz Juventa.")
OVERLAP = 0.9
MIN_PASSAGES = 3
WORD = re.compile(r"\w+", re.UNICODE)
CHOICES = ("a", "b", "both", "none")


# --- the assignment ---------------------------------------------------------------------------------------------------


def _double(project):
    return dict(((project.settings or {}).get("double") or {}))


def assignment(project, rid):
    found = _double(project).get(str(rid))
    return found if isinstance(found, dict) and len(found.get("coders") or []) >= 2 else None


def finished(entry):
    return entry is not None and set(entry.get("coders") or []) <= set(entry.get("done") or [])


def blind_for(project, who, rid):
    """Whether `who` sees only their own passages in this interview: an assigned coder before everyone is done."""
    entry = assignment(project, rid)
    return entry is not None and not who.team and who.key in entry["coders"] and not finished(entry)


def visible(project, who, row, items):
    """The codings of this interview the actor may see now."""
    if blind_for(project, who, row.id):
        return [c for c in items if c.get("by") == who.key]
    return items


def assignable(project, survey):
    """[(key, name)] of the people who can be assigned: the team (Bearbeiten, Admin), members as persons and through
    their groups, coding links, interviewers with the tick."""
    from app.models import CodingLink, InterviewerAccess, User, WorkGroupMember

    out = {}
    for m in survey.members:
        if m.user_id and m.role != "viewer" and m.user is not None:
            out[f"u:{m.user_id}"] = m.display_name
    for row in members(project):
        if row.kind == CodingMember.PERSON and row.user_id:
            user = db.session.get(User, row.user_id)
            if user is not None:
                out.setdefault(f"u:{user.id}", user.display_name)
        elif row.kind == CodingMember.GROUP and row.group_id:
            for m in db.session.scalars(db.select(WorkGroupMember).filter_by(group_id=row.group_id)):
                if m.user_id:
                    out.setdefault(f"u:{m.user_id}", m.display_name)
        elif row.kind == CodingMember.INTERVIEWER and row.access_id:
            access = db.session.get(InterviewerAccess, row.access_id)
            if access is not None and access.may_code != InterviewerAccess.CODE_NONE:
                out.setdefault(f"i:{access.id}", access.name)
    from app import interviewer, transcript_coding

    if transcript_coding.coder_mode(project) == transcript_coding.CODERS_ALL:
        for access in interviewer.accesses(survey):
            if transcript_coding.may_code(access):
                out.setdefault(f"i:{access.id}", access.name)
    for link in db.session.scalars(db.select(CodingLink).filter_by(project_id=project.id)):
        out.setdefault(f"l:{link.id}", link.name)
    return sorted(out.items(), key=lambda kv: kv[1].casefold())


def assign(project, survey, rid, keys):
    """Assign the interview to these coders (two or more), or take the assignment away (fewer)."""
    known = dict(assignable(project, survey))
    keys = [k for k in dict.fromkeys(keys) if k in known]
    double = _double(project)
    old = double.get(str(rid)) or {}
    if len(keys) >= 2:
        double[str(rid)] = {"coders": keys, "names": {k: known[k] for k in keys},
                            "done": [k for k in (old.get("done") or []) if k in keys]}
    else:
        double.pop(str(rid), None)
    project.settings = {**(project.settings or {}), "double": double}
    project.updated_at = utcnow()


def report_done(project, who, rid, done=True):
    entry = assignment(project, rid)
    if entry is None or who.key not in entry["coders"]:
        abort(404)
    marks = set(entry.get("done") or [])
    (marks.add if done else marks.discard)(who.key)
    double = _double(project)
    double[str(rid)] = {**entry, "done": sorted(marks)}
    project.settings = {**(project.settings or {}), "double": double}


# --- comparing two coders -------------------------------------------------------------------------------------------


def _overlap(x, y):
    """Intersection over union of two passages."""
    inter = min(x["end"], y["end"]) - max(x["start"], y["start"])
    union = max(x["end"], y["end"]) - min(x["start"], y["start"])
    return max(0, inter) / union if union > 0 else 0.0


def coder_codings(row, project, key):
    """The placed codings of one coder in this interview, with the category they count for now."""
    from app.transcript_coding_view import placed

    ok, _lost = placed(row, project)
    return [c for c in ok if c.get("by") == key]


def differences(a_items, b_items, threshold=OVERLAP):
    """[{"id", "kind": "match"|"only_a"|"only_b"|"other", "a": coding or None, "b": coding or None}]: each passage of
    A paired with the best passage of B of the same category (overlap ≥ threshold), each used once; the rest of A and B
    with "other" where a passage of the other coder overlaps it (another category or other borders), else "only"."""
    out, used_b = [], set()
    for a in sorted(a_items, key=lambda c: (c["start"], c["end"])):
        best, score = None, 0.0
        for b in b_items:
            if b["id"] in used_b or b["cat"] != a["cat"]:
                continue
            s = _overlap(a, b)
            if s >= threshold and s > score:
                best, score = b, s
        if best is not None:
            used_b.add(best["id"])
            out.append({"id": f"{a['id']}|{best['id']}", "kind": "match", "a": a, "b": best})
    matched_a = {d["a"]["id"] for d in out}
    rest_a = [a for a in a_items if a["id"] not in matched_a]
    rest_b = [b for b in b_items if b["id"] not in used_b]
    paired_b = set()
    for a in rest_a:
        other = next((b for b in rest_b if b["id"] not in paired_b and _overlap(a, b) > 0), None)
        if other is not None:
            paired_b.add(other["id"])
            out.append({"id": f"{a['id']}|{other['id']}", "kind": "other", "a": a, "b": other})
        else:
            out.append({"id": f"{a['id']}|", "kind": "only_a", "a": a, "b": None})
    for b in rest_b:
        if b["id"] not in paired_b:
            out.append({"id": f"|{b['id']}", "kind": "only_b", "a": None, "b": b})
    return sorted(out, key=lambda d: ((d["a"] or d["b"])["start"], (d["a"] or d["b"])["end"]))


def decisions(row, project):
    return {d.get("diff"): d for d in ((row.interview or {}).get("consensus") or [])
            if isinstance(d, dict) and d.get("project") == project.id}


def consensus(row, project, diffs):
    """The consensual coding: the matched passages (A's) and what the decisions take."""
    chosen = decisions(row, project)
    out = []
    for d in diffs:
        choice = "a" if d["kind"] == "match" else (chosen.get(d["id"]) or {}).get("choice")
        if choice in ("a", "both") and d["a"]:
            out.append(d["a"])
        if choice in ("b", "both") and d["b"]:
            out.append(d["b"])
    return out


def decide(row, project, who, diff_id, choice, comment=""):
    from app import interview_audio

    if choice not in CHOICES:
        abort(400)
    entry = {"project": project.id, "diff": diff_id, "choice": choice, "comment": " ".join(str(comment or "").split())[:1000],
             "by": who.key, "by_name": who.name[:200], "at": utcnow().isoformat(timespec="seconds")}

    def work():
        db.session.refresh(row)
        meta = dict(row.interview or {})
        meta["consensus"] = [d for d in (meta.get("consensus") or []) if not (isinstance(d, dict) and d.get("project") == project.id
                                                                            and d.get("diff") == diff_id)] + [entry]
        row.interview = meta

    interview_audio.retried(work)


# --- agreement ------------------------------------------------------------------------------------------------------


def table_kappa(a, b, c, d):
    """(p_o, kappa) of a 2 × 2 table: a both coded, b only the first, c only the second, d neither (Cohen, 1960).
    kappa is None where p_e = 1 (no variation); p_o None for an empty table."""
    n = a + b + c + d
    if n == 0:
        return None, None
    p_o = (a + d) / n
    p_e = ((a + b) * (a + c) + (c + d) * (b + d)) / (n * n)
    if p_e >= 1:
        return p_o, None
    return p_o, (p_o - p_e) / (1 - p_e)


def word_table(text, a_items, b_items, cat):
    """The 2 × 2 table of the words of `text` for one category."""
    words = [(m.start(), m.end()) for m in WORD.finditer(text)]

    def marked(items):
        spans = [(c["start"], c["end"]) for c in items if c["cat"] == cat]
        return [any(s < we and e > ws for s, e in spans) for ws, we in words]

    ma, mb = marked(a_items), marked(b_items)
    both = sum(1 for x, y in zip(ma, mb) if x and y)
    only_a = sum(1 for x, y in zip(ma, mb) if x and not y)
    only_b = sum(1 for x, y in zip(ma, mb) if y and not x)
    return [both, only_a, only_b, len(words) - both - only_a - only_b]


def passage_agreement(a_items, b_items, cat=None, threshold=OVERLAP):
    """(matched passages of both coders, all passages of both) for one category or all."""
    a = [c for c in a_items if cat is None or c["cat"] == cat]
    b = [c for c in b_items if cat is None or c["cat"] == cat]
    diffs = differences(a, b, threshold)
    matched = sum(2 for d in diffs if d["kind"] == "match")
    return matched, len(a) + len(b)


def agreement(project, rows, threshold=OVERLAP):
    """Per category id and "all": {"words": [a, b, c, d], "p_o", "kappa", "passages": (matched, total), "share",
    "enough"} summed over the double-coded, finished interviews (first two coders of each)."""
    from app.transcript_coding import subtree

    cats = [c.id for c in live(project)]
    groups = {}  # a category with something below it: its whole subtree as one (Ralf 2026-10-28)
    for c in live(project):
        tree_ids = subtree(project, c)
        if len(tree_ids) > 1:
            groups[f"sum:{c.id}"] = tree_ids
    sums = {cid: {"words": [0, 0, 0, 0], "passages": [0, 0]} for cid in cats + ["all"] + list(groups)}
    pairs = 0
    for row in rows:
        entry = assignment(project, row.id)
        if not finished(entry):
            continue
        a_key, b_key = entry["coders"][:2]
        a_items, b_items = coder_codings(row, project, a_key), coder_codings(row, project, b_key)
        text = ((row.interview or {}).get("transcript") or {}).get("text") or ""
        pairs += 1
        for cid in cats:
            t = word_table(text, a_items, b_items, cid)
            m, n = passage_agreement(a_items, b_items, cid, threshold)
            for key in (cid, "all"):
                sums[key]["words"] = [x + y for x, y in zip(sums[key]["words"], t)]
                sums[key]["passages"] = [sums[key]["passages"][0] + m, sums[key]["passages"][1] + n]
        for key, ids in groups.items():  # the codings of the subtree as one category
            a_sum = [{**c, "cat": key} for c in a_items if c["cat"] in ids]
            b_sum = [{**c, "cat": key} for c in b_items if c["cat"] in ids]
            t = word_table(text, a_sum, b_sum, key)
            m, n = passage_agreement(a_sum, b_sum, key, threshold)
            sums[key]["words"] = [x + y for x, y in zip(sums[key]["words"], t)]
            sums[key]["passages"] = [sums[key]["passages"][0] + m, sums[key]["passages"][1] + n]
    out = {}
    for key, s in sums.items():
        p_o, kappa = table_kappa(*s["words"])
        matched, total = s["passages"]
        out[key] = {"words": s["words"], "p_o": p_o, "kappa": kappa, "passages": (matched, total),
                    "share": matched / total if total else None, "enough": pairs > 0 and total >= MIN_PASSAGES}
    out["_pairs"] = pairs
    return out


def reading(kappa):
    """The words of Landis & Koch (1977) for a kappa."""
    if kappa is None:
        return ""
    if kappa < 0:
        return de_en("schlechter als Zufall", "poor")
    for limit, de, en in ((0.2, "sehr gering", "slight"), (0.4, "gering", "fair"), (0.6, "mittelmäßig", "moderate"),
                          (0.8, "beträchtlich", "substantial")):
        if kappa <= limit:
            return de_en(de, en)
    return de_en("fast vollkommen", "almost perfect")


# --- the pages ------------------------------------------------------------------------------------------------------


def _threshold():
    try:
        value = float(request.args.get("min", OVERLAP * 100)) / 100
    except (TypeError, ValueError):
        value = OVERLAP
    return min(1.0, max(0.1, value))


@bp.route("/<code>/interviews/coding/<int:pid>/i/<int:rid>/double", methods=["POST"])
@signed_in_or_coder
def assign_coders(code, pid, rid):
    survey, project, who = _in(code, pid, team_only=True)
    row = db.session.get(Response, rid)
    if row is None or row.survey_id != survey.id:
        abort(404)
    assign(project, survey, rid, request.form.getlist("coder"))
    db.session.commit()
    flash(de_en("Doppelkodierung gespeichert.", "Double coding saved."), "success")
    return redirect(url_for("transcript_coding.project", code=survey.code, pid=project.id) + "#interviews")


@bp.route("/<code>/interviews/coding/<int:pid>/i/<int:rid>/done", methods=["POST"])
@signed_in_or_coder
def coding_done(code, pid, rid):
    survey, project, who = _in(code, pid)
    report_done(project, who, rid, done=request.form.get("undo") != "1")
    db.session.commit()
    flash(de_en("Gemeldet: Kodierung abgeschlossen.", "Reported: coding finished.") if request.form.get("undo") != "1"
          else de_en("Wieder offen.", "Open again."), "success")
    return redirect(url_for("transcript_coding.page", code=survey.code, pid=project.id, rid=rid))


def _comparable(survey, project, who, rid):
    from app.transcript_coding_view import _row

    row = _row(project, who, rid)
    entry = assignment(project, rid)
    if entry is None or not (who.team or who.key in entry["coders"]) or not (who.team or finished(entry)):
        abort(404)
    return row, entry


@bp.route("/<code>/interviews/coding/<int:pid>/i/<int:rid>/compare")
@signed_in_or_coder
def compare(code, pid, rid):
    survey, project, who = _in(code, pid)
    row, entry = _comparable(survey, project, who, rid)
    keys = entry["coders"]
    a_key = request.args.get("a") if request.args.get("a") in keys else keys[0]
    b_key = request.args.get("b") if request.args.get("b") in keys and request.args.get("b") != a_key else next(k for k in keys if k != a_key)
    threshold = _threshold()
    a_items, b_items = coder_codings(row, project, a_key), coder_codings(row, project, b_key)
    diffs = differences(a_items, b_items, threshold)
    names = {c.id: c.name for c in live(project)}
    text = ((row.interview or {}).get("transcript") or {}).get("text") or ""
    figures = {}
    for cid in names:
        t = word_table(text, a_items, b_items, cid)
        p_o, kappa = table_kappa(*t)
        figures[cid] = {"p_o": p_o, "kappa": kappa, "passages": passage_agreement(a_items, b_items, cid, threshold)}
    return render_template("transcript_coding/compare.html", survey=survey, project=project, who=who, row=row, entry=entry,
                           a_key=a_key, b_key=b_key, diffs=diffs, decisions=decisions(row, project), names=names,
                           figures=figures, finished=finished(entry), threshold=round(threshold * 100),
                           consensus=consensus(row, project, diffs), reading=reading, min_passages=MIN_PASSAGES,
                           sources=[(de_en("Übereinstimmung", "Agreement"), [COHEN, LANDIS_KOCH, KUCKARTZ])])


@bp.route("/<code>/interviews/coding/<int:pid>/i/<int:rid>/compare", methods=["POST"])
@signed_in_or_coder
def compare_decide(code, pid, rid):
    survey, project, who = _in(code, pid)
    row, entry = _comparable(survey, project, who, rid)
    decide(row, project, who, request.form.get("diff", "")[:40], request.form.get("choice"), request.form.get("comment", ""))
    return redirect(url_for("transcript_coding.compare", code=survey.code, pid=project.id, rid=rid,
                            a=request.form.get("a"), b=request.form.get("b")) + "#d-" + request.form.get("anchor", ""))


@bp.route("/<code>/interviews/coding/<int:pid>/agreement")
@signed_in_or_coder
def agreement_page(code, pid):
    survey, project, who = _in(code, pid)
    rows = [r for r in interviews(project, who)]
    threshold = _threshold()
    figures = agreement(project, rows, threshold)
    cats = live(project)
    if request.args.get("format") == "csv":
        from app.export import safe_text

        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["kategorie", "woerter_beide", "woerter_nur_a", "woerter_nur_b", "woerter_keiner", "p_o", "kappa",
                    "stellen_passend", "stellen_gesamt", "anteil_stellen", "mindestueberlappung"])
        for key, label in [(c.id, c.name) for c in cats] + [("all", de_en("alle Kategorien", "all categories"))]:
            f = figures[key]
            w.writerow([safe_text(label)] + f["words"] + [_num(f["p_o"]), _num(f["kappa"]), f["passages"][0], f["passages"][1],
                                                         _num(f["share"]), round(threshold * 100)])
        return send_file(io.BytesIO(("﻿" + buf.getvalue()).encode("utf-8")), as_attachment=True, mimetype="text/csv",
                         download_name=f"uebereinstimmung-{survey.code}-{project.id}.csv")
    double = [(r, assignment(project, r.id)) for r in rows if assignment(project, r.id)]
    return render_template("transcript_coding/agreement.html", survey=survey, project=project, who=who, cats=cats,
                           figures=figures, double=double, finished=finished, threshold=round(threshold * 100),
                           reading=reading, min_passages=MIN_PASSAGES,
                           sources=[(de_en("Übereinstimmung", "Agreement"), [COHEN, LANDIS_KOCH, KUCKARTZ])])


def _num(value):
    return "" if value is None else f"{value:.3f}"

