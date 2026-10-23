"""Admin pages (site admins only): researchers (signed-in ORCIDs), AI usage, themes of institutions and groups."""
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import brand
from app import autosave
from app import kind_access
from app.ids import db_id
from app.lang import de_en
from app.tables import ROW_SIZES

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@login_required
def _admins_only():
    if not current_user.is_admin:
        abort(403)
    from app import twofactor

    return twofactor.gate()  # a code from the authenticator app first (security audit 2026-10-04, H5)


def _euro(value):
    """'0,15' or '0.15' -> 0.15, '1.000' -> 1000 in German (units.read_entry in the language of the page; Mathe-Audit
    2026-10-06, Z-4: it was 1); empty or no number -> None (no price)."""
    from app import units

    found = units.read_entry(value)
    return None if found is None else max(found, 0)


@bp.route("/ai", methods=["GET", "POST"])
@autosave.aware
def ai_usage():
    from app import ai_stats

    if request.method == "POST":  # the price table (saves by itself; a row counts once it has a model and a price)
        table = {"text": {}, "image": {}}
        for model, p_in, p_out in zip(request.form.getlist("text_model"), request.form.getlist("text_in"),
                                      request.form.getlist("text_out")):
            if model.strip() and (_euro(p_in) is not None or _euro(p_out) is not None):
                table["text"][model.strip()] = {"in": _euro(p_in) or 0, "out": _euro(p_out) or 0}
        for model, size, price in zip(request.form.getlist("image_model"), request.form.getlist("image_size"),
                                      request.form.getlist("image_price")):
            if model.strip() and size.strip() and _euro(price) is not None:
                table["image"].setdefault(model.strip(), {})[size.strip()] = _euro(price)
        ai_stats.save_prices(table)
        flash(de_en("Preise gespeichert.", "Prices saved."), "success")
        from app import units

        for note in units.entry_notes():  # "1,000" in German: read as 1 (the page shows it as a note)
            flash(note, "warning")
        return redirect(url_for("admin.ai_usage", **_ai_period_args()))
    from app import admin_stats, ai_access

    r = ai_stats.report(request.args.get("period"), admin_stats.read_since(request.args.get("since")))
    return render_template("admin/ai.html", r=r, chart=ai_stats.chart(r), purposes=ai_stats.purposes(),
                           periods=ai_stats.periods(), own_labels=ai_access.provider_label)


def _ai_period_args():
    """The period of the page KI as link arguments, so that saving the prices keeps it."""
    from app import ai_stats

    period = ai_stats.period_key(request.args.get("period"))
    return {"period": period, "since": request.args.get("since")} if period == "since" else {"period": period}


@bp.route("/ip-region")
def ip_region():
    """The former tab "IP-Region": a panel of the tab "Server" now (Ralf, 2026-10-07)."""
    return redirect(url_for("admin.database", _anchor="ip-region"))


def _ip_part():
    """What the server finds for the admin's own address: shows whether the table is there and whether the app
    sees the addresses of its visitors at all (behind a proxy only with PROXY_COUNT). Nothing is stored. The panel
    "IP-Region und Proxy" of the tab "Server"."""
    import ipaddress

    from flask import current_app

    from app import geoip
    from app.items.federal_state import STATES

    country, state = geoip.lookup(request.remote_addr)
    try:
        local = not ipaddress.ip_address(request.remote_addr or "").is_global
    except ValueError:
        local = True
    # How many addresses arrived in X-Forwarded-For (ProxyFix reads the header but leaves it in place): each proxy
    # appends one, so behind Plesk's nginx a plain browser request brings 1. Only the count is shown, no address.
    hops = len([a for a in request.headers.get("X-Forwarded-For", "").split(",") if a.strip()])
    return {"geoip": geoip, "month": geoip.month(), "country": country, "local": local,
            "state": next((de_en(de, en) for code, de, en in STATES if code == state), ""),
            "proxies": current_app.config["PROXY_COUNT"], "hops": hops}


@bp.route("/constructs")
def construct_pool():
    """Pooled construct data (licence.md section 4, app/construct_pool.py): every toolbox construct in use, with its
    closed studies and their participations; one page per construct has the reliabilities."""
    from app import construct_pool, submissions

    from app import constructs, shortlist

    return render_template("admin/constructs.html", rows=construct_pool.overview(), submitted=submissions.open_ones(),
                           decided=submissions.decided_ones(), keep="platform", max=shortlist.MAX,
                           shortlist_rows=[constructs.shown(constructs.get(k)) for k in shortlist.keys(None)])


@bp.route("/constructs/submitted/<int:sid>", methods=["GET", "POST"])
def submission(sid):
    """A linked element submitted for the toolbox (app/submissions.py): ask back, reject, accept as a new toolbox
    construct; after accepting, mark its wording as checked against the source."""
    from app import constructs, submissions
    from app.extensions import db
    from app.models import Construct, ConstructSubmission

    s = db.session.get(ConstructSubmission, sid) or abort(404)
    here = url_for("admin.submission", sid=sid)
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action in ("question", "reject"):
                submissions.decide(s, ConstructSubmission.QUESTION if action == "question" else ConstructSubmission.REJECTED,
                                   request.form.get("note"))
                db.session.commit()
                flash(de_en("Rückfrage gesendet.", "Query sent.") if action == "question" else de_en("Abgelehnt.", "Rejected."), "success")
            elif action == "accept":
                c = submissions.accept(s, request.form)
                db.session.commit()
                constructs.rebuild_index(force=True)
                flash(de_en("Aufgenommen als „{name}“ ({key}), Status „ungeprüft“.", "Added as “{name}” ({key}), status “unchecked”.",
                            name=c.name, key=c.key), "success")
            elif action == "verify" and s.toolbox_key:
                c = db.session.scalar(db.select(Construct).filter_by(key=s.toolbox_key)) or abort(404)
                c.status = "verified" if c.status == "review" else "review"
                db.session.commit()
                flash(de_en("Wortlaut als geprüft markiert.", "Wording marked as checked.") if c.status == "verified"
                      else de_en("Wieder als ungeprüft markiert.", "Marked as unchecked again."), "success")
            else:
                abort(400)
        except ValueError as e:
            db.session.rollback()
            flash(str(e), "error")
        return redirect(here)
    toolbox = db.session.scalar(db.select(Construct).filter_by(key=s.toolbox_key)) if s.toolbox_key else None
    return render_template("admin/submission.html", s=s, f=s.fields or {}, labels=submissions.labels(),
                           proposal=submissions.proposal(s) if s.state in ConstructSubmission.OPEN else None, toolbox=toolbox)


@bp.route("/constructs/compilation")
def construct_compose():
    """Several toolbox constructs as one data set for a meta-analysis (app/construct_compose.py): pick, filter, see
    how much comes together, download the ZIP."""
    from app import construct_compose

    keys = request.args.getlist("k")
    f = construct_compose.filters(request.args)
    data = construct_compose.build(keys, f) if keys else None
    languages = sorted({lang for c, _, _ in construct_compose.choices() for lang in c.languages})
    return render_template("admin/construct_compose.html", choices=construct_compose.choices(), keys=keys, f=f,
                           data=data, languages=languages)


@bp.route("/constructs/compilation.zip")
def construct_compose_file():
    from flask import Response

    from app import construct_compose
    from app.timeutil import to_local
    from app.models import utcnow

    keys = request.args.getlist("k")
    if not keys:
        abort(404)
    f = construct_compose.filters(request.args)
    data = construct_compose.build(keys, f)
    if not data["constructs"]:
        abort(404)
    body = construct_compose.zip_file(data, f, to_local(utcnow()).strftime("%d.%m.%Y %H:%M"))
    return Response(body, mimetype="application/zip", headers={
        "Content-Disposition": "attachment; filename=konstrukte_zusammenstellung.zip", "Cache-Control": "no-store"})


@bp.route("/constructs/<key>")
def construct_pool_detail(key):
    """N, Cronbach's alpha and McDonald's omega with their intervals per study, and the pooled values."""
    from app import construct_pool, constructs
    from app.extensions import db
    from app.models import Construct

    c = db.session.scalar(db.select(Construct).filter(Construct.key == key))
    if c is None or constructs.is_linked_key(c.key):
        abort(404)
    from app import citations

    return render_template("admin/construct_pool.html", c=constructs.shown(c), r=construct_pool.report(c), sources=construct_pool.sources(),
                           cited=citations.listing(c))


@bp.route("/constructs/<key>/citations", methods=["POST"])
def construct_citations(key):
    """The operator enters or removes a publication that used the construct (app/citations.py)."""
    from app import citations, constructs
    from app.extensions import db
    from app.models import Construct

    c = db.session.scalar(db.select(Construct).filter(Construct.key == key))
    if c is None or constructs.is_linked_key(c.key):
        abort(404)
    back = redirect(url_for("admin.construct_pool_detail", key=key) + "#verwendet")
    doi = citations.doi_of(request.form.get("doi"))
    if doi is None:
        flash(de_en("Die DOI sieht nicht richtig aus (z. B. 10.1037/0022-3514.84.4.822).",
                    "The DOI does not look right (e.g. 10.1037/0022-3514.84.4.822)."), "error")
        return back
    if request.form.get("action") == "remove":
        citations.remove_for_construct(c, doi)
        flash(de_en("Entfernt (auch wenn Forschende sie eingetragen hatten).", "Removed (even if researchers had entered it)."),
              "success")
    else:
        try:
            citations.add_for_construct(c, doi, current_user)
        except ValueError as e:
            flash(str(e), "error")
            return back
        flash(de_en("Eingetragen.", "Entered."), "success")
    db.session.commit()
    return back


DB_FIELDS = ("host", "port", "name", "user", "password")


@bp.route("/log")
def access_log():
    """The access log (app/audit.py): downloads of raw and contact data, changes of who has access, deletions,
    account and database actions. Sorted by a click on a column head, searched and paged like the other admin
    lists (app/tables.py; Ralf, 2026-10-05), newest first."""
    from app import audit
    from app.models import AccessLog
    from app.extensions import db
    from app.tables import Listing, by_text

    log = Listing("p", {"at": ([AccessLog.at], "desc"), "who": by_text(AccessLog.who),
                        "survey": by_text(AccessLog.survey_code), "action": by_text(AccessLog.action)}, "at",
                  search=(AccessLog.who, AccessLog.survey_code, AccessLog.action, AccessLog.detail),
                  then=(AccessLog.id.desc(),), anchor="protokoll", per_page=50)
    rows = [r[0] for r in log.rows(db.select(AccessLog))]
    return render_template("admin/log.html", rows=rows, log=log, keep=audit.KEEP_DAYS)


@bp.route("/privacy", methods=["GET", "POST"])
@autosave.aware
def privacy():
    """The Datenschutzkonzept (app/privacy_concept.py), built anew from code and configuration; the fields the code
    cannot know (controller, data protection officer, authority, breach route, next review) save by themselves."""
    from app import privacy_concept

    if request.method == "POST":
        privacy_concept.save_org(request.form, current_user)
        flash(de_en("Gespeichert.", "Saved."), "success")
        return redirect(url_for("admin.privacy") + "#zweck")
    return render_template("admin/privacy.html", c=privacy_concept.build(), max_chars=privacy_concept.MAX_CHARS)


@bp.route("/privacy.docx")
def privacy_docx():
    """The Datenschutzkonzept as a Word file."""
    from flask import Response as FlaskResponse
    from flask import current_app

    from app import privacy_concept

    try:
        from app import privacy_concept_docx
    except ImportError:  # python-docx is missing on a server whose vendor/ was built before it was needed
        current_app.logger.exception("Word export without python-docx")
        flash(de_en("Der Word-Export geht auf diesem Server gerade nicht: Das Paket python-docx fehlt.",
                    "The Word export does not work on this server at the moment: the package python-docx is missing."),
              "error")
        return redirect(url_for("admin.privacy"))
    from app.timeutil import local_today

    name = de_en("datenschutzkonzept", "data-protection-concept")
    return FlaskResponse(privacy_concept_docx.build(privacy_concept.build()), headers={
        "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": f'attachment; filename="{name}-{local_today().isoformat()}.docx"',
        "Cache-Control": "no-store",
    })


