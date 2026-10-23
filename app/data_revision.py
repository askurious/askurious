"""Cheap, transactionally accurate cache revisions, also for bulk/legacy SQL (SP13 PF-1/PF-3/PF-5)."""
from sqlalchemy import text
from app.extensions import db
from app.models import Survey


SHARDS = 64  # rows per data pool: a participation counts in the row of its id modulo SHARDS (models.ResponseRevision)


def _data_rows(survey):
    from app.models import ResponseRevision as rows

    return [tuple(row) for row in db.session.execute(
        db.select(rows.source, rows.status, rows.shard, rows.revision, rows.nonce).where(rows.survey_id == survey.id)
        .order_by(rows.source, rows.status, rows.shard))]


def revision(survey, structure=False):
    db.session.flush()
    if structure:
        return db.session.scalar(db.select(Survey.structure_rev).where(Survey.id == survey.id)) or 0
    return sum(row[3] for row in _data_rows(survey))


def stamp(survey, structure=False):
    """Counter plus fresh change nonce: a restore and later fork cannot reuse an old counter's cached state. The data
    of a survey: the sum of its pool rows and a hash of their nonces (audit 2026-10-15, T05-N-1)."""
    import hashlib
    import json

    db.session.flush()
    if structure:
        found = db.session.execute(db.select(Survey.structure_rev, Survey.structure_nonce)
                                   .where(Survey.id == survey.id)).first()
        return tuple(found) if found is not None else (0, "")
    rows = _data_rows(survey)
    return (sum(row[3] for row in rows), hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:32] if rows else "")


def sweep():
    """The pool rows of surveys that are gone (no foreign key, so that counting takes no lock on the survey's row)."""
    from app.models import ResponseRevision

    db.session.execute(db.delete(ResponseRevision).where(ResponseRevision.survey_id.not_in(db.select(Survey.id))))
    db.session.commit()


def _install_response(connection, tables):
    """The data triggers: every insert, change and deletion of a participation counts in its pool's row for its id
    (and in the row of the pool it left). Only rows of response_revision are written, never the survey's row, and
    two participations in different shards never wait for each other (audit 2026-10-15, T05-N-1)."""
    if "response" not in tables or "response_revision" not in tables:
        return
    sqlite = connection.dialect.name == "sqlite"
    nonce = "lower(hex(randomblob(16)))" if sqlite else "md5(random()::text || clock_timestamp()::text)"

    def count(prefix):
        return (f"INSERT INTO response_revision (survey_id,source,status,shard,revision,nonce) VALUES "
                f"({prefix}.survey_id,{prefix}.source,{prefix}.status,{prefix}.id % {SHARDS},1,{nonce}) "
                f"ON CONFLICT(survey_id,source,status,shard) DO UPDATE SET revision=response_revision.revision+1, "
                f"nonce=excluded.nonce;")

    if sqlite:
        moved = "OLD.survey_id IS NOT NEW.survey_id OR OLD.source IS NOT NEW.source OR OLD.status IS NOT NEW.status"
        for name, action, when, prefix in (("revision_response_insert", "INSERT", "", "NEW"),
                                           ("revision_response_update", "UPDATE", "", "NEW"),
                                           ("revision_response_moved", "UPDATE", f" WHEN {moved}", "OLD"),
                                           ("revision_response_delete", "DELETE", "", "OLD")):
            connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
            connection.execute(text(f"CREATE TRIGGER {name} AFTER {action} ON response{when} BEGIN {count(prefix)} END"))
    elif connection.dialect.name == "postgresql":
        connection.execute(text(f"""CREATE OR REPLACE FUNCTION revision_response() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP <> 'DELETE' THEN
                    {count('NEW')}
                END IF;
                IF TG_OP = 'DELETE' OR (TG_OP = 'UPDATE' AND (OLD.survey_id,OLD.source,OLD.status)
                                        IS DISTINCT FROM (NEW.survey_id,NEW.source,NEW.status)) THEN
                    {count('OLD')}
                END IF;
                RETURN NULL;
            END $$"""))
        connection.execute(text("DROP TRIGGER IF EXISTS revision_response ON response"))
        connection.execute(text("CREATE TRIGGER revision_response AFTER INSERT OR UPDATE OR DELETE ON response "
                                "FOR EACH ROW EXECUTE FUNCTION revision_response()"))


