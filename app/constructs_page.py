"""Konstrukte: explore, search and insert validated instruments."""
import csv
import io

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, citations, constructs, linked
from app.extensions import db
from app.ids import db_id
from app.lang import de_en, english
from app.surveys import LANGUAGE_NAMES, get_survey

bp = Blueprint("constructs", __name__, url_prefix="/constructs")


def _context_survey():
    """The survey the researcher came from (to insert into), if any; none for a visitor who is not signed in."""
    sid = request.args.get("survey", type=int)
    return get_survey(sid) if sid and current_user.is_authenticated else None


def _target_blocks(survey):
    from app.editor import _is_consent_block

    return [b for b in survey.blocks if not _is_consent_block(b)] if survey and survey.is_editable else []


@bp.route("/")
def index():
    """The toolbox, open to visitors who are not signed in too (Ralf 2026-10-16): they get the list, the search and
    the filters, but no favourites, no linked elements and no publication counts (those come from the surveys of the
    organisations)."""
    guest = not current_user.is_authenticated
    q = request.args.get("q", "").strip()
    selected = constructs.clean_tags(request.args.getlist("tag"))  # only tags that exist (D-03)
    own_only = request.args.get("eigene") == "1"  # only the linked elements of the person and those shared with them
    rows = [] if own_only else constructs.search(q, selected)
    if english() and not q:  # by the name as shown (the database orders by the German one)
        rows.sort(key=lambda c: constructs.shown_text(c, "name").lower())
    survey = _context_survey()
    linked_list = [] if guest else linked.palette(current_user)
    if q:  # the search finds them too: every word in the name, the variable name or the wording
        words = q.lower().split()
        linked_list = [c for c in linked_list
                       if all(w in " ".join((c.name, (c.spec or {}).get("code", ""), c.search_text or "")).lower() for w in words)]
    return render_template(
        "constructs/index.html", q=q, selected=selected, rows=[constructs.shown(c) for c in rows], facets=constructs.facets(),
        tag_name=constructs.tag_name,
        counts=constructs.facet_counts(rows, q, selected), all_tags=constructs.all_tags(), total=len(constructs.search()),
        cite_counts={} if guest else citations.counts(),
        survey=survey, stars=set() if guest else constructs.starred(current_user), linked_list=linked_list, own_only=own_only,
        has_linked=bool(linked_list) or own_only,
    )


@bp.route("/box")
@login_required
def box():
    """The full construct box of the questionnaire editor (konzept-konstrukt-schnellbox-2026-10-16.md, K4-K8): the
    toolbox as a part of the editor's dialog, searched and filtered in place; each card opens to its items and has
    "+ Einfügen" (the editor posts it with the block chosen in the dialog's head). `pick`: all, stars (favourites),
    org (recommended in the survey's organisation, app/shortlist.py) or mine (own and shared linked elements).
    `keep`: the dialog of an organisation's or the platform's list ("+ Aufnehmen" instead of "+ Einfügen", K9)."""
    from app import orgs, shortlist

    sid = request.args.get("survey", type=int)
    survey = get_survey(sid, need="viewer") if sid else None
    keep = request.args.get("keep")  # "platform" or an organisation id
    org_id = orgs.of(survey) if survey else None
    if keep:
        org_id = None if keep == "platform" else request.args.get("keep", type=int)
        if not shortlist.keeper_of(current_user, org_id):
            abort(404)
    q = request.args.get("q", "").strip()
    selected = constructs.clean_tags(request.args.getlist("tag"))  # only tags that exist (D-03)
    pick = request.args.get("pick", "all")
    recommended, by = shortlist.for_org(org_id) if not keep else (shortlist.keys(org_id), "")
    stars = constructs.starred(current_user)
    if pick == "mine":
        rows = linked.palette(current_user)
        if q:
            words = q.lower().split()
            rows = [c for c in rows if all(w in " ".join((c.name, (c.spec or {}).get("code", ""), c.search_text or "")).lower()
                                           for w in words)]
    else:
        rows = constructs.search(q, selected)
        if pick == "stars":
            rows = [c for c in rows if c.key in stars]
        elif pick == "org":
            rank = {k: n for n, k in enumerate(recommended)}
            rows = sorted((c for c in rows if c.key in rank), key=lambda c: rank[c.key])
    langs = survey.languages if survey else []
    if request.args.get("langs") and langs:  # only those with a version in every language of the survey
        rows = [c for c in rows if set(langs) <= set(c.languages)]
    if english() and not q and pick not in ("org", "mine"):
        rows.sort(key=lambda c: constructs.shown_text(c, "name").lower())
    lang = survey.default_language if survey else ("en" if english() else "de")
    return render_template(
        "constructs/_box.html", rows=[constructs.shown(c) for c in rows], q=q, selected=selected, pick=pick, survey=survey,
        stars=stars, recommended=set(recommended), by=by, keep=keep, kept=set(shortlist.keys(org_id)) if keep else set(),
        org_label=shortlist.for_org(org_id)[1] if not keep else "", facets=constructs.facets(), tag_name=constructs.tag_name,
        all_tags=constructs.all_tags(), counts=constructs.facet_counts(rows, q, selected) if pick != "mine" else {},
        lang=lang, languages=LANGUAGE_NAMES, n_stars=len(stars), n_org=len(recommended), is_linked=constructs.is_linked_key,
        wording_allowed={c.key for c in rows if constructs.permitted(c.key, survey, current_user)},
        permission_notice=constructs.permission_notice(),
    )