@bp.route("/database", methods=["GET", "POST"])
@autosave.aware  # the Slack panel saves by itself; the other forms of the page send as before
def database():
    """Which database the app works on, and moving its content into PostgreSQL: check the new database, copy
    (app/dbmove.py). The switch itself is a line in the server's .env and a restart; the page never writes
    it. The password is only used for this request and is never stored or logged."""
    from flask import current_app

    from app import dbmove, monitoring_feed

    if request.method == "POST" and "slack_content" in request.form:
        # what goes to Slack (app/notify.py): short unless a site admin chooses "full" (Ralf, 2026-10-21)
        from app import notify
        from app.extensions import db

        notify.set_full(request.form.get("slack_content") == "full", current_user)
        db.session.commit()
        flash(de_en("Gespeichert.", "Saved."), "success")
        return redirect(url_for("admin.database") + "#slack")
    if request.method == "POST" and request.form.get("action") in ("monitoring_make", "monitoring_remove"):
        # the token of the monitoring feed (app/monitoring_feed.py): made, changed and removed here, by site admins only
        if request.form["action"] == "monitoring_make":
            monitoring_feed.make_token(current_user.id)
            flash(de_en("Neues Token für den Monitoring-Feed erzeugt. Die Adresse steht im Block „Monitoring-Feed“.",
                        "New token for the monitoring feed made. The address is in the block “Monitoring feed”."), "success")
        else:
            monitoring_feed.remove_token(current_user.id)
            flash(de_en("Token des Monitoring-Feeds entfernt: Die Adresse gibt es nicht mehr.",
                        "Token of the monitoring feed removed: the address no longer exists."), "success")
        return redirect(url_for("admin.database") + "#monitoring")
    form = {k: (request.form.get(k) or "").strip() for k in DB_FIELDS}
    form["port"] = form["port"] or "5432"
    checked = copied = error = None
    if request.method == "POST" and request.form.get("action") == "probe":
        # may this server run an estimation in a process of its own? (app/estimate.py)
        from app import estimate

        failed = estimate.start_probe(current_app._get_current_object())
        if failed:
            flash(de_en("Der Prüfprozess ließ sich nicht starten: {e}", "The test process could not be started: {e}",
                        e=failed), "error")
        return redirect(url_for("admin.database") + "#prozess")
    if request.method == "POST" and request.form.get("action") == "mail_test":
        # sent in the request, so the page can say what went wrong (the reason, never the password)
        from app import mail, passwords

        to = passwords.normal_email(request.form.get("to"))
        if not mail.enabled() or not to:
            flash(de_en("Bitte eine gültige E-Mail-Adresse angeben.", "Please enter a valid email address."), "error")
        else:
            try:
                mail.deliver(mail.message(to, de_en("Askurious: Test-E-Mail", "Askurious: test email"), de_en(
                    "Diese Test-E-Mail kam vom Server {url} an. Der Versand funktioniert.",
                    "This test email came from the server {url}. Sending works.",
                    url=brand.public_base()) + mail.signature()))
            except Exception as e:  # noqa: BLE001 - shown to the admin
                flash(de_en("Die Test-E-Mail ging nicht raus: {e}", "The test email was not sent: {e}",
                            e=f"{type(e).__name__}: {str(e)[:200]}"), "error")
            else:
                flash(de_en("Test-E-Mail an {to} abgegeben. Kommt sie nicht an, auch im Spam-Ordner nachsehen.",
                            "Test email handed over for {to}. If it does not arrive, look in the spam folder too.", to=to),
                      "success")
        return redirect(url_for("admin.database") + "#mailserver")
    if request.method == "POST" and request.form.get("action") == "remove_old":
        # the SQLite file from before the move to PostgreSQL (audit 2026-10-05, L-5): asked twice, after a fresh sign-in
        from app import accounts

        again = accounts.need_fresh(url_for("admin.database"))
        if again:
            return again
        try:
            old = dbmove.remove_old_sqlite(current_app._get_current_object())
        except dbmove.MoveError as e:
            flash(str(e), "error")
        else:
            current_app.logger.warning("Old SQLite database %s removed", old["path"])
            flash(de_en("Die alte SQLite-Datei ist gelöscht.", "The old SQLite file has been deleted."), "success")
        return redirect(url_for("admin.database") + "#sicherungen")
    if request.method == "POST":
        from app import accounts

        # "Prüfen" connects from the server to any host and port, "Kopieren" sends the whole database there: both
        # after a fresh sign-in (audit 2026-10-11, AU9), a computer left signed in is not enough
        again = accounts.need_fresh(url_for("admin.database"))
        if again:
            return again
        try:
            url = dbmove.build_url(**form)
            if request.form.get("action") == "copy":
                copied = dbmove.copy(url)
                current_app.logger.warning("Database copied to %s: %s rows", dbmove.masked(url), copied["rows"])
            else:
                checked = dbmove.check(url)
        except dbmove.MoveError as e:
            error = str(e)
    from app import migrations

    app = current_app._get_current_object()
    leftovers = {"backups": migrations.backups(app), "days": migrations.BACKUP_DAYS, "temp": dbmove.stale_copies(),
                 "old": dbmove.old_sqlite(app)}
    from app.table_notes import note

    from app import _deployed_at, backups, compute, containers, estimate, whisper_jobs
    from app.extensions import db

    on_pg = db.engine.dialect.name == "postgresql"
    from app import notify

    return render_template("admin/database.html", slack_full=notify.full(),
                           slack_on=bool(current_app.config.get("SLACK_WEBHOOK_URL")), deployed=_deployed_at(), table_note=note, s=dbmove.status(fresh=request.method == "POST"), form=form, checked=checked, copied=copied,
                           error=error, env_line=dbmove.env_line(**form), leftovers=leftovers,
                           backups=backups.overview(app.config.get("CONTAINER_STATS"), db.engine.url.database if on_pg else None),
                           compute_status=compute.status(app),
                           containers=containers.overview(app.config.get("CONTAINER_STATS"),
                                                          only=containers.shown(app.config.get("CONTAINER_SERVICES"))),
                           probe=estimate.probe_state(app),
                           monitoring_url=monitoring_feed.feed_url(), status_json_url=monitoring_feed.status_json_url(),
                           monitoring_qr=monitoring_feed.setup_qr(),
                           whisper_status=whisper_jobs.status()[0] if app.config.get("WHISPER_JOBS") and app.config.get("WHISPER_URL") else None,
                           whisper_model=whisper_jobs.default_model(app.config),
                           ip=_ip_part(),
                           mail_status=_mail_status(),
                           security=_operation_security(app),
                           estimate_on=bool(app.config.get("ESTIMATE_PROCESS")))


def _operation_security(app):
    """What the panel "Sicherheit des Betriebs" warns about (audit 2026-10-13, KR-5, SV-5/DP-9):
    keys that are weak (names only, never a value), and a deploy that
    skipped its tests or the check for known vulnerabilities."""
    from app import _deployed_at, key_strength

    deployed = _deployed_at() or {}
    return {"admin_totp": bool(app.config.get("ADMIN_TOTP")), "keys": key_strength.findings(app.config),
            "skipped": deployed.get("skipped") or []}


def _mail_status():
    """The section "Mailserver" of the tab Server: what is set (never the password), what is missing."""
    from app import mail, passwords

    s = mail.settings()
    row = passwords.login_row(current_user)
    queued = None
    try:
        from app import mailqueue

        queued = mailqueue.waiting()
    except ImportError:
        pass
    return {**{k: v for k, v in s.items() if k != "password"}, "on": mail.enabled(), "missing": mail.missing(),
            "plaintext_login": mail.plaintext_login(),
            "own": row.email if row is not None and row.email else "", "queued": queued}


def _back(endpoint, anchor=None):
    """Back to a list after a change of one row: the same page, order and search (the form's `back`)."""
    from app import tables

    return redirect(url_for(endpoint, **tables.back_args(request.form.get("back")), _anchor=anchor))


def _real(*more):
    from app.models import Response

    from app import plugins

    # no further words of a live vote, no imports of an add-on module (app/plugins.py)
    return (Response.source == "real", Response.is_preview.is_(False), Response.not_live_extra(),
            *plugins.counted_responses(), *more)


@bp.route("/")
def index():
    """The administration opens with "Befragungen" (Ralf, 2026-10-17). "Personen" was here until then: an address of
    its lists (f_…) goes on to /admin/people, one of the former panel of groups (g_…) to "Gruppen"."""
    if any(k.startswith("g_") for k in request.args):  # the list of groups was a panel here until 2026-10-10
        return redirect(url_for("admin.groups", **{k: v for k, v in request.args.items() if k.startswith("g_")},
                                _anchor="gruppen"), 308)
    if any(k.startswith("f_") for k in request.args):
        return redirect(url_for("admin.researchers", **request.args), 308)
    return redirect(url_for("admin.surveys_page"))


@bp.route("/people")
def researchers():
    """Everyone who signed in: organisation, surveys (as member) and real completed participations in them. Both
    lists are sorted, searched and cut into pages by the database (app/tables.py); the numbers are counted for
    the rows of the page only."""
    from flask import current_app

    from app.extensions import db
    from app.models import Response, Survey, SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember
    from app.tables import Listing, by_text

    def count(column, *where, join=None):
        query = db.select(db.func.count(column))
        if join is not None:
            query = query.join(*join)
        return query.where(*where).scalar_subquery()

    done = _real(Response.status == Response.COMPLETED)
    # per person
    n_surveys = count(SurveyMember.id, SurveyMember.user_id == User.id)
    n_running = count(SurveyMember.id, SurveyMember.user_id == User.id, Survey.status == Survey.RUNNING,
                      join=(Survey, Survey.id == SurveyMember.survey_id))
    n_own = count(Survey.id, Survey.created_by_id == User.id)
    n_completed = count(Response.id, SurveyMember.user_id == User.id, *done,
                        join=(SurveyMember, SurveyMember.survey_id == Response.survey_id))
    first_group = (db.select(db.func.min(by_text(WorkGroup.name))).join(WorkGroupMember)
                   .where(WorkGroupMember.user_id == User.id).scalar_subquery())
    # the short name of the home organisation as the column shows it (orgs.short_of): "main" without a row of
    # its own, nothing for guests, so they come last (Ralf, 2026-10-20: the column could not be sorted)
    from app import orgs
    from app.models import Organisation, OrganisationMember

    home = (db.select(OrganisationMember.org_id)
            .where(OrganisationMember.user_id == User.id, OrganisationMember.is_home.is_(True))
            .correlate(User).limit(1).scalar_subquery())  # two levels down: named, or it reads "user" anew
    home_org = (db.select(db.case((Organisation.key == orgs.GUEST, ""), else_=Organisation.name))
                .where(Organisation.id == db.func.coalesce(home, orgs.default_id())).correlate(User).scalar_subquery())
    people = Listing("f", {
        "nr": User.number, "name": by_text(User.name), "org": by_text(home_org), "groups": first_group,
        "surveys": (n_surveys, "desc"), "running": (n_running, "desc"), "own": (n_own, "desc"),
        "completed": (n_completed, "desc"), "since": (User.created_at, "desc"), "last": (User.last_login_at, "desc"),
    }, default="last", then=[User.id], anchor="forschende", sizes=ROW_SIZES, search=[
        User.name, User.orcid, UserLogin.email,
        lambda match: db.exists().where(WorkGroupMember.user_id == User.id, WorkGroupMember.group_id == WorkGroup.id,
                                        match(WorkGroup.name))])
    base = db.select(User.id).outerjoin(UserLogin, UserLogin.user_id == User.id).where(User.pending.is_(False))
    kinds_only = request.args.get("f_kinds") == "1"  # only accounts with a survey type below "edit" (app/kind_access.py)
    if kinds_only:
        base = base.where(kind_access.below_edit_clause())
    from app import account_types
    from app.models import utcnow

    # accounts by their period (app/account_types.py, N16): ending within 30 days, ended, due for deletion
    period = request.args.get("f_end") if request.args.get("f_end") in ("soon", "ended", "due") else None
    if period == "soon":
        base = base.where(UserLogin.valid_until.is_not(None), UserLogin.valid_until > utcnow(),
                          UserLogin.valid_until < utcnow() + timedelta(days=account_types.NOTICE_DAYS[0] + 1))
    elif period in ("ended", "due"):
        base = base.where(UserLogin.block_reason == account_types.EXPIRED)
    type_filter = account_types.get(request.args.get("f_type"))
    if type_filter is not None:
        base = base.where(UserLogin.account_type_id == type_filter.id)
    from app import orgs

    org_filter = orgs.get(request.args.get("f_org"))
    if org_filter is not None:  # the people of one organisation (from the tab "Organisationen")
        base = base.where(User.id.in_(orgs.user_ids_in([org_filter.id])))
    group_filter = db.session.get(WorkGroup, request.args.get("f_group", type=int) or 0)
    if group_filter is not None:  # the members of one group (Ralf 2026-10-16)
        base = base.where(User.id.in_(db.select(WorkGroupMember.user_id).where(WorkGroupMember.group_id == group_filter.id)))
    # every group, apart from the organisation filter; with several organisations named with theirs (same names may stand
    # in two organisations)
    shorts = {o.id: o.name for o in orgs.all_orgs()} if orgs.several() else None
    group_options = [(g.id, f"{g.name} · {shorts.get(orgs.of(g), '')}" if shorts else g.name) for g in db.session.scalars(
        db.select(WorkGroup).order_by(db.func.lower(WorkGroup.name), WorkGroup.id))]
    if period == "due":  # few rows: the ended ones whose type's months are over (N13)
        due = [uid for uid in db.session.scalars(base) if account_types.deletion_due(db.session.get(UserLogin, uid))]
        base = base.where(User.id.in_(due))
    ids = [row[0] for row in people.rows(base)]
    users = {u.id: u for u in db.session.scalars(db.select(User).where(User.id.in_(ids)))}
    logins = {row.user_id: row for row in db.session.scalars(db.select(UserLogin).where(UserLogin.user_id.in_(ids)))}
    numbers = {row[0]: row for row in db.session.execute(
        db.select(User.id, n_surveys, n_running, n_own, n_completed).where(User.id.in_(ids)))}
    groups_of = {}
    for uid, name in db.session.execute(
            db.select(WorkGroupMember.user_id, WorkGroup.name).join(WorkGroup)
            .where(WorkGroupMember.user_id.in_(ids)).order_by(by_text(WorkGroup.name))):
        groups_of.setdefault(uid, []).append(name)
    rows = []
    for uid in ids:
        u, login, (_, n_all, running, own, completed) = users[uid], logins.get(uid), numbers[uid]
        rows.append({"user": u, "surveys": n_all, "groups": groups_of.get(u.id, []), "login": login,
                     "blocked": login is not None and login.status == UserLogin.BLOCKED,
                     "managed": u.orcid not in current_app.config["ADMIN_ORCIDS"],  # admins: by the server's configuration
                     "running": running, "own": own, "completed": completed,
                     "kinds": kind_access.clean(login.kinds) if login is not None else None,
                     "period": account_types.summary(login),
                     "org_name": orgs.short_of(u)})  # the short name on the platform's pages

    totals = {"users": db.session.scalar(db.select(db.func.count(User.id)).where(User.pending.is_(False)))}
    # requests "Passwort vergessen?" from the sign-in page (auth.forgot_password), oldest first
    requests = db.session.execute(
        db.select(User, UserLogin).join(UserLogin, UserLogin.user_id == User.id)
        .where(UserLogin.reset_requested_at.is_not(None)).order_by(UserLogin.reset_requested_at)).all()
    # the groups are a tab of their own again, "Gruppen" (Ralf 2026-10-10)
    return render_template("admin/researchers.html", rows=rows, totals=totals, people=people,
                           sign_in=_sign_in_list(), requests=requests, kinds_only=kinds_only,
                           kinds_default=kind_access.full(kind_access.default()), kind_keys=kind_access.KINDS,
                           period=period, type_filter=type_filter, types=account_types.all_types(),
                           type_counts={t.id: account_types.count_of(t) for t in account_types.all_types()},
                           default_type=account_types.default_type(), org_filter=org_filter,
                           group_filter=group_filter, group_options=group_options,
                           org_options=[(o.id, orgs.label(o)) for o in orgs.all_orgs()] if orgs.several() else [])