def install(connection):
    from sqlalchemy import inspect

    tables = set(inspect(connection).get_table_names())
    if "survey" not in tables or "data_rev" not in {c["name"] for c in inspect(connection).get_columns("survey")}:
        return
    if connection.dialect.name == "sqlite":
        connection.execute(text("DROP TRIGGER IF EXISTS revision_survey_flow"))
        connection.execute(text("CREATE TRIGGER revision_survey_flow AFTER UPDATE OF flow ON survey "
                                "WHEN OLD.flow IS NOT NEW.flow BEGIN UPDATE survey SET structure_rev=structure_rev+1, "
                                "structure_nonce=lower(hex(randomblob(16))) WHERE id=NEW.id; END"))
    elif connection.dialect.name == "postgresql":
        connection.execute(text("""CREATE OR REPLACE FUNCTION revision_survey_flow() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.flow::text IS DISTINCT FROM NEW.flow::text THEN
                    NEW.structure_rev := OLD.structure_rev+1;
                    NEW.structure_nonce := md5(random()::text || clock_timestamp()::text);
                END IF;
                RETURN NEW;
            END $$"""))
        connection.execute(text("DROP TRIGGER IF EXISTS revision_survey_flow ON survey"))
        connection.execute(text("CREATE TRIGGER revision_survey_flow BEFORE UPDATE OF flow ON survey "
                                "FOR EACH ROW EXECUTE FUNCTION revision_survey_flow()"))
    _install_response(connection, tables)
    for table, column in (("block", "structure_rev"), ("item", "structure_rev")):
        if table not in tables:
            continue
        nonce_column = "structure_nonce"
        random_nonce = "lower(hex(randomblob(16)))" if connection.dialect.name == "sqlite" else "md5(random()::text || clock_timestamp()::text)"
        def target(prefix):
            return f"(SELECT survey_id FROM block WHERE id={prefix}.block_id)" if table == "item" else f"{prefix}.survey_id"
        if connection.dialect.name == "sqlite":
            for action, prefix in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
                name = f"revision_{table}_{action.lower()}"
                extra = (f"UPDATE survey SET {column}={column}+1, {nonce_column}={random_nonce} WHERE id={target('OLD')} "
                         f"AND id IS NOT {target('NEW')};") if action == "UPDATE" else ""
                connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
                connection.execute(text(f"CREATE TRIGGER {name} AFTER {action} ON {table} BEGIN "
                                        f"UPDATE survey SET {column}={column}+1, {nonce_column}={random_nonce} WHERE id={target(prefix)}; {extra} END"))
        elif connection.dialect.name == "postgresql":
            name = f"revision_{table}"
            connection.execute(text(f"""CREATE OR REPLACE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP <> 'DELETE' THEN
                    UPDATE survey SET {column}={column}+1, {nonce_column}={random_nonce} WHERE id={target('NEW')};
                END IF;
                IF TG_OP = 'DELETE' THEN
                    UPDATE survey SET {column}={column}+1, {nonce_column}={random_nonce} WHERE id={target('OLD')};
                ELSIF TG_OP = 'UPDATE' AND {target('OLD')} IS DISTINCT FROM {target('NEW')} THEN
                    UPDATE survey SET {column}={column}+1, {nonce_column}={random_nonce} WHERE id={target('OLD')};
                END IF;
                RETURN NULL;
            END $$"""))
            connection.execute(text(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
            connection.execute(text(f"CREATE TRIGGER {name} AFTER INSERT OR UPDATE OR DELETE ON {table} "
                                    f"FOR EACH ROW EXECUTE FUNCTION {name}()"))


def uninstall(connection):
    if connection.dialect.name == "sqlite":
        connection.execute(text("DROP TRIGGER IF EXISTS revision_survey_flow"))
    elif connection.dialect.name == "postgresql":
        connection.execute(text("DROP TRIGGER IF EXISTS revision_survey_flow ON survey"))
        connection.execute(text("DROP FUNCTION IF EXISTS revision_survey_flow()"))
    for table in ("response", "block", "item"):
        if connection.dialect.name == "sqlite":
            for action in ("insert", "update", "delete") + (("moved",) if table == "response" else ()):
                connection.execute(text(f"DROP TRIGGER IF EXISTS revision_{table}_{action}"))
        elif connection.dialect.name == "postgresql":
            connection.execute(text(f"DROP TRIGGER IF EXISTS revision_{table} ON {table}"))
            connection.execute(text(f"DROP FUNCTION IF EXISTS revision_{table}()"))


def validate(connection):
    """Fail closed after a damaged/incomplete restore; column comparison cannot see triggers."""
    if connection.dialect.name == "sqlite":
        expected = {f"revision_{table}_{action}" for table in ("response", "block", "item")
                    for action in ("insert", "update", "delete")}
        expected |= {"revision_survey_flow", "revision_response_moved"}
        found = set(connection.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'")).scalars())
    elif connection.dialect.name == "postgresql":
        expected = {f"revision_{table}" for table in ("response", "block", "item")}
        expected.add("revision_survey_flow")
        found = set(connection.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgenabled <> 'D' "
                                            "AND tgrelid IN ('survey'::regclass,'response'::regclass,'block'::regclass,'item'::regclass)")).scalars())
    else:
        return
    if expected - found:
        from app.migrations import SchemaMismatch

        raise SchemaMismatch("Missing cache revision triggers: " + ", ".join(sorted(expected - found)) +
                             "; restore the schema/triggers from migrations 0058 and 0064 before serving cached data.")