@bp.route("/shortlist/<keep>", methods=["POST"])
@login_required
def shortlist_change(keep):
    """Change a "Schnellbox" list (app/shortlist.py, K9): `keep` is "platform" (site admins) or an organisation id (its
    admins). action add (also from the full box, then JSON), remove, up or down."""
    from app import security_log, shortlist

    org_id = None if keep == "platform" else (int(keep) if keep.isdigit() else -1)
    if org_id == -1 or not shortlist.keeper_of(current_user, org_id):
        abort(404)
    action, key = request.form.get("action"), request.form.get("key", "")
    found = shortlist.keys(org_id)
    if action == "add":
        ok = shortlist.add(org_id, key)
    elif action == "remove":
        shortlist.remove(org_id, key)
        ok = True
    elif action in ("up", "down") and key in found:
        n = found.index(key)
        m = n - 1 if action == "up" else n + 1
        if 0 <= m < len(found):
            found[n], found[m] = found[m], found[n]
        shortlist.order(org_id, found)
        ok = True
    else:
        abort(400)
    db.session.commit()
    if ok and action in ("add", "remove"):
        security_log.event("construct_shortlist", by=current_user.id, target=f"org {org_id}" if org_id else "platform",
                           kind=f"{action} {key}"[:40])
    if request.form.get("json"):
        return jsonify(done=ok, full=len(shortlist.keys(org_id)) >= shortlist.MAX)
    if not ok:
        flash(de_en("Nicht aufgenommen: schon in der Liste oder die Liste ist voll ({n}).",
                    "Not added: already in the list, or the list is full ({n}).", n=shortlist.MAX), "error")
    back = url_for("admin.construct_pool", _anchor="schnellbox") if org_id is None else url_for("org.constructs", org_id=org_id)
    return redirect(back)