@bp.route("/surveys")
def surveys_page():
    """The tab "Befragungen", the first of the administration: the dashboard of the platform in a chosen period
    (app/admin_stats.py, Ralf 2026-10-17) and the surveys by real participations (out of "Personen", Ralf
    2026-10-07). The list is sorted, searched and cut into pages by the database (app/tables.py); its column "im
    Zeitraum" follows the period of the dashboard."""
    from flask import current_app

    from app import admin_stats
    from app.extensions import db, json_given
    from app.models import Response, Survey, SurveyMember, User
    from app.tables import Listing, by_text

    def count(column, *where, join=None):
        query = db.select(db.func.count(column))
        if join is not None:
            query = query.join(*join)
        return query.where(*where).scalar_subquery()

    done = _real(Response.status == Response.COMPLETED)
    # the surveys by real participations (admin page only)
    s_done = count(Response.id, Response.survey_id == Survey.id, *done)
    s_started = count(Response.id, Response.survey_id == Survey.id, *_real())
    s_last = db.select(db.func.max(Response.completed_at)).where(Response.survey_id == Survey.id, *done).scalar_subquery()
    period = request.args.get("period") if request.args.get("period") in admin_stats.PERIODS else admin_stats.DEFAULT
    stats = admin_stats.overview(current_app._get_current_object(), period, admin_stats.read_since(request.args.get("since")))
    w = stats["window"]
    s_period = count(Response.id, Response.survey_id == Survey.id, *done, Response.completed_at >= w["start"])
    member_name = db.func.coalesce(db.func.nullif(User.name, ""), SurveyMember.name)  # the cached name: not signed in yet
    first_member = (db.select(by_text(member_name)).select_from(SurveyMember)
                    .outerjoin(User, User.id == SurveyMember.user_id).where(SurveyMember.survey_id == Survey.id)
                    .order_by(SurveyMember.position, SurveyMember.id).limit(1).scalar_subquery())
    surveys = Listing("b", {
        "title": by_text(Survey.title), "members": first_member,
        "kind": db.case((json_given(Survey.live), 1), (json_given(Survey.interview), 2),
                        (json_given(Survey.sampling), 3), else_=0),
        "status": db.case({Survey.DRAFT: 0, Survey.RUNNING: 1, Survey.CLOSED: 2}, value=Survey.status, else_=3),
        "period": ([s_period, s_done], "desc"),
        "completed": ([s_done, s_started], "desc"), "started": (s_started, "desc"), "last": (s_last, "desc"),
    }, default="period", then=[by_text(Survey.title), Survey.id], anchor="befragungen", sizes=ROW_SIZES, search=[  # like the people (Ralf, 2026-10-17)
        Survey.title, Survey.code,
        lambda match: db.exists().where(SurveyMember.survey_id == Survey.id, match(SurveyMember.name)),
        lambda match: db.exists().where(SurveyMember.survey_id == Survey.id, SurveyMember.user_id == User.id,
                                        match(User.name))])
    # surveys whose admins asked to be left out (setting "admin_hidden") are not listed; the totals count them
    from app import plugins

    counted = plugins.counted_surveys()  # imported analyses of an add-on module are no surveys of the platform
    sids = [row[0] for row in surveys.rows(db.select(Survey.id).where(Survey.admin_hidden.is_(False), *counted))]
    by_id = {s.id: s for s in db.session.scalars(db.select(Survey).where(Survey.id.in_(sids)))}
    counts = {row[0]: row for row in db.session.execute(
        db.select(Survey.id, s_done, s_started, s_last, s_period).where(Survey.id.in_(sids)))}
    members = {}
    for sid, cached, uid, name in db.session.execute(
            db.select(SurveyMember.survey_id, SurveyMember.name, User.id, User.name).select_from(SurveyMember)
            .outerjoin(User, User.id == SurveyMember.user_id).where(SurveyMember.survey_id.in_(sids))
            .order_by(SurveyMember.position, SurveyMember.id)):
        members.setdefault(sid, []).append((name or "ORCID user") if uid else cached)
    ranking = [{"survey": by_id[sid], "completed": counts[sid][1], "started": counts[sid][2], "last": counts[sid][3],
                "period": counts[sid][4], "members": [m for m in members.get(sid, []) if m]} for sid in sids]
    totals = {"surveys": db.session.scalar(db.select(db.func.count(Survey.id)).where(*counted)),
              "running": db.session.scalar(db.select(db.func.count(Survey.id)).where(Survey.status == Survey.RUNNING,
                                                                                     *counted)),
              "completed": db.session.scalar(db.select(db.func.count(Response.id)).where(*done)),
              "hidden": db.session.scalar(db.select(db.func.count(Survey.id)).where(Survey.admin_hidden.is_(True),
                                                                                    *counted))}
    return render_template("admin/surveys.html", totals=totals, ranking=ranking, surveys=surveys, stats=stats, stats_mod=admin_stats,
                           period=w["key"], periods=admin_stats.period_labels())


@bp.route("/platform-erase")
def platform_erase_find():
    """The form "Befragung per Code" of the tab "Befragungen": also a survey its researchers keep out of the list."""
    from app.extensions import db
    from app.models import Survey

    code = "".join(request.args.get("code", "").split()).lower()[:20]
    survey = db.session.scalar(db.select(Survey).filter_by(code=code)) if code else None
    if survey is None:
        flash(de_en("Keine Befragung mit diesem Code.", "No survey with this code."), "error")
        return redirect(url_for("admin.surveys_page", _anchor="plattform-loeschung"))
    return redirect(url_for("admin.platform_erase", survey_id=survey.id))


@bp.route("/surveys/<sid:survey_id>/erase", methods=["GET", "POST"])
def platform_erase(survey_id):
    """Deletion by the platform (app/platform_erase.py): participations of a survey, or the whole survey, with a
    reason that the seal chain keeps. First what was found, then the confirmation; the researchers are told after."""
    from types import SimpleNamespace

    from flask import g

    from app import accounts, audit, platform_erase as pe, seal
    from app.extensions import db
    from app.models import Survey

    survey = db.session.get(Survey, survey_id) or abort(404)
    here = url_for("admin.platform_erase", survey_id=survey.id)
    found = None
    if request.method == "POST":
        action = request.form.get("action")
        reason = pe.clean_reason(request.form.get("reason"))
        terms = request.form.get("terms", "")[:5000]
        if reason is None:
            flash(de_en("Bitte den Grund in einigen Worten angeben (mindestens {n} Zeichen). Er steht danach "
                        "unveränderlich in der Siegelkette.", "Please give the reason in a few words (at least {n} "
                        "characters). It is then recorded permanently in the seal chain.", n=pe.REASON_MIN), "error")
        elif action == "cases":
            responses, entries, missing = pe.find(survey, terms)
            if not responses and not entries:
                flash(de_en("Dazu wurde in dieser Befragung nichts gefunden.",
                            "Nothing was found for this in this survey."), "warning")
            elif request.form.get("confirm", type=int) != len(responses) + len(entries):
                found = {"responses": len(responses), "entries": len(entries), "missing": missing,
                         "terms": terms, "reason": reason}
            else:
                again = accounts.need_fresh(here)
                if again:
                    return again
                people, title, code = pe.recipients(survey), survey.title, survey.code
                n = pe.erase_cases(survey, responses, entries, reason)
                told, not_told = pe.tell(people, title, code, reason, deleted=n)
                g.audit_survey = SimpleNamespace(id=survey.id, code=code)
                audit.done(detail=f"{n} Teilnahmen, {len(entries)} Einträge; Grund: {reason}")
                _told_flash(told, not_told)
                flash(de_en("{n} Teilnahmen und {e} Einträge des getrennten Abschnitts gelöscht; die Siegelkette "
                            "vermerkt den Grund.", "{n} participations and {e} entries of the separated section "
                            "deleted; the seal chain records the reason.", n=n, e=len(entries)), "success")
                return redirect(here)
        elif action == "survey":
            if " ".join(request.form.get("code", "").split()).lower() != survey.code:
                flash(de_en("Der eingegebene Code stimmt nicht. Es wurde nichts gelöscht.",
                            "The code entered does not match. Nothing was deleted."), "error")
            else:
                again = accounts.need_fresh(here)
                if again:
                    return again
                people, title, code, sid = pe.recipients(survey), survey.title, survey.code, survey.id
                pe.take_down(survey, reason)
                told, not_told = pe.tell(people, title, code, reason)
                g.audit_survey = SimpleNamespace(id=sid, code=code)
                audit.done(detail=f"Befragung gelöscht; Grund: {reason}")
                _told_flash(told, not_told)
                flash(de_en("Die Befragung „{t}“ ist gelöscht; ihre Siegelkette endet mit dem Grund.",
                            "The survey “{t}” has been deleted; its seal chain ends with the reason.", t=title), "success")
                return redirect(url_for("admin.surveys_page"))
    rows = [dict(e._mapping) for e in seal.entries(survey.uid)]
    return render_template("admin/platform_erase.html", survey=survey, found=found, people=pe.recipients(survey),
                           chain=seal.summary(survey) if rows else None, earlier=seal.by_platform(rows),
                           reason_max=pe.REASON_MAX, reason_min=pe.REASON_MIN)


def _told_flash(told, not_told):
    """Whom the deletion by the platform reached by e-mail, and whom the admin still has to tell."""
    if told:
        flash(de_en("Per E-Mail informiert: {names}.", "Notified by email: {names}.", names=", ".join(told)), "success")
    if not_told:
        flash(de_en("Ohne E-Mail informiert (keine Adresse am Konto oder E-Mail-Versand aus): {names}. Bitte auf "
                    "anderem Weg informieren.", "Not told by email (no address on the account, or mail is off): "
                    "{names}. Please tell them another way.", names=", ".join(not_told)), "warning")


