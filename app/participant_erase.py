"""Participations deleted on the participants' side only with a proof the database checks (access audit 2026-11-11,
B2 stage 2).

The participants' containers delete a participation in six places: with its deletion code (participate.erase,
public.erase), in the browser that took part (participate.erase, "here"), from the diary app with the person's
secret (diary_api.erase), when someone declines (participate.page) and when a further word of a live word cloud is
taken back (live/audience.py, withdraw); a pretest link (/p/...) drops the tester's test run before the next
(editor/preview.py). On PostgreSQL the function participant_erase_response, which runs with the
rights of its owner (SECURITY DEFINER), deletes the rows there; it deletes a row only when the proof of the
transaction fits it:

- "code": the deletion code; the function hashes it as erasure.digest does and looks for the participation with that
  hash. A diary person's further entries go with it (same enrolment).
- "browser": the participation id (pid) the browser's session holds; the same for the further entries of a person.
- "diary": the secret of the diary app; the function hashes it as diary_api._hash does and compares it with the
  person's (enrolment.secret_hash).
- "declined": only a participation whose status is "declined".
- "live_word": only a further word of a live vote (response.live_extra).
- "test_run": only test data (response.source "test"), never a real participation.

So the participants' role needs no DELETE on response once stage 2b takes it away. The function is only a gate for
deleting: the role still writes participations (UPDATE), so it cannot stand against a role that overwrites rows; it
does stand against a single DELETE that empties a survey or many at once.

The ORM keeps doing the deletion as before (seal chain, quotas, recordings, the cache triggers: all hooks of
Session.delete stay): on the participants' side the DELETE statement the unit of work sends for response is turned
into one call of the function (attach, _redirect), with the ids and row versions of the flush and the proof that
`prove` noted for this transaction. A flush without a proof, or with one that does not fit, deletes nothing; the ORM
then counts fewer rows than it expected and raises StaleDataError, and the request is rolled back.
"""
from sqlalchemy import event, text
from sqlalchemy.sql.dml import Delete

FUNCTION = "participant_erase_response"
SIGNATURE = f"{FUNCTION}(bigint[], bigint[], text, text)"
KINDS = ("code", "browser", "diary", "declined", "live_word", "test_run")
_KEY = "participant_erase_proof"

# Each condition decides for one row r of response (ids and versions of the flush) with the proof p_proof.
# _anchor(c): the participation the proof names; r is it, or another entry of the same diary person.
_ANCHOR = ("EXISTS (SELECT 1 FROM public.response c WHERE {match} AND NOT c.is_preview AND c.survey_id = r.survey_id "
           "AND (c.id = r.id OR (c.enrolment_id IS NOT NULL AND c.enrolment_id = r.enrolment_id)))")
_SQL = f"""CREATE OR REPLACE FUNCTION public.{FUNCTION}(p_ids bigint[], p_versions bigint[], p_kind text, p_proof text)
RETURNS SETOF integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    h text := NULL;
BEGIN
    IF p_proof IS NOT NULL AND p_proof <> '' THEN
        IF p_kind = 'code' THEN
            h := encode(sha256(convert_to('erase:' || p_proof, 'UTF8')), 'hex');
        ELSIF p_kind = 'diary' THEN
            h := encode(sha256(convert_to(p_proof, 'UTF8')), 'hex');
        END IF;
    END IF;
    RETURN QUERY
    DELETE FROM public.response r
    USING unnest(p_ids, p_versions) AS a(id, version)
    WHERE r.id = a.id AND r.version = a.version AND (
        (p_kind = 'code' AND h IS NOT NULL AND {_ANCHOR.format(match="c.erase_hash = h")})
        OR (p_kind = 'browser' AND p_proof <> '' AND {_ANCHOR.format(match="c.pid = p_proof")})
        OR (p_kind = 'diary' AND h IS NOT NULL AND EXISTS (SELECT 1 FROM public.enrolment e
                                                           WHERE e.id = r.enrolment_id AND e.secret_hash = h))
        OR (p_kind = 'declined' AND r.status = 'declined')
        OR (p_kind = 'live_word' AND r.live_extra)
        OR (p_kind = 'test_run' AND r.source = 'test')
    )
    RETURNING r.id;
END $$"""


def install(connection):
    """The function, on PostgreSQL (migration 0123; a database made with create_all, models._erase_function). Nobody
    may call it but the roles it is granted to (schema_migrate.grant_participant): not PUBLIC, the default."""
    if connection.dialect.name != "postgresql":
        return
    connection.execute(text(_SQL))
    connection.execute(text(f"REVOKE ALL ON FUNCTION public.{SIGNATURE} FROM PUBLIC"))


def uninstall(connection):
    if connection.dialect.name == "postgresql":
        connection.execute(text(f"DROP FUNCTION IF EXISTS public.{SIGNATURE}"))


def exists(connection):
    return connection.dialect.name == "postgresql" and bool(connection.execute(
        text("SELECT to_regprocedure(:f) IS NOT NULL"), {"f": f"public.{SIGNATURE}"}).scalar())


def prove(kind, value=""):
    """The proof with which this transaction may delete participations (KINDS). Only where the deletion goes through
    the function (attach); elsewhere nothing happens. Forgotten when the transaction ends."""
    from flask import current_app

    from app.extensions import db

    assert kind in KINDS, kind
    if not current_app.extensions.get(_KEY):
        return
    db.session.connection().info[_KEY] = (kind, str(value or ""))


def attach(app, engine):
    """The participants' side on PostgreSQL: DELETE on response goes through the function (create_app)."""
    if engine.dialect.name != "postgresql" or getattr(engine, "_participant_erase", False):
        app.extensions[_KEY] = engine.dialect.name == "postgresql"
        return
    engine._participant_erase = True
    app.extensions[_KEY] = True
    event.listen(engine, "before_execute", _redirect, retval=True)
    for name in ("commit", "rollback"):
        event.listen(engine, name, _forget)
    event.listen(engine.pool, "checkin", lambda dbapi_connection, _record: _forget_info(dbapi_connection))


def _forget(connection):
    connection.info.pop(_KEY, None)


def _forget_info(dbapi_connection):
    info = getattr(dbapi_connection, "info", None)
    if isinstance(info, dict):
        info.pop(_KEY, None)


def _redirect(connection, element, multiparams, params, _options):
    """The unit of work's DELETE of participations (WHERE id = :id AND version = :version) as one call of the
    function; every other statement as it is."""
    if not isinstance(element, Delete) or element.table.name != "response":
        return element, multiparams, params
    rows = list(multiparams) or [params]
    if not rows or not all(isinstance(r, dict) and set(r) == {"id", "version"} for r in rows):
        return element, multiparams, params  # not the ORM's: left as it is (the role's own rights decide)
    kind, proof = connection.info.get(_KEY, ("none", ""))
    call = text(f"SELECT * FROM public.{FUNCTION}(CAST(:ids AS bigint[]), CAST(:versions AS bigint[]), :kind, :proof)")
    return call, [], {"ids": [r["id"] for r in rows], "versions": [r["version"] for r in rows], "kind": kind,
                      "proof": proof}