@bp.route("/linked/<key>", methods=["GET", "POST"], endpoint="linked")
@login_required
@autosave.aware
def linked_element(key):
    """A linked element (app/linked.py): its uses and state; whoever made it renames or dissolves it here. The name
    saves by itself (autosave); sharing and dissolving are actions of their own."""
    c = linked.get(key)
    if c is None or not linked.usable(current_user, c):
        abort(404)
    info = linked.overview(c, current_user)
    from app import submissions

    if request.method == "POST":
        if not info["owner"]:
            abort(403)
        if request.form.get("action") == "submit":  # for the construct toolbox (app/submissions.py)
            fields, errors = submissions.read_form(request.form)
            if errors:
                flash(de_en("Bitte ausfüllen: {fields}.", "Please fill in: {fields}.", fields=", ".join(errors)), "error")
                return redirect(url_for("constructs.linked", key=c.key, **{k: v[:300] for k, v in fields.items()}) + "#toolbox")
            try:
                s = submissions.submit(c, current_user, fields)
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("constructs.linked", key=c.key) + "#toolbox")
            db.session.commit()
            submissions.notify(s)
            flash(de_en("Eingereicht. Ein Admin der Plattform prüft das Element; die Antwort steht hier und auf Ihrer Startseite.",
                        "Submitted. A platform admin checks the element; the answer appears here and on your start page."),
                  "success")
            return redirect(url_for("constructs.linked", key=c.key) + "#toolbox")
        if request.form.get("action") == "answer":
            s = submissions.latest(c)
            if s is None or s.user_id != current_user.id or not (request.form.get("answer") or "").strip():
                flash(de_en("Bitte eine Antwort schreiben.", "Please write an answer."), "error")
                return redirect(url_for("constructs.linked", key=c.key) + "#toolbox")
            submissions.answer(s, current_user, request.form["answer"])
            db.session.commit()
            submissions.notify(s)
            flash(de_en("Antwort gesendet.", "Answer sent."), "success")
            return redirect(url_for("constructs.linked", key=c.key) + "#toolbox")
        if request.form.get("action") == "rename":
            if linked.rename(c, request.form.get("name", "")):
                db.session.commit()
                flash(de_en("Name gespeichert.", "Name saved."), "success")
            else:
                flash(de_en("Bitte einen Namen eingeben.", "Please enter a name."), "error")
            return redirect(url_for("constructs.linked", key=c.key))
        if request.form.get("action") in ("share_person", "unshare_person"):
            from app import accounts, auth
            from app.auth import ORCID_RE
            from app.models import ConstructShare

            from app import people_keys

            orcid = (request.form.get("orcid") or "").strip()
            known = people_keys.account(orcid, create=False)
            shared = known is not None and any(s.user_id == known.id for s in c.person_shares)
            if request.form["action"] == "unshare_person":
                # the account by its id (field "user"), from a page of before by its key
                uid = request.form.get("user", type=int) or (known.id if known is not None else None)
                c.person_shares = [s for s in c.person_shares if s.user_id != uid]
                db.session.commit()
                flash(de_en("Freigabe beendet. Befragungen, die das Element schon verwenden, behalten es.",
                            "Sharing ended. Surveys that already use the element keep it."), "success")
            elif not ORCID_RE.match(orcid) and accounts.local_account(orcid) is None:
                flash(de_en("Bitte eine ORCID iD angeben (0000-0000-0000-0000) oder eine Person aus den Kontakten wählen.",
                            "Please enter an ORCID iD (0000-0000-0000-0000) or choose a person from your contacts."), "error")
            elif known is not None and known.id == current_user.id:
                flash(de_en("Das Element gehört Ihnen bereits.", "The element is already yours."), "error")
            elif not shared and not auth.may_sign_in(orcid):
                flash(auth.cannot_sign_in(), "error")
            elif not shared:
                from app.surveys import _remember_contact

                name = (request.form.get("name") or "").strip()[:200]
                user = people_keys.account(orcid, name)  # a pending account for an iD that never signed in
                c.person_shares.append(ConstructShare(user=user))
                _remember_contact(user, name)  # the name stays next to the account
                db.session.commit()
                flash(de_en("Geteilt: Die Person findet das Element im Fragebogen unter „Verknüpfte Elemente“ und kann es "
                            "einfügen, aber nicht ändern.",
                            "Shared: the person finds the element in the questionnaire under “Linked elements” and can "
                            "insert it, but not change it."), "success")
            return redirect(url_for("constructs.linked", key=c.key) + "#teilen")
        if request.form.get("action") in ("share", "unshare"):
            from app import orgs
            from app.models import ConstructGroupShare, WorkGroup

            group = db.session.get(WorkGroup, request.form.get("group", type=db_id) or 0)
            if request.form["action"] == "unshare":
                c.group_shares = [s for s in c.group_shares if group is None or s.group_id != group.id]
                db.session.commit()
                flash(de_en("Freigabe beendet. Befragungen, die das Element schon verwenden, behalten es.",
                            "Sharing ended. Surveys that already use the element keep it."), "success")
            elif group is None or group.member_of(current_user) is None:
                flash(de_en("Bitte eine Ihrer Gruppen wählen.", "Please choose one of your groups."), "error")
            elif not orgs.same(group, c):  # only within the element's organisation (app/orgs.py, A8)
                flash(orgs.fenced_out(), "error")
            elif not any(s.group_id == group.id for s in c.group_shares):
                c.group_shares.append(ConstructGroupShare(group_id=group.id))
                db.session.commit()
                flash(de_en("Mit der Gruppe „{group}“ geteilt: Alle Mitglieder, auch künftige, können das Element "
                            "einfügen, aber nicht ändern.",
                            "Shared with the group “{group}”: all members, future ones too, can insert the element, but "
                            "not change it.", group=group.name), "success")
            return redirect(url_for("constructs.linked", key=c.key) + "#teilen")
        if request.form.get("action") == "dissolve":
            name, mine = c.name, [i for i, _ in linked.dissolvable(current_user, c)]
            n = len(mine)
            if not linked.dissolve(c, only=mine):
                db.session.commit()
                if n == 1:
                    flash(de_en("Ihre Verwendung von „{name}“ ist jetzt gewöhnliche Kopien; die Verwendungen in "
                                "Befragungen, die Sie nicht bearbeiten können, bleiben verknüpft.",
                                "Your use of “{name}” is now an ordinary copy; the uses in surveys you cannot edit stay "
                                "linked.", name=name), "success")
                else:
                    flash(de_en("Ihre {n} Verwendungen von „{name}“ sind jetzt gewöhnliche Kopien; die Verwendungen in "
                                "Befragungen, die Sie nicht bearbeiten können, bleiben verknüpft.",
                                "Your {n} uses of “{name}” are now ordinary copies; the uses in surveys you cannot edit "
                                "stay linked.", n=n, name=name), "success")
                return redirect(url_for("constructs.index"))
            db.session.commit()
            if n == 1:
                flash(de_en("„{name}“ ist aufgelöst: Die Frage ist jetzt gewöhnliche Kopien; ihre Antworten bleiben in "
                            "den Befragungen.",
                            "“{name}” is dissolved: the question is now an ordinary copy; its answers stay in the "
                            "surveys.", name=name), "success")
            else:
                flash(de_en("„{name}“ ist aufgelöst: Alle {n} Fragen sind jetzt gewöhnliche Kopien; ihre Antworten "
                            "bleiben in den Befragungen.",
                            "“{name}” is dissolved: all {n} questions are now ordinary copies; their answers stay in the "
                            "surveys.", n=n, name=name), "success")
            return redirect(url_for("constructs.index"))
        abort(400)
    spec = c.spec or {}
    lang = request.args.get("lang") if request.args.get("lang") in spec.get("texts", {}) else _first_lang(list(spec.get("texts") or {"de": {}}))
    from app import linked_pool

    running, without_hint = _pool_choice()
    data = linked_pool.collect(c, current_user, running, without_hint)
    from app import groups

    mine = groups.of(current_user, c) if info["owner"] else []
    contacts, names = [], {}
    if info["owner"]:  # the address book for "Mit Personen teilen", and the names of the people shared with
        from app.models import Contact, User

        contacts = list(db.session.scalars(db.select(Contact).filter_by(owner_id=current_user.id)
                                           .order_by(Contact.last_used_at.desc())))
        shared_to = [s.user_id for s in c.person_shares if s.user_id]
        if shared_to:
            names = {u.id: u.name for u in db.session.scalars(db.select(User).filter(User.id.in_(shared_to))) if u.name}
        names.update({k.user_id: k.name for k in contacts if k.name and k.user_id and k.user_id not in names})
    # "Person hinzufügen" (_people.html, S8): the same search as for the people of a survey
    q, people = request.args.get("q", "").strip(), None
    if info["owner"] and q:
        from app.surveys import search_to_share

        from app.surveys import people_keys_of

        people = search_to_share(q, people_keys_of(c.person_shares) | {current_user.orcid})
    submission = submissions.latest(c)
    if submission is not None and not submission.seen and submission.user_id == current_user.id:
        submission.seen = True  # the start page no longer points to it
        db.session.commit()
    return render_template("constructs/linked.html", c=c, info=info, lang=lang, texts=(spec.get("texts") or {}).get(lang, {}),
                           submission=submission, submit_fields=submissions.FIELDS, prefill=request.args,
                           status_names=_status_names(),
                           my_groups=[g for g in mine if not any(s.group_id == g.id for s in c.group_shares)],
                           shared_groups=[s.group for s in c.group_shares], contacts=contacts, names=names, q=q, people=people,
                           config=spec.get("config", {}), languages=LANGUAGE_NAMES, data=data,
                           figures=linked_pool.summary(c, data), running=running, without_hint=without_hint)