def _sign_in_list():
    """The section "Anmeldung freischalten": the list, the switch, and what still stands in the configuration."""
    from app import signin
    from app.extensions import db
    from app.models import SignInGrant, SurveyMember, User, WorkGroupMember

    if signin.mode() == signin.MODE_CAP:
        signin.admit_waiting()  # a place that became free goes to the first of the waiting list
    rows = signin.entries()
    # the accounts of the iDs, pending ones too (added to a survey or group before their first sign-in)
    ids = dict(db.session.execute(db.select(User.orcid, User.id).where(User.orcid.in_([row.orcid for row, _u in rows]))).all())
    # who stays in when taken off the list: invited to a survey, in a group, or an admin's yes on the account
    other = set(db.session.scalars(db.select(SurveyMember.user_id).where(SurveyMember.user_id.in_(ids.values()))))
    other |= set(db.session.scalars(db.select(WorkGroupMember.user_id).where(WorkGroupMember.user_id.in_(ids.values()))))
    granted = set(db.session.scalars(db.select(SignInGrant.user_id)))
    return {"rows": [{"entry": row, "user": user, "stays": ids.get(row.orcid) in other or (user is not None and user.id in granted)}
                     for row, user in rows],
            "everyone": signin.everyone(), "leftover": signin.leftover(), "taken_over": signin.setting(signin.TAKEN_OVER),
            # "every ORCID holder" up to a limit (gruppen-freischaltung-konzept.md, Stufe 3)
            "mode": signin.mode(), "limit": signin.limit(), "steps": signin.STEPS, "used": signin.open_count(),
            "level": signin.level(), "waiting": signin.waiting()}


def _account_page(user, link=None):
    from app import accounts, passwords

    from app import experimental

    return render_template("admin/account.html", user=user, login=passwords.login_row(user), link=link,
                           link_open=passwords.open_link(user), has_password=passwords.has_password(user),
                           site_admin=accounts.managed_by_config(user),
                           hours=passwords.LINK_HOURS, hard_locked=passwords.hard_locked(passwords.login_row(user)),
                           hard_limit=passwords.HARD_LIMIT,
                           experiments=[(key, label, experimental.how(key, user)) for key, label, _t in experimental.features()],
                           kinds=_kind_rows(user), **_period_of(user))


def _period_of(user):
    """User type, period and supervisor of the account for its page (app/account_types.py, N5, N10, N13)."""
    from app import account_types, accounts, passwords

    login = passwords.login_row(user)
    info = account_types.summary(login)
    due = account_types.deletion_due(login)
    from app import orgs

    home = orgs.get(orgs.home_id(user))
    return {"period": info, "types": account_types.all_types(org_id=orgs.home_id(user)),
            "home": home, "home_choices": orgs.all_orgs(guests=False) if orgs.several() else [],
            "also_in": [orgs.get(i) for i in sorted(orgs.ids_of(user) - {orgs.home_id(user), orgs.guest_id()})],
            "supervisors": account_types.supervisors_of(user),
            "supervisor_choices": [u for u in _supervisor_choices(user)
                                   if u not in account_types.supervisors_of(user)],
            "deletion_due": due, "open_points": account_types.open_points(accounts.plan(user)) if due else [],
            "describe_spec": account_types.describe}


def _supervisor_choices(user):
    """Who can supervise the account, picked by name (Ralf 2026-10-18: a number nobody remembers): the people of its
    home organisation with an account, without the account itself; the current supervisors stay in the list."""
    from app import account_types, orgs

    home = orgs.get(orgs.home_id(user))
    people = [u for u, _row in orgs.members_of(home)] if home is not None else []
    for current in account_types.supervisors_of(user):
        if current not in people:
            people.append(current)
    return [u for u in people if u.id != user.id]


def _kind_rows(user):
    """The four survey types of the account (app/kind_access.py) for its page: [(type, level, {lower level: what it
    would take away})], the latter for the question before a lower level (konzept F4)."""
    from app import kind_access

    levels = kind_access.full(kind_access.stored(user))
    rows = []
    for kind in kind_access.KINDS:
        lower = {}
        for lvl in (kind_access.VIEW, kind_access.OFF):
            if lvl != levels[kind]:
                hits = kind_access.affected(user, kind, lvl)
                if hits:
                    lower[lvl] = {"n": len(hits), "running": [s.title for s, _r, run, _o in hits if run],
                                  "only": [s.title for s, _r, _run, only in hits if only]}
        rows.append((kind, levels[kind], lower))
    return rows


def _show_link_once(user, link):
    """Post/Redirect/Get for a fresh one-time link (UI audit 2026-10-07, W84): the link waits in the session for the
    next view of the account's page and is shown there once; reloading that page asks nothing and sends nothing
    again. The session cookie is signed, not encrypted: the link (it signs in as the person) goes into it encrypted
    (audit 2026-10-09, A6)."""
    from flask import session

    from app import secrets_box

    session["admin_link"] = {"user": user.id, "link": secrets_box.encrypt(link)}
    return redirect(url_for("admin.account", user_id=user.id))


def _new_link(user):
    """A fresh one-time link for this account. It is shown on the page that follows and nowhere else."""
    from app import passwords
    from app.extensions import db

    if user.has_orcid:
        abort(403)  # ORCID accounts recover through ORCID, not an admin bearer link (AU-6)
    token = passwords.new_link_token(user, current_user)
    row = passwords.login_row(user)
    if row is not None:
        row.reset_requested_at = None  # a request "Passwort vergessen?" is answered by the link
    db.session.commit()
    return brand.public_base() + url_for("auth.set_password", token=token)


def _listed_kinds(orcid):
    """The four survey types of "Person hinzufügen" with ORCID, kept with the iD for its first sign-in (F7); an iD that
    has an account already keeps what its page says."""
    from app.extensions import db
    from app.models import SignInOrcid, User

    if db.session.scalar(db.select(User.id).filter_by(orcid=orcid, pending=False)) is not None:
        return  # a pending account (the iD added somewhere before) takes the list's choice at its first sign-in
    row = db.session.get(SignInOrcid, orcid)
    if row is None:
        row = SignInOrcid(orcid=orcid, note=(request.form.get("note") or "").strip()[:200], added_by_id=current_user.id)
        db.session.add(row)
    from app import account_types

    t = account_types.get(request.form.get("account_type"))
    row.kinds = _kinds_of_form(t)  # explicit, also when all four are "edit"
    row.account_type_id = t.id if t is not None else None
    spec, _error = account_types.spec_from_form(request.form, t)  # checked before (sign_in_orcids)
    row.valid_spec = spec if t is not None else None
    from app import orgs

    org = orgs.get(request.form.get("org"))  # its home organisation at the first sign-in (A21, A22)
    if t is not None and (org is None or account_types.org_of(t) != org.id):
        org = orgs.get(account_types.org_of(t))  # a type belongs to one organisation (2026-10-16)
    row.org_id = org.id if org is not None and org.key not in (orgs.MAIN, orgs.GUEST) else None


def _kinds_of_form(t):
    """The four survey-type selects of "Person hinzufügen": "" (as the user type) takes the type's level."""
    levels = kind_access.full(t.kinds if t is not None else kind_access.default())
    for k in kind_access.KINDS:
        if request.form.get("kind_" + k) in kind_access.LEVELS:
            levels[k] = request.form.get("kind_" + k)
    return levels


@bp.route("/accounts/new", methods=["POST"])
def new_account():
    """An account without ORCID: the admin enters name and address and passes the one-time link on."""
    from app import accounts, passwords
    from app.extensions import db

    again = accounts.need_fresh(url_for("admin.researchers") + "#person-hinzufuegen")
    if again:
        return again
    name = " ".join(request.form.get("name", "").split())[:200]
    email = passwords.normal_email(request.form.get("email"))
    problem = field = None
    if not name or not email:
        problem = de_en("Bitte einen Namen und eine gültige E-Mail-Adresse angeben.", "Please enter a name and a valid email address.")
        field = "name" if not name else "email"
    elif not accounts.email_free(email):
        problem = de_en("Mit dieser E-Mail-Adresse gibt es schon ein Konto.", "There is already an account with this email address.")
        field = "email"
    elif _period_problem():
        problem, field = _period_problem(), "valid_n"
    else:
        from app import account_types

        t = account_types.get(request.form.get("account_type"))
        user = accounts.create_local(name, email, "", False, current_user)
        kind_access.store(user, _kinds_of_form(t), current_user)  # the dialog's four types (F7)
        if request.form.get("may_create") == "1":
            kind_access.give_create(user, current_user)  # the tick: "edit" becomes "create" where the form left "edit"
        # type and period; the period starts when the person sets the password (N4)
        spec, _ = account_types.spec_from_form(request.form, t)
        account_types.assign(user, t, spec, by=current_user, supervisor=current_user, start=False)
        from app import orgs

        org = orgs.get(request.form.get("org"))  # its home organisation (A21)
        if t is not None and (org is None or account_types.org_of(t) != org.id):
            org = orgs.get(account_types.org_of(t))  # a type belongs to one organisation (2026-10-16)
        if org is not None and org.key not in (orgs.MAIN, orgs.GUEST):
            orgs.add(org, user, by=current_user, home=True)
        db.session.commit()
        from app import mail

        if mail.enabled() and request.form.get("send_mail") == "1":
            # the link goes by mail and is not shown (M4a); a new one can be made on the account's page
            passwords.mail_link(user, _new_link(user), reset=False)
            flash(de_en("Konto für {name} angelegt. Der Einmal-Link ist per E-Mail an {email} unterwegs.",
                        "Account for {name} created. The one-time link is on its way to {email} by email.",
                        name=name, email=email), "success")
            return redirect(url_for("admin.account", user_id=user.id))
        flash(de_en("Konto für {name} angelegt.", "Account for {name} created.", name=name), "success")
        return _show_link_once(user, _new_link(user))
    # the form comes back with what was entered and the reason inside it (macro form_error), not empty (W84); the
    # dialog "Person hinzufügen" opens by itself with it, the field in question marked (S10)
    return _refused("new-account", problem, field)


def _period_problem():
    """Why the user type and period of "Person hinzufügen" cannot be taken (a number or date missing), or None."""
    from app import account_types

    t = account_types.get(request.form.get("account_type"))
    if request.form.get("account_type") and t is None:
        return de_en("Diesen Nutzertyp gibt es nicht mehr.", "This user type no longer exists.")
    return account_types.spec_from_form(request.form, t)[1]


def _refused(key, message, field):
    """The page "Personen" once more, with the refused form of the dialog "Person hinzufügen" open: what was entered,
    the reason in the form, the field in question marked (UI/UX audit 2026-10-07, W84 and S10)."""
    from flask import g

    g.form_error = {"key": key, "message": message, "field": field}
    return researchers(), 422


