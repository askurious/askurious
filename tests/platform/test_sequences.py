"""PostgreSQL sequences after rows inserted with their own id (app/extensions.py, catch_up): moved on with nextval
only, never setval, which the runtime role of the Docker server may not run. Undoing a deleted block on RWTH ended in
"permission denied for sequence block_id_seq" (Ralf, 2026-10-22)."""
import pytest

from app.extensions import catch_up
from tests.conftest import PG_SERVER, _pg_admin, _pg_name, _pg_url


class _Connection:
    """Answers "select last_value, is_called" and records what else is run."""

    def __init__(self, last, called):
        self.state, self.run = (last, called), []

    def execute(self, statement, params=None):
        sql = str(statement)
        if "last_value" in sql:
            state = self.state
            return type("R", (), {"one": lambda self: state})()
        self.run.append((sql, params))


@pytest.mark.parametrize("last, called, top, steps", [
    (120, True, 57, None),      # undo: the block's old id lies far below what the sequence gave out
    (120, True, 120, None),
    (1, False, 0, None),
    (10, True, 13, 3),          # a new account one above the highest id: three ids behind
    (1, False, 4, 4),           # a sequence never used: its next value is 1 itself
])
def test_sequence_moves_on_with_nextval_only(last, called, top, steps):
    connection = _Connection(last, called)
    catch_up(connection, "public.block_id_seq", top)
    assert all("setval" not in sql for sql, _ in connection.run)
    if steps is None:
        assert connection.run == []
    else:
        (sql, params), = connection.run
        assert "nextval" in sql and params == {"s": "public.block_id_seq", "n": steps}


def test_no_rows_no_query():
    connection = _Connection(1, False)
    catch_up(connection, "public.block_id_seq", None)
    assert connection.run == []


@pytest.mark.skipif(not PG_SERVER, reason="needs a PostgreSQL server (TEST_PG_SERVER)")
def test_a_role_without_update_on_the_sequence_moves_it_on():
    from sqlalchemy import create_engine, text

    name = _pg_name("sequences")
    _pg_admin(f'DROP DATABASE IF EXISTS "{name}"', f'CREATE DATABASE "{name}"')
    engine = create_engine(_pg_url(name))
    try:
        with engine.begin() as c:
            c.execute(text("CREATE TABLE block (id serial PRIMARY KEY)"))
            c.execute(text("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'seq_test_run') "
                           "THEN CREATE ROLE seq_test_run NOLOGIN; END IF; END $$"))
            c.execute(text("GRANT SELECT, INSERT ON block TO seq_test_run"))
            c.execute(text("GRANT USAGE, SELECT ON SEQUENCE block_id_seq TO seq_test_run"))  # as runtime-role.sql
        with engine.begin() as c:
            c.execute(text("SET ROLE seq_test_run"))
            c.execute(text("INSERT INTO block (id) VALUES (1), (2), (3)"))
            catch_up(c, "public.block_id_seq", 3)
            c.execute(text("INSERT INTO block DEFAULT VALUES"))  # gets 4, not a taken id
            assert c.scalar(text("SELECT max(id) FROM block")) == 4
            catch_up(c, "public.block_id_seq", 2)  # an old id put back: nothing to do, no error
    finally:
        engine.dispose()
        _pg_admin(f'DROP DATABASE IF EXISTS "{name}"')