def _first_lang(langs):
    """The wording shown first: English for a reader of the English pages if there is one, else the first."""
    return "en" if english() and "en" in langs else langs[0]


def _status_names():
    """The states of a survey as the table of uses names them."""
    return ({"draft": "draft", "running": "running", "closed": "closed"} if english()
            else {"draft": "Entwurf", "running": "läuft", "closed": "abgeschlossen"})


def _pool_choice():
    """What the shared analysis takes in: running surveys (yes unless "laufend=0"), participations from before the
    question was linked (only with "ohne_hinweis=1")."""
    return request.args.get("laufend") != "0", request.args.get("ohne_hinweis") == "1"


FILE_TYPES = {"csv": "text/csv", "sav": "application/x-spss-sav", "omv": "application/octet-stream", "rds": "application/octet-stream"}


@bp.route("/linked/<key>/data.<fmt>", endpoint="linked_data")
@login_required
def linked_data(key, fmt):
    """The shared data set of a linked element (app/linked_pool.py), from the surveys the person may open: CSV, or
    as a file for SPSS/JASP (.sav), jamovi (.omv) or R (.rds) with labels, as the survey exports."""
    from app import linked_pool
    from app.export import safe_text

    c = linked.get(key)
    if c is None or not linked.usable(current_user, c) or fmt not in FILE_TYPES:
        abort(404)
    data = linked_pool.collect(c, current_user, *_pool_choice())
    name = "".join(ch for ch in (c.spec or {}).get("code", c.key) if ch.isalnum() or ch in "_-") or c.key
    headers = {"Content-Disposition": f"attachment; filename=verknuepft_{name}.{fmt}", "Cache-Control": "no-store"}
    if fmt != "csv":
        return Response(linked_pool.file(c, data, fmt), mimetype=FILE_TYPES[fmt], headers=headers)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(data["header"])
    writer.writerows(["" if v is None else safe_text(v) for v in row] for row in data["rows"])
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv", headers=headers)