@bp.route("/accounts/<int:user_id>", methods=["GET", "POST"])
@autosave.aware
def account(user_id):
    """One account: may it create surveys, its sign-in address, a new one-time link (also for a forgotten
    password)."""
    from app import accounts, passwords
    from app.extensions import db

    user = accounts.admin_target(user_id)
    if request.method == "POST":
        action = request.form.get("action")
        row = passwords.login_row(user, create=True)
        if action == "may_create":
            # both buttons over the table of types: "create" for every type at "edit", or back to "edit" (the former
            # "darf anlegen" / "darf nicht anlegen"; konzept-stufe-anlegen-2026-10-18.md)
            from app import kind_access

            value = request.form.get("value")
            if value not in ("1", "0"):
                abort(400)
            if value == "1":
                kind_access.give_create(user, current_user)
                passwords.login_row(user, create=True).may_create = None  # no longer an admin's "no"
            else:
                kind_access.take_create(user, current_user)
            db.session.commit()
            passwords._event("may_create_changed", account=user.id, by=current_user.id, kind=value)  # AU-7
            who = user.display_name
            flash(de_en("{who} legt jetzt Befragungen jeder Art an, die auf „Bearbeiten“ stand.",
                        "{who} now creates surveys of every type that was at “Edit”.", who=who) if value == "1" else
                  de_en("{who} legt keine eigenen Befragungen mehr an und bearbeitet weiter, was geteilt ist. Bestehende "
                        "Befragungen bleiben.", "{who} no longer creates their own surveys and keeps editing what is "
                        "shared. Existing surveys stay.", who=who), "success")
            return redirect(url_for("admin.account", user_id=user.id, _anchor="befragungstypen"))
        elif action == "email" and row.email:
            # the address decides who gets a one-time link after "Passwort vergessen?": as fresh a sign-in as for
            # the link itself (audit 2026-10-09, A4)
            again = accounts.need_fresh(url_for("admin.account", user_id=user.id))
            if again:
                return again
            email = passwords.normal_email(request.form.get("email"))
            if not email:
                flash(de_en("Bitte eine gültige E-Mail-Adresse angeben.", "Please enter a valid email address."), "error")
            elif not accounts.email_free(email, user):
                flash(de_en("Mit dieser E-Mail-Adresse gibt es schon ein Konto.",
                            "There is already an account with this email address."), "error")
            else:
                from app import audit

                from app.models import AuthToken, MailOutbox

                db.session.execute(db.delete(AuthToken).where(AuthToken.user_id == user.id,
                                                              AuthToken.purpose == "email_confirm"))
                db.session.execute(db.delete(MailOutbox).where(MailOutbox.sender_id == user.id,
                    MailOutbox.kind == "emailcheck", MailOutbox.sent_at.is_(None)))
                row.email = email
                row.email_verified_at = None
                row.reset_requested_at = None  # an open "Passwort vergessen?" was asked for the old address (A4)
                db.session.commit()
                audit.done(detail=f"user={user.number or user.id}")  # never the address itself (A3)
                flash(de_en("E-Mail-Adresse geändert. Die Person meldet sich ab jetzt mit der neuen Adresse an.",
                            "Email address changed. From now on the person signs in with the new address."), "success")
        elif action == "kind":
            # the level of one survey type (app/kind_access.py, konzept-rechte-befragungstypen-2026-10-15.md)
            from app import kind_access

            kind, lvl = request.form.get("kind", ""), request.form.get("level", "")
            try:
                kind_access.set_level(user, kind, lvl, current_user)
            except ValueError:
                abort(400)
            db.session.commit()
            flash(de_en("{who}: {kind} jetzt „{level}“.", "{who}: {kind} now “{level}”.", who=user.display_name,
                        kind=kind_access.label(kind), level=kind_access.level_label(lvl)), "success")
            return redirect(url_for("admin.account", user_id=user.id, _anchor="befragungstypen"))
        elif action in ("account_type", "extend", "supervisor", "supervisor_remove"):
            return _period_action(user, action)
        elif action == "home":
            return _home_action(user)
        elif action == "experiment":
            from app import experimental

            feature, state = request.form.get("feature", ""), request.form.get("state", "")
            try:
                experimental.set_person(user, feature, state, current_user)
            except ValueError:
                abort(400)
            db.session.commit()
            flash(_experiment_said(experimental.label(feature), user.display_name, state), "success")
            return redirect(url_for("admin.account", user_id=user.id, _anchor="experimentell"))
        elif action == "release":
            # the lock after passwords.HARD_LIMIT wrong passwords or codes in a row (audit 2026-10-11, AU3)
            passwords.release(row)
            db.session.commit()
            from app import security_log

            security_log.event("sign_in_released", account=user.id, by=current_user.id)  # audit 2026-10-13, AU-7
            flash(de_en("Anmeldung freigegeben. Die Zählung der Fehlversuche beginnt neu.",
                        "Sign-in released. The count of failed attempts starts again."), "success")
        elif action == "request_done":
            # the request "Passwort vergessen?" needs no link (the person remembered it, or it was not theirs)
            row.reset_requested_at = None
            db.session.commit()
            flash(de_en("Anfrage als erledigt markiert.", "Request marked as done."), "success")
            if request.form.get("back") == "list":
                return redirect(url_for("admin.researchers"))
        elif action in ("link", "link_mail") and row.email:
            again = accounts.need_fresh(url_for("admin.account", user_id=user.id))  # the link signs in as the person
            if again:
                return again
            from app import mail

            if action == "link_mail":
                if not mail.enabled():
                    abort(400)
                # sent to the address on file only, never shown (M4a)
                passwords.mail_link(user, _new_link(user), reset=False)
                flash(de_en("Neuer Einmal-Link per E-Mail an {email} geschickt.", "New one-time link sent to {email} by email.",
                            email=row.email), "success")
                return redirect(url_for("admin.account", user_id=user.id))
            return _show_link_once(user, _new_link(user))
        else:
            abort(400)
        return redirect(url_for("admin.account", user_id=user.id))
    from flask import session

    from app import secrets_box

    waiting = session.get("admin_link")
    if waiting and waiting.get("user") == user.id:
        session.pop("admin_link")
        link = secrets_box.decrypt(waiting.get("link"))
        if link:
            return _account_page(user, link=link)
    return _account_page(user)


def _home_action(user):
    """Another home organisation for the account (app/orgs.py, move_home; Ralf 2026-10-18)."""
    from app import account_types, accounts, orgs
    from app.extensions import db

    back = redirect(url_for("admin.account", user_id=user.id, _anchor="heimat"))
    again = accounts.need_fresh(url_for("admin.account", user_id=user.id) + "#heimat")  # rights follow the home
    if again:
        return again
    org = orgs.get(request.form.get("org"))
    refused = orgs.move_refusal(user, org)
    if refused:
        flash(refused, "error")
        return back
    before = account_types.of(user)
    t = orgs.move_home(user, org, by=current_user)
    db.session.commit()
    said = de_en("Die Heimat von {who} ist jetzt {o}.", "The home of {who} is now {o}.", who=user.display_name,
                 o=orgs.label(org))
    if before is not None and account_types.name(t) != account_types.name(before):
        said += " " + de_en("Nutzertyp jetzt „{t}“.", "User type now “{t}”.", t=account_types.name(t))
    flash(said, "success")
    return back


def _period_action(user, action):
    """The user type, the period and the supervisor on the account's page (app/account_types.py, N5, N10)."""
    from app import account_types
    from app.extensions import db
    from app.models import User

    back = redirect(url_for("admin.account", user_id=user.id, _anchor="nutzertyp"))
    if action == "account_type":
        t = account_types.get(request.form.get("account_type"))
        if request.form.get("account_type") and t is None:
            abort(400)
        account_types.change_type(user, t, current_user)
        db.session.commit()
        flash(de_en("{who} hat jetzt den Nutzertyp „{t}“; Befragungstypen und Anlegerecht kommen vom Typ, die Laufzeit "
                    "bleibt.", "{who} now has the user type “{t}”; survey types and the right to create come from the "
                    "type, the period stays.", who=user.display_name, t=account_types.name(t)), "success")
        return back
    if action == "extend":
        spec, error = account_types.spec_from_form(request.form, None)
        if error:
            flash(error, "error")
            return back
        account_types.extend(user, spec, current_user)
        db.session.commit()
        from app import lang
        from app.timeutil import fmt

        info = account_types.summary(account_types._row(user))
        what = (de_en("bis {d}", "until {d}", d=fmt(info["until"], with_time=False, lang=lang.current()))
                if info["until"] else account_types.describe(info["pending"] or ""))
        flash(de_en("Laufzeit von {who}: {what}.", "Period of {who}: {what}.", who=user.display_name, what=what),
              "success")
        return back
    # several supervisors (Ralf 2026-10-18): a click in the list adds one ("supervisor"), the × removes one
    # ("supervisor_remove"); an empty choice leaves the list as it is
    back = redirect(url_for("admin.account", user_id=user.id, _anchor="betreuung"))
    chosen = request.form.get("supervisor", type=int)
    current = account_types.supervisors_of(user)
    if request.form.get("action") == "supervisor_remove":
        gone = next((u for u in current if u.id == chosen), None)
        if gone is None:
            return back
        account_types.set_supervisors(user, [u for u in current if u.id != gone.id])
        db.session.commit()
        flash(de_en("{s} betreut {who} nicht mehr.", "{s} no longer supervises {who}.", s=gone.display_name,
                    who=user.display_name), "success")
        return back
    if not chosen:
        return back
    sup = next((u for u in _supervisor_choices(user) if u.id == chosen), None)
    if sup is None:
        flash(de_en("Diese Person kann das Konto nicht betreuen.", "This person cannot supervise the account."), "error")
        return back
    account_types.set_supervisors(user, [*current, sup])
    db.session.commit()
    flash(de_en("{s} betreut jetzt {who}.", "{s} now supervises {who}.", s=sup.display_name, who=user.display_name),
          "success")
    return back


@bp.route("/accounts/<int:user_id>/lock", methods=["POST"])
def block_account(user_id):
    """Keep someone out without touching anything of theirs (and let them in again). The account is signed out
    with its next request (auth.load_user)."""
    from app import accounts
    from app.extensions import db

    user = accounts.admin_target(user_id)
    again = accounts.need_fresh(url_for("admin.researchers"))
    if again:
        return again
    block = request.form.get("action") == "block"
    from app import account_types

    if not block and account_types.is_expired(account_types._row(user)):
        # an ended period: unblocking alone would end again at once; "verlängern" opens it (N12)
        flash(de_en("Die Laufzeit von {who} ist abgelaufen. Bitte unter „Verwalten“ die Laufzeit verlängern; das "
                    "entsperrt das Konto.", "The period of {who} has ended. Please extend the period under “Manage”; "
                    "that unblocks the account.", who=user.display_name), "error")
        return redirect(url_for("admin.account", user_id=user.id, _anchor="nutzertyp"))
    accounts.set_blocked(user, block, current_user)
    db.session.commit()
    if block:
        flash(de_en("{who} ist gesperrt und kann sich nicht mehr anmelden. Befragungen, Designs und Gruppen bleiben, "
                    "wie sie sind.", "{who} is blocked and can no longer sign in. Surveys, designs and groups stay as "
                    "they are.", who=user.display_name), "success")
    else:
        flash(de_en("{who} ist nicht mehr gesperrt.", "{who} is no longer blocked.", who=user.display_name), "success")
    return _back("admin.researchers", "forschende")


@bp.route("/admit", methods=["POST"])
def sign_in_orcids():
    """Who may sign in with ORCID by themselves (app/signin.py): add an iD to the list or take it off, and the
    switch "every ORCID holder". Until 2026-10-06 this was ALLOWED_ORCIDS in the server's configuration."""
    from app import accounts, signin

    action = request.form.get("action")
    back = url_for("admin.researchers") + "#freischalten"
    opening = (action == "everyone" and request.form.get("value") == "1") or (
        action == "mode" and request.form.get("mode") in (signin.MODE_CAP, signin.MODE_ALL))
    if action == "add" or opening:
        # as making an account: a computer left signed in must not be enough; back at the form the person came from
        again = accounts.need_fresh(url_for("admin.researchers") + "#person-hinzufuegen" if action == "add" else back)
        if again:
            return again
    if action == "add":
        orcid = signin.clean(request.form.get("orcid"))
        if not orcid:
            return _refused("allow-orcid", de_en("Das ist keine gültige ORCID iD (0000-0000-0000-0000 oder die Adresse des "
                                                 "Profils).", "This is not a valid ORCID iD (0000-0000-0000-0000 or the "
                                                 "address of the profile)."), "orcid")
        if _period_problem():
            return _refused("allow-orcid", _period_problem(), "valid_n")
        if signin.listed(orcid):
            signin.add(orcid, request.form.get("note", ""), current_user)
            flash(de_en("Diese ORCID iD ist schon freigeschaltet.", "This ORCID iD is already cleared."), "success")
        else:
            ticked = signin.add(orcid, request.form.get("note", ""), current_user)
            if ticked:
                flash(de_en("Freigeschaltet. {who} hat schon ein Konto und darf damit jetzt auch eigene Befragungen "
                            "anlegen (zurücknehmen unter „verwalten“).", "Cleared. {who} already has an account and may now "
                            "also create their own surveys with it (take back under “manage”).", who=ticked.display_name),
                      "success")
            else:
                flash(de_en("Freigeschaltet. Die Person kann sich jetzt mit ORCID anmelden.",
                            "Cleared. The person can now sign in with ORCID."), "success")
        _listed_kinds(orcid)
        from app.extensions import db

        db.session.commit()
    elif action == "kinds_default":
        from app.extensions import db

        kind_access.set_default(kind_access.from_form(request.form), current_user)
        db.session.commit()
        flash(de_en("Befragungstypen für neue Konten übernommen.", "Survey types for new accounts applied."), "success")
        return redirect(url_for("admin.researchers", _anchor="neue-konten-typen"))
    elif action == "remove":
        signin.remove(request.form.get("orcid", ""))
        flash(de_en("Von der Liste entfernt. Wer nicht zu einer Befragung eingeladen oder in einer Gruppe ist, ist mit dem "
                    "nächsten Klick abgemeldet.", "Removed from the list. Anyone not invited to a survey or in a group is "
                    "signed out with their next click."), "success")
    elif action == "mode":
        # three states of "every ORCID holder" (E9); the limit in steps (E11); lowering it locks nobody out
        state = request.form.get("mode")
        top = request.form.get("limit", type=int)
        if state not in (signin.MODE_OFF, signin.MODE_CAP, signin.MODE_ALL) or (
                state == signin.MODE_CAP and top not in signin.STEPS):
            abort(400)
        signin.set_mode(state, current_user, top if state == signin.MODE_CAP else None)
        from app.extensions import db

        db.session.commit()
        moved = signin.admit_waiting() if state == signin.MODE_CAP else 0
        if state == signin.MODE_CAP:
            flash(de_en("Jede Person mit ORCID iD kann sich anmelden, bis {n} Konten so dazugekommen sind; danach kommen "
                        "Neue auf die Warteliste.", "Anyone with an ORCID iD can sign in until {n} accounts have come in "
                        "this way; after that new people go on the waiting list.", n=top)
                  + (" " + de_en("{m} Personen von der Warteliste sind jetzt freigeschaltet.",
                                 "{m} people from the waiting list are now allowed in.", m=moved) if moved else ""),
                  "success")
        elif state == signin.MODE_ALL:
            flash(de_en("Jede Person mit ORCID iD kann sich jetzt anmelden.", "Anyone with an ORCID iD can now sign in."),
                  "success")
        else:
            flash(de_en("Anmelden kann sich wieder nur, wer auf der Liste steht, eingeladen oder in einer Gruppe ist. "
                        "Wer schon ein Konto hat, verliert es nicht, kommt aber nur noch mit einem dieser Gründe hinein.",
                        "Only people on the list, invited or in a group can sign in again. Anyone who has an account already "
                        "keeps it, but only gets in with one of these reasons."), "success")
    elif action == "unwait":
        signin.unwait(request.form.get("orcid", ""))
        flash(de_en("Von der Warteliste genommen.", "Taken off the waiting list."), "success")
    elif action == "everyone":
        on = request.form.get("value") == "1"
        signin.set_everyone(on, current_user)
        from app.extensions import db

        db.session.commit()
        flash(de_en("Jede Person mit ORCID iD kann sich jetzt anmelden.", "Anyone with an ORCID iD can now sign in.") if on else
              de_en("Anmelden kann sich wieder nur, wer auf der Liste steht, eingeladen oder in einer Gruppe ist.",
                    "Only people on the list, invited or in a group can sign in again."), "success")
    return redirect(back)


