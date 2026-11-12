"""The participants' database role (app/participant_grants.py): `survey` may be updated only in the columns the
participants' side writes, never in those that decide who owns a survey, what it shows or how long its data stay;
and a participants' container with PARTICIPANT_ROLE_CHECK refuses to start under any other role (role_problem;
access audit 2026-11-11)."""
import pytest

from app import participant_grants as pg

NEVER = ("created_by_id", "org_id", "end_texts", "seal", "retention", "zenodo", "erase_code", "showcase",
         "org_showcase", "title", "default_language", "start_at", "field_end", "max_responses", "paradata",
         "flow", "data_pool")
# measured: what the participants' side writes today; the flow and the data pool no longer (access audit
# 2026-11-11, B2 stage 2: board_erase_floor, flow.normalize_for_reading)
MEASURED = {"status", "closed_at", "close_reason", "status_log", "declined_count", "updated_at"}


def test_survey_has_column_rights_only_and_never_the_owner_or_the_flow():
    privileges = pg.GRANTS["survey"][0]
    assert "UPDATE" not in privileges                       # never the whole row
    cols = pg.columns("survey", "UPDATE")
    assert cols, privileges
    assert not cols & set(NEVER), cols & set(NEVER)
    assert cols == MEASURED, cols ^ MEASURED                # a new column is measured first (PARTICIPANT_SQL_RECORD)


class _Connection:
    """A stand-in for a PostgreSQL connection: has_*_privilege answers from `granted`."""

    def __init__(self, granted, user="askurious_participant", execute=True):
        self.granted, self.user, self.may_execute = granted, user, execute

    def execute(self, statement, params=None):
        sql = str(statement)
        if sql.strip() == "SELECT current_user":
            value = self.user
        elif "has_function_privilege" in sql:
            value = self.may_execute
        elif "has_column_privilege" in sql:
            value = (params["t"].split(".")[1], params["c"], params["p"]) in self.granted
        else:
            value = (params["t"].split(".")[1], None, params["p"]) in self.granted
        return type("R", (), {"scalar": lambda _self: value})()


def test_the_restricted_role_passes():
    assert pg.role_problem(_Connection(set())) is None


@pytest.mark.parametrize("granted, word", [
    ({("user_login", "password_hash", "SELECT")}, "user_login.password_hash"),
    ({("survey", None, "UPDATE")}, "UPDATE on survey"),
    ({("survey_member", None, "INSERT")}, "INSERT on survey_member"),
    ({("survey", "flow", "UPDATE")}, "UPDATE on survey.flow"),
    ({("survey", "data_pool", "UPDATE")}, "UPDATE on survey.data_pool"),
    ({("response", None, "DELETE")}, "DELETE on response"),
])
def test_a_wider_role_is_named(granted, word):
    problem = pg.role_problem(_Connection(granted, user="askurious_app"))
    assert problem and word in problem and "askurious_app" in problem


def test_compose_switches_the_check_on_with_the_participants_role():
    from tests.platform.test_audit8_b_deploy import _read

    compose = _read("deploy", "docker", "compose.yaml")
    assert "PARTICIPANT_ROLE_CHECK: ${PARTICIPANT_DB_USER:+1}" in compose
    assert "PARTICIPANT_ROLE_CHECK: ${PARTICIPANT_DB_USER:+1}" in _read("deploy", "docker", "isolation", "roles.yaml")


def test_a_role_that_cannot_delete_through_the_function_is_named():
    """Without EXECUTE on participant_erase_response no participation could be deleted (deletion code, diary app,
    declines): such a role is refused at the start as well (access audit 2026-11-11, B2 stage 2)."""
    problem = pg.role_problem(_Connection(set(), execute=False))
    assert problem and "no EXECUTE on participant_erase_response" in problem


def test_participations_are_not_deleted_by_the_role_itself():
    assert "DELETE" not in pg.allowed("response")
    assert "participant_erase_response(bigint[], bigint[], text, text)" in pg.FUNCTIONS