@bp.route("/<key>")
def detail(key):
    """One construct. A visitor who is not signed in sees what it is, its source and its terms of use, the wording
    only where the licence allows passing it on (constructs.wording_public), and nothing taken from surveys: no
    "Verwendet in", no publications, no "oft zusammen mit"."""
    c = constructs.get(key)
    if not c:
        abort(404)
    lang = request.args.get("lang") if request.args.get("lang") in c.languages else _first_lang(c.languages)
    survey = _context_survey()
    guest = not current_user.is_authenticated
    uses = [] if guest else constructs.usage(c.key)  # once for the figures, the publications and "oft zusammen mit" (I14)
    from app import orgs

    if not guest and orgs.several():  # counted only over surveys of one's own organisations (app/orgs.py, A14)
        mine = orgs.ids_of(current_user)
        uses = [(item, s) for item, s in uses if orgs.of(s) in mine]
    return render_template(
        "constructs/detail.html", c=constructs.shown(c), lang=lang, texts=c.spec["texts"][lang], config=c.spec["config"],
        languages=LANGUAGE_NAMES, facets=constructs.facets(), tag_name=constructs.tag_name, survey=survey, blocks=_target_blocks(survey),
        missing_langs=[l for l in (survey.languages if survey else []) if l not in c.languages],
        stats=constructs.usage_stats(c.key, uses), stars=set() if guest else constructs.starred(current_user),
        cited=[] if guest else citations.listing(c, uses), pooled=constructs.pooled(c.key),
        together=[] if guest else [(constructs.shown(o), n) for o, n in citations.together(c, uses=uses)],
        guest=guest, show_wording=(not guest or constructs.wording_public(c.key))
        and constructs.permitted(c.key, survey, current_user, [lang]),
        insert_allowed=constructs.permitted(c.key, survey, current_user),
        permission_required=c.key in constructs.PERMISSION_REQUIRED, permission_notice=constructs.permission_notice(),
    )