@bp.route("/accounts/<int:user_id>/delete", methods=["GET", "POST"])
def delete_account(user_id):
    """Delete someone's account: the same page the person would see (app/accounts.py), from the admin's side.
    Surveys with real participations that belong to nobody else go to this admin unless they say otherwise."""
    from app import accounts
    from app.models import SurveyMember

    user = accounts.admin_target(user_id)
    if request.method == "POST":
        again = accounts.need_fresh(url_for("admin.delete_account", user_id=user.id))
        if again:
            return again
        name = user.display_name
        if not accounts.name_matches(user, request.form.get("name")):
            flash(de_en("Der eingegebene Name stimmt nicht. Es wurde nichts gelöscht.",
                        "The name entered does not match. Nothing was deleted."), "error")
            return redirect(url_for("admin.delete_account", user_id=user.id))
        accounts.delete(user, "admin", request.form, admin=current_user._get_current_object())
        flash(de_en("Das Konto von {name} wurde gelöscht.", "The account of {name} has been deleted.", name=name), "success")
        return redirect(url_for("admin.researchers"))
    return render_template("accounts/delete.html", user=user, plan=accounts.plan(user), admin=True, fresh=True,
                           role_names=SurveyMember.roles())


@bp.route("/groups")
def groups():
    """The tab "Gruppen": all groups, out of "Personen" again (Ralf 2026-10-10)."""
    return render_template("admin/groups.html", gr=_groups_part())


def _groups_part():
    """All groups (app/groups.py): to their members only they are visible otherwise. Names are free and not unique,
    so an admin can rename or delete a group. The tab "Gruppen" (admin/groups.html, admin/_groups.html)."""
    from app.extensions import db
    from app.models import WorkGroup, WorkGroupMember
    from app.tables import Listing, by_text

    listing = Listing("g", {
        "name": by_text(WorkGroup.name), "created": (WorkGroup.created_at, "desc"),
        "members": (db.select(db.func.count(WorkGroupMember.id)).where(WorkGroupMember.group_id == WorkGroup.id)
                    .scalar_subquery(), "desc"),
    }, default="name", then=[WorkGroup.id], anchor="gruppen", sizes=ROW_SIZES,
        search=[WorkGroup.name, _member_match(WorkGroup)])
    from app import orgs

    base = db.select(WorkGroup.id)
    org = orgs.get(request.args.get("g_org"))
    if org is not None:  # the groups of one organisation (from the tab "Organisationen")
        base = base.where(orgs.column(WorkGroup.org_id) == org.id)
    ids = [row[0] for row in listing.rows(base)]
    by_id = {g.id: g for g in db.session.scalars(db.select(WorkGroup).where(WorkGroup.id.in_(ids)))}
    from flask import session

    from app import joinlinks

    held = session.get(joinlinks.SHOW_KEY)
    shown = db.session.get(WorkGroup, held.get("group")) if isinstance(held, dict) else None
    new_link = {"group": shown, "link": joinlinks.take_shown(shown)} if shown is not None else None
    return {"groups": [by_id[i] for i in ids], "listing": listing, "new_link": new_link,
            "lead_links": joinlinks.open_lead_links(ids), "org": org,
            "org_options": [(o.id, orgs.label(o)) for o in orgs.all_orgs(guests=False)] if orgs.several() else [],
            "org_names": orgs.names() if orgs.several() else None}


def _member_match(group, *where):
    """For the search of a list of groups: a member (with `where`: e.g. only the leads) by name or ORCID iD."""
    from app.extensions import db
    from app.models import User, WorkGroupMember

    def condition(match):
        return (db.select(WorkGroupMember.id).outerjoin(User, User.id == WorkGroupMember.user_id)
                .where(WorkGroupMember.group_id == group.id, *where,
                       db.or_(match(User.name), match(WorkGroupMember.name), match(User.orcid))).exists())

    return condition


@bp.route("/groups/<int:group_id>", methods=["POST"])
@autosave.aware
def group_action(group_id):
    from app import groups as groups_mod
    from app.extensions import db
    from app.models import WorkGroup

    group = db.session.get(WorkGroup, group_id)
    if group is None:
        abort(404)
    if request.form.get("action") == "delete":
        from app import audit

        name = group.name
        groups_mod.remove(group)
        db.session.commit()
        audit.done(detail=name[:40])  # its members lose their access (audit 2026-10-09, A3)
        flash(de_en("Gruppe „{name}“ gelöscht. Geteilte Befragungen und Designs bleiben bei den Personen, denen sie gehören.",
                    "Group “{name}” deleted. Shared surveys and designs stay with their owners.", name=name), "success")
    elif request.form.get("action") == "course":
        # a course (Lehrveranstaltung, app/account_types.py, N7): new accounts that come in through the group take
        # this user type, valid until the course ends
        from datetime import date

        from app import account_types
        from app.timeutil import end_of_local_day_utc, local_today

        t = account_types.get(request.form.get("account_type"))
        if t is None:
            group.account_type_id, group.course_until = None, None
            flash(de_en("„{name}“ ist keine Lehrveranstaltung mehr; neue Konten bekommen den Standard.",
                        "“{name}” is no longer a course; new accounts get the default.", name=group.name), "success")
        else:
            try:
                until = date.fromisoformat(request.form.get("course_until") or "")
            except ValueError:
                until = None
            if until is not None and until < local_today():
                until = None
            if until is None and account_types.needs_input(t.duration):
                flash(de_en("Bitte ein Enddatum der Lehrveranstaltung ab heute angeben.",
                            "Please give an end date of the course from today on."), "error")
                return _back("admin.groups", "gruppen")
            group.account_type_id = t.id
            group.course_until = end_of_local_day_utc(until) if until is not None else None
            flash(de_en("„{name}“ ist eine Lehrveranstaltung: Neue Konten über die Gruppe bekommen den Nutzertyp „{t}“ ({d}).",
                        "“{name}” is a course: new accounts through the group get the user type “{t}” ({d}).",
                        name=group.name, t=account_types.name(t),
                        d=account_types.describe(account_types.course_spec(group))), "success")
        db.session.commit()
        from app import security_log

        security_log.event("course_changed", target=f"group {group.id}", by=current_user.id,
                           kind=(t.key or str(t.id)) if t is not None else "none")
    elif request.form.get("action") == "kinds_preset":
        # the survey types new accounts take over once through this group (app/kind_access.py, F9); explicit, also
        # when all four are "edit"
        group.kinds_preset = None if request.form.get("none") == "1" else kind_access.full(kind_access.from_form(request.form))
        db.session.commit()
        from app import security_log

        security_log.event("kinds_changed", target=f"group {group.id}", by=current_user.id,
                           kind=kind_access.encode(group.kinds_preset) or ("all" if group.kinds_preset else "none"))
        flash(de_en("Vorgabe der Gruppe „{name}“ übernommen. Sie gilt für Konten, die über die Gruppe neu dazukommen.",
                    "Preset of the group “{name}” applied. It applies to accounts that newly join through the group.",
                    name=group.name) if group.kinds_preset else
              de_en("Die Gruppe „{name}“ hat keine Vorgabe mehr; neue Konten bekommen den Standard.",
                    "The group “{name}” no longer has a preset; new accounts get the default.", name=group.name), "success")
    elif request.form.get("action") == "may_create":
        group.may_create = request.form.get("value") == "1"
        kind_access.group_allowed(group, current_user)
        db.session.commit()
        from app import security_log

        security_log.event("may_create_changed", target=f"group {group.id}", by=current_user.id,  # AU-7
                           kind="1" if group.may_create else "0")
        flash(de_en("Die Mitglieder der Gruppe „{name}“ haben jetzt „Anlegen“ bei jeder Art, die bei ihnen auf „Bearbeiten“ "
                    "stand, und wer neu dazukommt, bekommt es auch. Wer dazugehört, entscheidet die Leitung der Gruppe.",
                    "The members of the group “{name}” now have “Create” for every type that was at “Edit” for them, "
                    "and whoever joins gets it as well. The group's lead decides who belongs to it.", name=group.name)
              if group.may_create else
              de_en("Wer jetzt neu in die Gruppe „{name}“ kommt, bekommt kein „Anlegen“ mehr. Die Mitglieder behalten es; "
                    "zurücksetzen lässt es sich am Konto.", "Whoever newly joins the group “{name}” no longer gets "
                    "“Create”. The members keep it; it can be reset on the account.", name=group.name),
              "success")
    else:
        from app import orgs

        name = groups_mod._name(request.form.get("name"))
        if not name:
            flash(de_en("Bitte einen Namen angeben.", "Please enter a name."), "error")
        elif orgs.group_name_taken(name, group.org_id, without=group.id):
            flash(orgs.name_taken_said(name), "error")
        else:
            group.name = name
            db.session.commit()
            flash(de_en("Name gespeichert.", "Name saved."), "success")
    return _back("admin.groups", "gruppen")


def _lead_from_outside(text, name=""):
    """The person a site admin names as lead of a group: an ORCID iD (also as the address of the profile) or the
    e-mail address or key of an existing account. Returns (orcid, account or None, name) or a message why not
    (gruppen-freischaltung-konzept.md, E2: the lead need not have an account yet)."""
    from app import accounts, orcid_api, passwords, signin
    from app.extensions import db
    from app.models import User, UserLogin

    text = (text or "").strip()
    orcid = signin.clean(text)
    user = None
    if orcid:
        user = db.session.scalar(db.select(User).filter_by(orcid=orcid, pending=False))
    elif accounts.local_account(text) is not None:
        user = accounts.local_account(text)
    elif passwords.normal_email(text):
        user = db.session.scalar(db.select(User).join(UserLogin, UserLogin.user_id == User.id)
                                 .filter(UserLogin.email == passwords.normal_email(text)))
        if user is None:
            return de_en("Zu dieser E-Mail-Adresse gibt es kein Konto. Bitte die ORCID iD der Person angeben oder zuerst "
                         "ein Konto anlegen.", "There is no account with this email address. Please give the person's "
                         "ORCID iD or create an account first.")
    else:
        return de_en("Bitte eine ORCID iD (Form 0000-0000-0000-0000) oder die E-Mail-Adresse eines Kontos angeben.",
                     "Please give an ORCID iD (form 0000-0000-0000-0000) or the email address of an account.")
    if user is not None:
        return user.orcid, user, user.name or " ".join((name or "").split())[:200]
    name = " ".join((name or "").split())[:200]
    if not name:
        found = orcid_api.lookup(orcid)
        if not found:
            return de_en("Diese ORCID iD gibt es nicht. Bitte die 16 Ziffern prüfen (Form 0000-0000-0000-0000).",
                         "This ORCID iD does not exist. Please check the 16 digits (form 0000-0000-0000-0000).")
        name = found["name"]
    return orcid, None, name


def _make_lead(group, found):
    """Put the person `_lead_from_outside` found into the group as lead, or make the member lead. Returns a message
    if that is not possible, else None; commits."""
    from app import groups as groups_mod, invites, people_keys
    from app.extensions import db
    from app.models import WorkGroupMember

    orcid, user, name = found
    person = user or people_keys.account(orcid, name)  # a pending account for an iD without one
    member = next((m for m in group.members if m.user_id == person.id), None)
    if member is None:
        if len(group.members) >= groups_mod.MAX_MEMBERS:
            return de_en("Mehr als {n} Mitglieder je Gruppe sind nicht möglich.",
                         "More than {n} members per group are not possible.", n=groups_mod.MAX_MEMBERS)
        if groups_mod.count_for(person.id) >= groups_mod.MAX_GROUPS:
            return de_en("Diese Person ist schon in {n} Gruppen; mehr sind nicht möglich.",
                         "This person is already in {n} groups; more are not possible.", n=groups_mod.MAX_GROUPS)
        member = WorkGroupMember(user=person, name=name, role="lead", invited_by_id=current_user.id)
        group.members.append(member)
        kind_access.joined(group, user, current_user)  # "darf Befragungen anlegen": "create" once (E3)
    member.role = "lead"
    db.session.commit()
    invites.notice(user, current_user, group=group, role="lead")  # a mail, if switched on (M5a)
    return None


def _lead_link(group):
    """A lead link for this group (app/joinlinks.py, E2): used once, LEAD_DAYS days; shown once on the next view of
    the panel. Returns a message if it could not be made."""
    from app import joinlinks

    link, said = joinlinks.create(group, current_user, days=joinlinks.LEAD_DAYS, max_uses=1, role="lead")
    if said:
        return said
    joinlinks.show_once(group, link)
    return None


@bp.route("/groups/new", methods=["POST"])
def group_new():
    """A group made by a site admin who does not become a member of it, with its first lead
    (gruppen-freischaltung-konzept.md, Ralf 2026-10-10, E1 and E2). "Mitglieder dürfen eigene Befragungen anlegen" is
    ticked at first (E4): its lead then lets in people who may create surveys, through the tick only a site admin
    sets. A lead hands out the group's rights: fresh sign-in, as for the other rights actions (V5)."""
    from app import accounts, groups as groups_mod
    from app.extensions import db
    from app.models import WorkGroup

    again = accounts.need_fresh(url_for("admin.groups", _anchor="gruppen"))
    if again:
        return again
    name = groups_mod._name(request.form.get("name"))
    if not name:
        flash(de_en("Bitte einen Namen für die Gruppe angeben.", "Please give the group a name."), "error")
        return _back("admin.groups", "gruppen")
    from app import orgs

    if orgs.group_name_taken(name, orgs.for_new(current_user)):
        flash(orgs.name_taken_said(name), "error")
        return _back("admin.groups", "gruppen")
    by_link = not (request.form.get("lead") or "").strip() and bool(request.form.get("lead_link"))
    found = None if by_link else _lead_from_outside(request.form.get("lead"), request.form.get("lead_name"))
    if isinstance(found, str):
        flash(found, "error")
        return _back("admin.groups", "gruppen")
    group = WorkGroup(name=name, description=(request.form.get("description") or "").strip()[:groups_mod.MAX_DESCRIPTION],
                      created_by_id=current_user.id, may_create=bool(request.form.get("members_create")))
    db.session.add(group)
    if by_link:
        db.session.commit()
        _lead_link(group)
        flash(de_en("Gruppe „{g}“ angelegt. Schicken Sie den Leitungslink der Person, die sie leiten soll; wer ihn zuerst "
                    "öffnet und sich anmeldet, leitet die Gruppe.", "Group “{g}” created. Send the lead link to the person "
                    "who is to lead it; the first person who opens it and signs in leads the group.", g=name), "success")
        return _back("admin.groups", "gruppen")
    refused = _make_lead(group, found)
    if refused:
        db.session.rollback()
        flash(refused, "error")
        return _back("admin.groups", "gruppen")
    flash(de_en("Gruppe „{g}“ angelegt; {name} leitet sie. Sie selbst sind nicht Mitglied.",
                "Group “{g}” created; {name} leads it. You are not a member yourself.", g=name,
                name=found[2] or found[0]), "success")
    return _back("admin.groups", "gruppen")


@bp.route("/groups/<int:group_id>/lead", methods=["POST"])
def group_lead(group_id):
    """A member becomes lead of the group, e.g. when no lead can be reached any more (rechte-uebersicht-konzept.md
    H7), or a person from outside does, by ORCID iD or the address of an account (gruppen-freischaltung-konzept.md,
    E3). Only leads: site admins do not add or remove members, that stays with the group (Ralf, 2026-10-09). A lead
    hands out the group's rights: as fresh a sign-in as the other rights actions (audit 2026-10-09b, V5)."""
    from app import accounts
    from app.extensions import db
    from app.models import WorkGroup

    group = db.session.get(WorkGroup, group_id)
    if group is None:
        abort(404)
    again = accounts.need_fresh(url_for("admin.groups", _anchor="gruppen"))
    if again:
        return again
    if request.form.get("action") == "revoke_links":  # a lead link sent to the wrong person (audit 2026-10-10, A5)
        from app import joinlinks

        gone = joinlinks.revoke_lead_links(group)
        db.session.commit()
        flash(de_en("{n} Leitungslink(s) für „{g}“ widerrufen; sie gelten nicht mehr.",
                    "{n} lead link(s) for “{g}” revoked; they no longer work.", n=gone, g=group.name), "success")
        return _back("admin.groups", "gruppen")
    if request.form.get("action") == "link":
        said = _lead_link(group)
        flash(said or de_en("Leitungslink für „{g}“ erzeugt.", "Lead link for “{g}” created.", g=group.name),
              "error" if said else "success")
        return _back("admin.groups", "gruppen")
    if (request.form.get("lead") or "").strip():
        found = _lead_from_outside(request.form.get("lead"), request.form.get("lead_name"))
        refused = found if isinstance(found, str) else _make_lead(group, found)
        if refused:
            flash(refused, "error")
        else:
            flash(de_en("{name} leitet jetzt die Gruppe „{g}“.", "{name} now leads the group “{g}”.",
                        name=found[2] or found[0], g=group.name), "success")
        return _back("admin.groups", "gruppen")
    member = next((m for m in group.members if m.id == request.form.get("member_id", type=int)), None)
    if member is None:
        flash(de_en("Bitte ein Mitglied der Gruppe wählen.", "Please choose a member of the group."), "error")
    elif member.role != "lead":
        member.role = "lead"
        db.session.commit()
        flash(de_en("{name} leitet jetzt die Gruppe „{g}“.", "{name} now leads the group “{g}”.", name=member.display_name,
                    g=group.name), "success")
    return _back("admin.groups", "gruppen")


def _experiment_said(label, who, state):
    said = {"on": ("„{label}“ ist jetzt für {who} eingeschaltet.", "“{label}” is now switched on for {who}."),
            "off": ("„{label}“ ist für {who} nicht mehr persönlich eingeschaltet. Gruppen und „für alle“ gelten weiter.",
                    "“{label}” is no longer switched on for {who} personally. Groups and “for everyone” still apply."),
            "blocked": ("{who} ist von „{label}“ ausgeschlossen, auch über Gruppen und „für alle“. Befragungen, die die "
                        "Funktion schon nutzen, laufen weiter.", "{who} is kept out of “{label}”, also through groups and "
                        "“for everyone”. Surveys that already use the feature keep running.")}[state]
    return de_en(*said, label=label, who=who)


@bp.route("/experimental", methods=["GET"])
def experiments():
    """Experimental features (app/experimental.py): per feature, on for everyone?, the persons, groups and
    organisations it is switched on for, and the persons kept out; switched on and off here and on the admin page of an
    account."""
    from app import experimental, orgs
    from app.extensions import db
    from app.models import User, WorkGroup

    people = sorted(db.session.scalars(db.select(User).where(User.pending.is_(False))), key=lambda u: (u.display_name.casefold(), u.id))
    groups = sorted(db.session.scalars(db.select(WorkGroup)), key=lambda w: (w.name.casefold(), w.id))
    organisations = [o for o in orgs.all_orgs(guests=False)]  # not "Gäste": people there belong to no organisation
    features = [{"key": key, "label": label, "text": text, **experimental.overview(key)}
                for key, label, text in experimental.features()]
    return render_template("admin/experiments.html", features=features, people=people, groups=groups,
                           organisations=organisations)


@bp.route("/experimental", methods=["POST"])
def experiments_change():
    """On or off for everyone, a group, an organisation or a person, or a person kept out."""
    from app import experimental, orgs
    from app.extensions import db
    from app.models import Organisation, User, WorkGroup

    feature, target, action = request.form.get("feature", ""), request.form.get("target", ""), request.form.get("action", "")
    if feature not in experimental.KEYS:
        abort(400)
    label = experimental.label(feature)
    kind, _, ident = target.partition(":")
    if kind == "all" and action in ("on", "off"):
        experimental.set_everyone(feature, action == "on", current_user)
        message = (de_en("„{label}“ ist jetzt für alle eingeschaltet; ausgeschlossene Personen bleiben ausgeschlossen.",
                         "“{label}” is now switched on for everyone; people kept out stay kept out.", label=label)
                   if action == "on" else de_en("„{label}“ gilt nicht mehr für alle.", "“{label}” no longer applies to everyone.",
                                                label=label))
    elif kind == "g" and action in ("on", "off"):
        group = db.session.get(WorkGroup, int(ident) if ident.isdigit() else 0)
        if group is None:
            abort(404)
        experimental.set_group(group, feature, action == "on", current_user)
        message = (de_en("„{label}“ ist jetzt für die Gruppe „{name}“ eingeschaltet.",
                         "“{label}” is now switched on for the group “{name}”.", label=label, name=group.name) if action == "on"
                   else de_en("„{label}“ gilt nicht mehr für die Gruppe „{name}“.",
                              "“{label}” no longer applies to the group “{name}”.", label=label, name=group.name))
    elif kind == "o" and action in ("on", "off"):
        org = db.session.get(Organisation, int(ident) if ident.isdigit() else 0)
        if org is None or org.id == orgs.guest_id():
            abort(404)
        experimental.set_org(org, feature, action == "on", current_user)
        message = (de_en("„{label}“ ist jetzt für die Organisation „{name}“ eingeschaltet.",
                         "“{label}” is now switched on for the organisation “{name}”.", label=label, name=org.name)
                   if action == "on" else de_en("„{label}“ gilt nicht mehr für die Organisation „{name}“.",
                                                "“{label}” no longer applies to the organisation “{name}”.", label=label,
                                                name=org.name))
    elif kind == "u" and action in ("on", "off", "blocked"):
        user = db.session.get(User, int(ident) if ident.isdigit() else 0)
        if user is None:
            abort(404)
        experimental.set_person(user, feature, action, current_user)
        message = _experiment_said(label, user.display_name, action)
    else:
        flash(de_en("Ausschließen geht nur für einzelne Personen.", "Only single people can be kept out.") if kind in ("g", "o")
              else de_en("Bitte eine Person, Gruppe oder Organisation auswählen.",
                         "Please choose a person, group or organisation."), "error")
        return redirect(url_for("admin.experiments", _anchor=f"exp-{feature}"))
    db.session.commit()
    flash(message, "success")
    return redirect(url_for("admin.experiments", _anchor=f"exp-{feature}"))


@bp.route("/designs")
def designs():
    """The former tab "RWTH-Design" (Ralf, 2026-10-07), later a section of "Organisationen": since 2026-10-19 every
    organisation has its own design, and its template lies on the page of the organisation."""
    return redirect(url_for("admin.organisations"))


@bp.route("/templates", methods=["GET"])
def themes():
    """The former tab "Einrichtungen" (then "RWTH-Design"): see designs()."""
    return redirect(url_for("admin.organisations"))


def _template_sides(org):
    from app import org_designs

    return [{"mode": mode, "label": org_designs.mode_label(mode), "template": org_designs.get(org, mode, template=True),
             "design": org_designs.get(org, mode)} for mode in org_designs.MODES]


@bp.route("/orgs/<int:org_id>/template/<any(light, dark):mode>.json")
def organisation_template_file(org_id, mode):
    """One side of the template as a design file."""
    from app import org_designs, orgs
    from app.org_admin import _design_download

    org = orgs.get(org_id)
    if org is None or org.key == orgs.GUEST:
        abort(404)
    return _design_download(org_designs.get(org, mode, template=True), f"{org.name}-{mode}-template")


@bp.route("/orgs/<int:org_id>/template", methods=["POST"])
def organisation_template(org_id):
    """"Design der Einrichtung" (Ralf 2026-10-19): site admins upload a design file per side as the template of the
    organisation design, a starting point only. Where the organisation has no design of that side yet, the template
    becomes it; what its admins made is never replaced (they see "Vorlage übernehmen")."""
    from app import org_designs, orgs, security_log
    from app.extensions import db

    org = orgs.get(org_id)
    if org is None or org.key == orgs.GUEST:
        abort(404)
    back = redirect(url_for("admin.organisation_page", org_id=org.id, _anchor="design"))
    mode = request.form.get("mode")
    if mode not in org_designs.MODES:
        abort(400)
    if request.form.get("action") == "remove":
        org_designs.put(org, mode, None, template=True)
        db.session.commit()
        security_log.event("organisation_template", by=current_user.id, target=f"org {org.id}", kind=f"removed {mode}")
        flash(de_en("Vorlage ({side}) entfernt. Das Organisationsdesign bleibt, wie es ist.",
                    "Template ({side}) removed. The organisation design stays as it is.",
                    side=org_designs.mode_label(mode)), "success")
        return back
    found, why = org_designs.read_upload(request.files.get("file"), mode)
    if found is None:
        flash(why, "error")
        return back
    had = org_designs.get(org, mode) is not None
    org_designs.put(org, mode, found, template=True)
    if not had:
        org_designs.put(org, mode, found)
    db.session.commit()
    security_log.event("organisation_template", by=current_user.id, target=f"org {org.id}", kind=f"upload {mode}")
    if had:
        flash(de_en("Vorlage ({side}) gespeichert. Die Organisation hat schon ein eigenes Design; ihre Admins sehen „Vorlage "
                    "übernehmen“.", "Template ({side}) saved. The organisation already has a design of its own; its admins "
                    "see “Apply template”.", side=org_designs.mode_label(mode)), "success")
    else:
        flash(de_en("Vorlage ({side}) gespeichert und als Organisationsdesign übernommen; die Admins der Organisation "
                    "können es ändern.", "Template ({side}) saved and applied as organisation design; the organisation's "
                    "admins can change it.", side=org_designs.mode_label(mode)), "success")
    return back