@bp.route("/<key>/pool.csv")
@login_required
def pool_csv(key):
    """Pooled data across closed surveys for meta-analyses (operator only, licence.md section 4)."""
    from app import twofactor

    again = twofactor.admin_page()
    if again:
        return again
    if not constructs.get(key) or not constructs.pooled(key):  # the RatSWD standard questions have no pool
        abort(404)
    from app.export import safe_text

    header, rows = constructs.pool(key)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    # formula protection like the other exports: a text cell starting with "=" is no formula in Excel
    writer.writerows(["" if v is None else safe_text(v) for v in row] for row in rows)
    # pseudonymous data of every study: kept in no cache, as the other data downloads (the charset comes with "text/csv")
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=pool_{key}.csv", "Cache-Control": "no-store"})


@bp.route("/<key>/used.<fmt>")
@login_required
def citing(key, fmt):
    """The publications that used a construct (app/citations.py) as BibTeX or RIS."""
    from app import bibliography

    c = constructs.get(key)
    if not c or fmt not in ("bib", "ris"):
        abort(404)
    items = citations.entries(c)
    if not items:
        abort(404)
    body = bibliography.bibtex(items) if fmt == "bib" else bibliography.ris(items)
    return Response(body, mimetype=("application/x-bibtex" if fmt == "bib" else "application/x-research-info-systems")
                    + "; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={c.key}_verwendet.{fmt}"})


@bp.route("/<key>/source.<fmt>")
def reference(key, fmt):
    """The references of one construct as BibTeX or RIS (app/bibliography.py)."""
    from app import bibliography

    c = constructs.get(key)
    if not c or fmt not in ("bib", "ris") or not (c.citation or c.doi):
        abort(404)
    items = bibliography.entries(c)
    body = bibliography.bibtex(items) if fmt == "bib" else bibliography.ris(items)
    return Response(body, mimetype=("application/x-bibtex" if fmt == "bib" else "application/x-research-info-systems")
                    + "; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={c.key}.{fmt}"})


@bp.route("/api/search")
@login_required
def api_search():
    """Top hits for the editor sidebar."""
    rows = constructs.search(request.args.get("q", ""), limit=request.args.get("limit", 8, type=int))
    return jsonify(constructs.palette_rows(rows, current_user))


@bp.route("/<key>/favourite", methods=["POST"])
@login_required
def star(key):
    """Star or unstar a construct: favourites are the constructs the questionnaire editor lists."""
    c = constructs.get(key)
    if not c:
        abort(404)
    on = constructs.toggle_star(current_user, c)
    db.session.commit()
    if request.form.get("json"):  # the editor palette: the new list comes back with the answer
        from app import orgs

        sid = request.form.get("survey", type=int)
        found = get_survey(sid, need="viewer") if sid else None
        org = orgs.of(found) if found else None  # the "Schnellbox" of the survey's organisation (app/shortlist.py)
        return jsonify(starred=on, sidebar=constructs.palette_rows(constructs.sidebar(current_user, org), current_user, org))
    survey = request.form.get("survey", type=int)
    if request.form.get("list"):  # back to the same search on the Konstrukte page
        return redirect(url_for("constructs.index", q=request.form.get("q") or None, tag=request.form.getlist("tag"),
                                survey=survey, _anchor=f"k-{c.key}"))
    return redirect(url_for("constructs.detail", key=c.key, lang=request.form.get("lang") or None, survey=survey))