@bp.route("/orgs")
def organisations():
    """The tab "Organisationen" as a table (variant A of the concept of 2026-10-18, Ralf): a row per organisation with
    accounts, invited iDs, homes, groups, surveys and admins; searched, sorted
    and cut into 10/20/50 rows by the database (app/tables.py). Names, admins, limits and deleting are on the page
    of the organisation (organisation_page). The guests are a line under the table. Groups become organisations
    from the tab "Gruppen" (A23)."""
    from app import orgs
    from app.extensions import db
    from app.models import Organisation, OrganisationMember, Survey, User, WorkGroup
    from app.tables import ROW_SIZES, Listing, by_text

    n_groups = (db.select(db.func.count(WorkGroup.id)).where(orgs.column(WorkGroup.org_id) == Organisation.id)
                .scalar_subquery())
    n_surveys = (db.select(db.func.count(Survey.id)).where(orgs.column(Survey.org_id) == Organisation.id)
                 .scalar_subquery())
    listing = Listing("o", {"name": by_text(Organisation.name), "groups": (n_groups, "desc"),
                            "surveys": (n_surveys, "desc")},
                      default="name", then=[Organisation.id], anchor="organisationen", sizes=ROW_SIZES, search=[
        Organisation.name, Organisation.long_name, Organisation.description,
        lambda match: db.exists().where(OrganisationMember.org_id == Organisation.id, OrganisationMember.role == "admin",
                                        db.exists().where(User.id == OrganisationMember.user_id,
                                                          db.or_(match(User.orcid), match(User.name))))])
    guest = orgs.get(orgs.guest_id())
    base = db.select(Organisation.id)
    if guest is not None:
        base = base.where(Organisation.id != guest.id)
    ids = [r[0] for r in listing.rows(base)]
    known = {o.id: o for o in db.session.scalars(db.select(Organisation).where(Organisation.id.in_(ids)))}
    rows = [{"org": o, "counts": orgs.counts(o), "admins": [m for m in o.members if m.role == "admin" and orgs.valid(m)]}
            for o in (known[i] for i in ids)]
    return render_template("admin/orgs.html", rows=rows, t=listing, main_key=orgs.MAIN, guest=guest,
                           guest_counts=orgs.counts(guest) if guest is not None else None)


@bp.route("/orgs/<int:org_id>", methods=["GET"])
def organisation_page(org_id):
    """One organisation (variant A, Ralf 2026-10-18): names, the template of its design ("Design der Einrichtung"), admins (also by
    ORCID iD or the address of an account, who needs no account yet), limits and switches, links to its people and
    management, delete when empty. Everything saves itself, as on the tab before."""
    from app import orgs

    org = orgs.get(org_id)
    if org is None or org.key == orgs.GUEST:
        abort(404)
    admins = [m for m in org.members if m.role == "admin" and orgs.valid(m)]
    from flask import session

    removed = session.pop("org_removed", None)  # an iD just taken out (organisation_action, "unadmin"), shown once
    removed = removed if isinstance(removed, dict) and removed.get("org") == org.id else None
    return render_template("admin/org.html", o=org, counts=orgs.counts(org), admins=admins, removed=removed,
                           members=orgs.members_of(org), main_key=orgs.MAIN, template_sides=_template_sides(org))


@bp.route("/orgs/<int:org_id>", methods=["POST"])
@autosave.aware
def organisation_action(org_id):
    from app import orgs
    from app.extensions import db
    from app.models import User

    org = orgs.get(org_id)
    if org is None:
        abort(404)
    page = url_for("admin.organisation_page", org_id=org.id)  # its own page (variant A, Ralf 2026-10-18)
    back = redirect(page)
    action = request.form.get("action")
    from app import security_log

    if action == "rename":
        name = " ".join((request.form.get("name") or "").split())[:orgs.MAX_NAME]
        if not name:
            flash(de_en("Bitte einen Namen angeben.", "Please give a name."), "error")
            return back
        org.name = name
        org.long_name = " ".join((request.form.get("long_name") or "").split())[:200]
        org.description = (request.form.get("description") or "").strip()[:500]
        db.session.commit()
        flash(de_en("Organisation „{n}“ gespeichert.", "Organisation “{n}” saved.", n=name), "success")
        return back
    if action in ("admin", "unadmin"):
        again = accounts_need_fresh(page)
        if again:
            return again
        from app import people_keys

        person, shown = None, ""  # the account, a pending one too (an iD entered before its first sign-in)
        if action == "admin" and (request.form.get("who") or "").strip():
            # by ORCID iD or the address of an account, as for a new organisation; needs no account yet (2026-10-18)
            found = _lead_from_outside(request.form.get("who"), request.form.get("who_name"))
            if isinstance(found, str):
                flash(found, "error")
                return back
            person, shown = found[1] or people_keys.account(found[0], found[2]), found[2] or found[0]
        elif request.form.get("user_id", type=int) or request.form.get("orcid"):
            person = (db.session.get(User, request.form.get("user_id", type=int)) if request.form.get("user_id", type=int)
                      else db.session.scalar(db.select(User).filter_by(orcid=request.form.get("orcid"))))
            if action == "admin" and (person is None or person.pending or not orgs.is_member(person, org.id)):
                flash(de_en("Bitte eine Person dieser Organisation wählen.",
                            "Please choose a person of this organisation."), "error")
                return back
            shown = "" if person is None else orgs.display_name(person.orcid) if person.pending else person.display_name
        row = orgs.row_of(org.id, person.id) if person is not None else None
        if action == "unadmin" and (row is None or row.role != "admin"):
            flash(de_en("Diese Person ist kein Admin der Organisation.", "This person is no admin of the organisation."),
                  "error")
            return back
        if person is None:
            flash(de_en("Bitte eine Person wählen oder eine ORCID iD angeben.", "Please choose a person or give an ORCID iD."),
                  "error")
            return back
        user = person if not person.pending else None
        if action == "admin":
            orgs.add(org, person, role="admin", by=current_user)
            said = de_en("{n} ist jetzt Admin der Organisation „{o}“.", "{n} is now an admin of the organisation “{o}”.",
                         n=shown, o=org.name)
        elif user is None:  # an iD without an account (entered for a new organisation): the entry goes as a whole,
            # else it stayed a membership no list shows (Ralf 2026-10-19); "Rückgängig" enters the iD again
            from flask import session

            shown = orgs.display_name(person.orcid)
            session["org_removed"] = {"org": org.id, "orcid": person.orcid, "name": shown}
            db.session.delete(row)
            orgs.forget()
            said = de_en("{n} ist nicht mehr Admin und nicht mehr in der Organisation „{o}“.",
                         "{n} is no longer an admin of the organisation “{o}” nor in it.", n=shown, o=org.name)
        else:
            row.role = "member"
            said = de_en("{n} ist nicht mehr Admin der Organisation „{o}“.",
                         "{n} is no longer an admin of the organisation “{o}”.", n=user.display_name, o=org.name)
        db.session.commit()
        security_log.event("organisation_admin", account=user.id if user else None, by=current_user.id, target=f"org {org.id}",
                           role="admin" if action == "admin" else "member" if user is not None else "removed")
        flash(said, "success")
        return back
    if action == "caps":
        # the limits its admins work within and the switches for invitations across the fence (A10, A16, A17)
        again = accounts_need_fresh(page)
        if again:
            return again
        from app import account_types

        org.kinds_cap = kind_access.cap_from_form(request.form)
        org.may_create = False if request.form.get("no_create") == "1" else None
        try:
            n = int(request.form.get("max_accounts") or 0)
        except ValueError:
            n = 0
        org.max_accounts = n if n > 0 else None
        scope = request.form.get("invite_scope")
        if scope in orgs.SCOPES:
            org.invite_scope = scope
            org.invite_out = scope == "create"  # the former switch, kept in step for code that still reads it
        org.invite_in = request.form.get("invite_in") == "1"
        org.basis = (request.form.get("basis") or "").strip()[:300]
        db.session.commit()
        security_log.event("organisation_caps", by=current_user.id, target=f"org {org.id}",
                           kind=",".join(f"{k}={v}" for k, v in (org.kinds_cap or {}).items()) or "all")
        flash(de_en("Grenzen der Organisation „{o}“ gespeichert.", "Limits of the organisation “{o}” saved.", o=org.name),
              "success")
        return back
    if action == "delete":
        refused = orgs.delete(org)
        if refused:
            flash(refused, "error")
            return back
        db.session.commit()
        security_log.event("organisation_deleted", by=current_user.id, target=f"org {org_id}")
        flash(de_en("Organisation gelöscht.", "Organisation deleted."), "success")
        return redirect(url_for("admin.organisations"))
    abort(400)


@bp.route("/orgs/new", methods=["POST"])
def organisation_new():
    """A new organisation without a group (Ralf 2026-10-18): a name, optionally the full name and a description, and
    its first admin by ORCID iD or the address of an account (who needs no account yet, as the lead of a new group).
    The admin becomes a member, the home stays where it is ("Heimat ändern" under Forschende moves it). Fresh
    sign-in, as for the other rights actions."""
    from app import orgs, security_log
    from app.extensions import db

    back = redirect(url_for("admin.organisations", _anchor="organisationen"))
    again = accounts_need_fresh(url_for("admin.organisations", _anchor="organisationen"))
    if again:
        return again
    name = " ".join((request.form.get("name") or "").split())[:orgs.MAX_NAME]
    if not name:
        flash(de_en("Bitte einen Namen für die Organisation angeben.", "Please give the organisation a name."), "error")
        return back
    if orgs.name_taken(name):
        flash(de_en("Eine Organisation „{name}“ gibt es schon. Bitte einen anderen Namen wählen.",
                    "An organisation “{name}” exists already. Please choose another name.", name=name), "error")
        return back
    found = None
    if (request.form.get("admin") or "").strip():
        found = _lead_from_outside(request.form.get("admin"), request.form.get("admin_name"))
        if isinstance(found, str):
            flash(found, "error")
            return back
    org = orgs.make(name, current_user, long_name=request.form.get("long_name"),
                    description=request.form.get("description"))
    if found is not None:
        from app import people_keys

        orgs.add(org, found[1] or people_keys.account(found[0], found[2]), role="admin", by=current_user)
    db.session.commit()
    security_log.event("organisation_created", by=current_user.id, target=f"org {org.id}",
                       account=found[1].id if found and found[1] is not None else None)
    if found is not None:
        flash(de_en("Organisation „{o}“ angelegt; {n} ist ihr Admin.", "Organisation “{o}” created; {n} is its admin.",
                    o=org.name, n=found[2] or found[0]), "success")
    else:
        flash(de_en("Organisation „{o}“ angelegt. Admins setzen Sie, sobald Personen dazugehören.",
                    "Organisation “{o}” created. You appoint admins once people belong to it.", o=org.name), "success")
    return redirect(url_for("admin.organisation_page", org_id=org.id))


def accounts_need_fresh(back):
    from app import accounts

    return accounts.need_fresh(back)


@bp.route("/groups/<int:group_id>/to-org", methods=["GET", "POST"])
def group_to_org(group_id):
    """Make a group an organisation (A23, Ralf 2026-10-15): a preview of who moves, what comes along and which shares
    become personal entries; then, with a fresh sign-in, the change."""
    from app import orgs
    from app.extensions import db
    from app.models import WorkGroup

    group = db.session.get(WorkGroup, group_id)
    if group is None:
        abort(404)
    if request.method == "POST":
        again = accounts_need_fresh(url_for("admin.group_to_org", group_id=group.id))
        if again:
            return again
        org = orgs.make_from_group(group, request.form.get("name"), current_user,
                                   leads_admin=request.form.get("leads_admin") == "1",
                                   keep_group=request.form.get("keep_group") == "1")
        db.session.commit()
        from app import audit

        audit.done(detail=org.name[:40])
        flash(de_en("Organisation „{o}“ angelegt.", "Organisation “{o}” created.", o=org.name), "success")
        return redirect(url_for("admin.organisation_page", org_id=org.id))
    p = orgs.plan(group)
    return render_template("admin/org_from_group.html", p=p, group=group, source_name=orgs.name(p["source"]),
                           kept_shares=orgs.crossing(p, True), dropped_shares=orgs.crossing(p, False))
