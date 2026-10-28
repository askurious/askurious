"""Two participants' containers restarted one after the other (zwei-teilnahme-container-konzept.md, Ralf
2026-10-08): compose.yaml, nginx's upstream, the ROLLING mark of the migrations, restart.sh's choice between one by
one and together, and the status page that calls the whole disrupted only when both containers are down."""
import ast
import os
import re

import pytest

from app.status_node import disrupted, parse_redundant
from tests.helpers import bounded_run
from tests.platform.test_audit8_b_deploy import BASH, BASH_SLOW, needs_bash

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCKER = os.path.join(HERE, "deploy", "docker")
VERSIONS = os.path.join(HERE, "migrations", "versions")


def _bash_major():
    if BASH is None:
        return 0
    out = bounded_run.run([BASH, "-c", "echo ${BASH_VERSINFO[0]}"], text=True, timeout=60 * BASH_SLOW)
    return int(out.stdout.strip() or 0)


# not_rolling() keeps the migrations in associative arrays (bash 4, as on the server); macOS ships bash 3.2
needs_bash4 = pytest.mark.skipif(_bash_major() < 4, reason="needs bash 4 or newer")


def _read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


# What old code cannot live with while the other participant still runs it: a column or table gone or renamed, a
# column that has to be filled although old code does not fill it, a shorter or other type, a UNIQUE that old code's
# "look, then insert" runs into, rows changed under its feet, a trigger. The check reads the syntax tree of the whole
# module (upgrade(), every function of the module it calls, the module's constants), not a pattern over the text of
# upgrade(): a drop in a loop or a helper, a column kept in a tuple, an alter_column(existing_type=sa.String(19),
# nullable=False) count too (audit 2026-10-21, B10). A ROLLING = True migration may take such a step only when
# ROLLING_ALLOWED names it for its revision, with the reason why the old code is not hurt.
RISKS = {
    "drop": "drops a column or a table",
    "rename": "renames a table or a column",
    "required": "makes a column required or adds one without a default",
    "type": "changes the type of a column",
    "unique": "adds a UNIQUE index or constraint",
    "data": "changes or deletes rows (UPDATE/DELETE)",
    "sql": "runs raw SQL that alters or drops",
    "trigger": "creates or replaces triggers",
}

_SQL = [
    (re.compile(r"\bUPDATE\s+\S+\s+SET\b|\bDELETE\s+FROM\b"), "data"),
    (re.compile(r"\bALTER\s+TABLE\b|\bDROP\s+(TABLE|COLUMN|INDEX|CONSTRAINT|VIEW)\b"), "sql"),
    (re.compile(r"\bCREATE\s+(OR\s+REPLACE\s+)?(TRIGGER|FUNCTION)\b|\bDROP\s+TRIGGER\b"), "trigger"),
    (re.compile(r"\bCREATE\s+UNIQUE\s+INDEX\b"), "unique"),
]


def _upgrade_source(text):
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            return ast.get_source_segment(text, node)
    return ""


def _rolling(text):
    """The value of ROLLING (True/False), None when the migration does not say."""
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "ROLLING" for t in node.targets):
            assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, bool), "ROLLING = True/False"
            return node.value.value
    return None


def _reached(tree):
    """The statements upgrade() runs: its body, the bodies of the module's functions it reaches (by call or by
    name, also through each other), and the module's own statements (constants such as a tuple of columns)."""
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    todo, seen = ["upgrade"], set()
    while todo:
        name = todo.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        todo += [n.id for n in ast.walk(functions[name]) if isinstance(n, ast.Name) and n.id in functions]
    nodes = [functions[n] for n in sorted(seen)]
    nodes += [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Import,
                                                          ast.ImportFrom))
              and not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    return nodes


def _walk_with_parents(nodes):
    """(node, [ancestors]) of everything in these statements, docstrings left out."""
    out = []

    def visit(node, parents):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return  # a docstring or a bare string: words, not SQL that runs
        out.append((node, parents))
        for child in ast.iter_child_nodes(node):
            visit(child, parents + [node])
    for node in nodes:
        visit(node, [])
    return out


def _kw(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _is_const(node, value):
    return isinstance(node, ast.Constant) and node.value is value


def _risky(text):
    """{(kind, detail)} of the risky steps of a migration's upgrade (kinds: RISKS)."""
    tree = ast.parse(text)
    found = set()
    revision_code = "data_revision" in text
    for node, parents in _walk_with_parents(_reached(tree)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for pattern, kind in _SQL:
                if pattern.search(node.value):
                    found.add((kind, node.value.strip()[:60]))
            continue
        if not isinstance(node, ast.Call):
            continue
        name, line = _func_name(node), f"line {node.lineno}"
        if name in ("drop_column", "drop_table"):
            found.add(("drop", f"{name} {line}"))
        elif name == "rename_table":
            found.add(("rename", line))
        elif name == "alter_column":
            if _kw(node, "new_column_name") is not None:
                found.add(("rename", f"alter_column {line}"))
            if _is_const(_kw(node, "nullable"), False):
                found.add(("required", f"alter_column {line}"))
            if _kw(node, "type_") is not None:
                found.add(("type", f"alter_column {line}"))
        elif name == "create_index" and _is_const(_kw(node, "unique"), True) or name == "create_unique_constraint":
            found.add(("unique", f"{name} {line}"))
        elif name == "Column":
            in_new_table = any(isinstance(p, ast.Call) and _func_name(p) == "create_table" for p in parents)
            if not in_new_table and _is_const(_kw(node, "nullable"), False) and _kw(node, "server_default") is None:
                found.add(("required", f"Column {line}"))
            if not in_new_table and _is_const(_kw(node, "unique"), True):
                found.add(("unique", f"Column {line}"))
        elif name in ("update", "delete"):
            f = node.func
            on_sa = isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("sa", "sqlalchemy")
            bare = isinstance(f, ast.Name)
            table_method = isinstance(f, ast.Attribute) and not node.args and not node.keywords
            if on_sa or bare or table_method:
                found.add(("data", f"{name}() {line}"))
        elif name in ("install", "uninstall") and revision_code:
            found.add(("trigger", f"data_revision.{name} {line}"))
    return found


# Risky steps of migrations marked ROLLING = True that are safe for the old code, each with its reason. A new entry
# needs the same: why the participant on the code before keeps working while the step runs and after it.
TRIGGERS = ("the revision triggers of app/data_revision.py, taken out and put back around a table SQLite copies; "
            "they count changes for caches and change no row the old code reads")
ROLLING_ALLOWED = {
    "0033": {"data": "removes doubled invitations and waiting mails a race left; the old code reads them like any "
                     "other missing row (docstring)",
             "unique": "uq_survey_invite_survey_email and uq_mail_outbox_waiting_invite: the old code's look-then-insert "
                       "may meet an IntegrityError in the rolling window (audit 2026-10-21, B18; applied, left as it is)"},
    "0035": {"type": "seal_entry.reason String(20) -> String(200): longer, the old code writes shorter values"},
    "0049": {"data": "fills the new column response.part from occasion; the old code does not read it"},
    "0104": {"data": "fills the new column interviewer_access.may_code ('own' where an analysis had the tick); the "
                     "old code does not read it"},
    "0050": {"data": "fills the new column response.due_at; the old code does not read it",
             "unique": "partial unique indexes on new, empty columns (due_at, uuid) the old code never writes"},
    "0058": {"trigger": TRIGGERS},
    "0064": {"trigger": TRIGGERS},
    "0069": {"trigger": TRIGGERS},
    "0071": {"trigger": TRIGGERS},
    "0080": {"data": "raises 'edit' to 'create' in kinds; the code before reads a level it does not know as 'edit' "
                     "(docstring)"},
    "0083": {"data": "fills the new column user_id where it is empty; the old code does not read it"},
    "0087": {"data": "fills the new column response.live_extra; the old code does not read it"},
    "0090": {"data": "fills the new design columns and empties grants that decide nothing since 2026-10-16; the "
                     "participants' side reads neither (docstring)"},
    "0092": {"trigger": TRIGGERS},
}


@pytest.mark.parametrize("name", sorted(f for f in os.listdir(VERSIONS) if f.endswith(".py")))
def test_a_migration_marked_rolling_only_adds(name):
    text = _read("migrations", "versions", name)
    if _rolling(text) is not True:
        return  # not marked (or False): restart.sh restarts both participants together for it
    revision = re.search(r"^revision = ['\"]([^'\"]+)['\"]", text, re.M).group(1)
    allowed = ROLLING_ALLOWED.get(revision, {})
    found = _risky(text)
    left = sorted(f"{RISKS[kind]} ({detail})" for kind, detail in found if kind not in allowed)
    assert not left, (f"{name} says ROLLING = True but {'; '.join(left)}: set ROLLING = False, or name the step in "
                      "ROLLING_ALLOWED with the reason why the old code is not hurt")
    stale = set(allowed) - {kind for kind, _ in found}
    assert not stale, f"ROLLING_ALLOWED[{revision!r}] names {sorted(stale)}, which {name} does not do"


def test_rolling_allowed_names_known_steps_with_a_reason():
    for revision, kinds in ROLLING_ALLOWED.items():
        assert kinds and set(kinds) <= set(RISKS), revision
        assert all(isinstance(why, str) and len(why) > 20 for why in kinds.values()), revision


def test_the_check_catches_what_it_should():
    """Audit 2026-10-21, B10: the seven steps the former pattern over upgrade() did not see."""
    def kinds(source):
        return {kind for kind, _ in _risky(source)}

    assert kinds("def upgrade():\n    op.drop_column('survey', 'x')\n") == {"drop"}
    assert kinds("def upgrade():\n    with op.batch_alter_table('user') as b:\n"
                 "        b.alter_column('orcid', existing_type=sa.String(19), nullable=False)\n") == {"required"}
    assert kinds("COLUMNS = (('survey', sa.Column('x', sa.Integer(), nullable=False)),)\n"
                 "def upgrade():\n    for t, c in COLUMNS:\n        op.add_column(t, c)\n") == {"required"}
    assert kinds("def _gone():\n    op.drop_column('survey', 'x')\ndef upgrade():\n    _gone()\n") == {"drop"}
    assert kinds("def upgrade():\n    op.create_index('uq', 'survey_invite', ['a', 'b'], unique=True)\n") == {"unique"}
    assert kinds("def upgrade():\n    op.execute('ALTER TABLE survey DROP COLUMN x')\n") == {"sql"}
    assert kinds("def upgrade():\n    op.alter_column('seal_entry', 'reason', existing_type=sa.String(200), "
                 "type_=sa.String(20))\n") == {"type"}
    assert kinds("def upgrade():\n    col = sa.Column('x', sa.Integer(), nullable=False)\n"
                 "    op.add_column('survey', col)\n") == {"required"}
    assert kinds("def upgrade():\n    bind = op.get_bind()\n    bind.execute(t.update().values(x=1))\n"
                 "    bind.execute(sa.text(f'DELETE FROM {name} WHERE x'))\n") == {"data"}
    # what only adds stays allowed: a new table with required columns, a column with a default, an index
    assert kinds("def upgrade():\n    op.create_table('t', sa.Column('id', sa.Integer(), nullable=False))\n"
                 "    op.add_column('survey', sa.Column('x', sa.Integer(), nullable=False, server_default='0'))\n"
                 "    op.create_index('ix_t_id', 't', ['id'])\n    d = {}\n    d.update(a=1)\n") == set()
    # downgrade() is not upgrade(), and a docstring is no SQL
    assert kinds('def upgrade():\n    """ALTER TABLE x"""\n    pass\ndef downgrade():\n    op.drop_table("t")\n') == set()


# Indexes on the large tables of participation (audit 2026-10-21, B13). On PostgreSQL CREATE INDEX holds a SHARE lock
# and DROP INDEX an ACCESS EXCLUSIVE lock on the table until the migration commits, and the migration has no
# statement_timeout: both participants' containers wait for the whole build. A ROLLING migration therefore builds and
# drops such an index with postgresql_concurrently=True inside `with op.get_context().autocommit_block():` (a new
# index under a new name first, the old one dropped after it).
BIG_TABLES = ("response", "enrolment", "once_value", "seal_entry", "response_revision", "live_reaction",
              "browser_receipt", "rate_hit", "rate_count")
# built before the rule; already applied everywhere, left as they are
BIG_INDEX_BEFORE_THE_RULE = {"0024", "0046", "0049", "0050", "0053", "0057", "0060", "0077", "0087", "0088",
                             "0089"}


def _table_of_index(name):
    """The large table an index name says it belongs to (ix_<table>_..., uq_<table>_...), else None."""
    if not isinstance(name, str):
        return None
    hits = [t for t in BIG_TABLES if re.match(rf"(ix|uq)_{t}_", name)]
    return max(hits, key=len) if hits else None


def _big_index_steps(text):
    """{"create_index ix_... on response"} of the index steps on BIG_TABLES that do not run concurrently."""
    tree = ast.parse(text)
    out = set()
    for node, parents in _walk_with_parents(_reached(tree)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = node.value
            for m in re.finditer(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(\w+)"
                                 r"\s+ON\s+(\w+)", sql, re.I):
                if not m.group(1) and m.group(3) in BIG_TABLES:
                    out.add(f"CREATE INDEX {m.group(2)} on {m.group(3)}")
            for m in re.finditer(r"\bDROP\s+INDEX\s+(CONCURRENTLY\s+)?(?:IF\s+EXISTS\s+)?(\w+)", sql, re.I):
                if not m.group(1) and _table_of_index(m.group(2)):
                    out.add(f"DROP INDEX {m.group(2)} on {_table_of_index(m.group(2))}")
            continue
        if not isinstance(node, ast.Call) or _func_name(node) not in ("create_index", "drop_index"):
            continue
        name = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        batch = next((w for w in reversed(parents) if isinstance(w, ast.With) and any(
            isinstance(i.context_expr, ast.Call) and _func_name(i.context_expr) == "batch_alter_table"
            for i in w.items)), None)
        table = None
        if batch is not None:
            call = next(i.context_expr for i in batch.items if _func_name(i.context_expr) == "batch_alter_table")
            table = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else None
        elif _func_name(node) == "create_index" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            table = node.args[1].value
        elif isinstance(_kw(node, "table_name"), ast.Constant):
            table = _kw(node, "table_name").value
        table = table if table in BIG_TABLES else None if table else _table_of_index(name)
        if table is None:
            continue
        in_autocommit = any(isinstance(p, ast.With) and any(
            isinstance(i.context_expr, ast.Call) and _func_name(i.context_expr) == "autocommit_block"
            for i in p.items) for p in parents)
        if batch is not None or not (_is_const(_kw(node, "postgresql_concurrently"), True) and in_autocommit):
            out.add(f"{_func_name(node)} {name or '?'} on {table}")
    return out


@pytest.mark.parametrize("name", sorted(f for f in os.listdir(VERSIONS) if f.endswith(".py")))
def test_a_rolling_index_on_a_large_table_is_built_concurrently(name):
    text = _read("migrations", "versions", name)
    revision = re.search(r"^revision = ['\"]([^'\"]+)['\"]", text, re.M).group(1)
    if _rolling(text) is not True or revision in BIG_INDEX_BEFORE_THE_RULE:
        return
    steps = sorted(_big_index_steps(text))
    assert not steps, (f"{name} says ROLLING = True but {', '.join(steps)} would hold the table for the whole "
                       "build: postgresql_concurrently=True inside `with op.get_context().autocommit_block():`")


def test_the_index_rule_sees_what_it_should():
    assert _big_index_steps("def upgrade():\n    op.create_index('ix_response_x', 'response', ['x'])\n") \
        == {"create_index ix_response_x on response"}
    assert _big_index_steps("def upgrade():\n    op.drop_index('ix_response_x', table_name='response')\n") \
        == {"drop_index ix_response_x on response"}
    assert _big_index_steps("def upgrade():\n    with op.batch_alter_table('response') as b:\n"
                            "        b.create_index('ix_response_x', ['x'])\n") == {"create_index ix_response_x on response"}
    assert _big_index_steps("def upgrade():\n    op.execute('CREATE INDEX ix_r ON response (x)')\n")
    good = ("def upgrade():\n    with op.get_context().autocommit_block():\n"
            "        op.create_index('ix_response_y', 'response', ['y'], postgresql_concurrently=True)\n"
            "        op.drop_index('ix_response_x', table_name='response', postgresql_concurrently=True)\n"
            "    op.create_index('ix_survey_x', 'survey', ['x'])\n")
    assert _big_index_steps(good) == set()
    assert _big_index_steps("def upgrade():\n    op.create_index('ix_response_y', 'response', ['y'], "
                            "postgresql_concurrently=True)\n")  # concurrently, but inside the migration's transaction


def test_new_migrations_are_generated_with_the_mark():
    assert "ROLLING = False" in _read("migrations", "script.py.mako")


def test_compose_two_participants_with_the_shares_of_one():
    compose = _read("deploy", "docker", "compose.yaml")
    assert "  participant-a: &participant\n" in compose and "  participant-b: *participant\n" in compose
    assert "\n  participant:\n" not in compose
    block = compose[compose.index("  participant-a:"):compose.index("  participant-b:")]
    assert '"--workers", "12"' in block and '"--graceful-timeout", "60"' in block
    assert compose.count('"--keep-alive", "75"') == 4                        # longer than nginx's 60 s
    assert "cpu_shares: 1024" in block and "cpus: 20" in block and "stop_grace_period: 65s" in block
    assert "participant-a=http:http://participant-a:8000/healthz" in compose
    assert "participant-b=http:http://participant-b:8000/healthz" in compose
    assert "06-participant-upstream.sh:ro" in compose


def test_nginx_upstream_from_the_include_with_least_conn():
    conf = (_read("deploy", "docker", "nginx", "askurious.conf.template")
            + _read("deploy", "docker", "nginx", "locations", "participant.conf"))
    up = conf[conf.index("upstream participant {"):]
    up = up[:up.index("}")]
    assert "least_conn;" in up and "include /etc/nginx/upstream/participant-*.conf;" in up
    assert "server participant:8000" not in conf
    assert "proxy_next_upstream error timeout;" in conf and "error timeout non_idempotent" not in conf
    # on to the other container only while connecting, never a slow GET a second time (audit 2026-10-09, BT10)
    assert "proxy_next_upstream_timeout 5s;" in conf and "proxy_connect_timeout 2s;" in conf


@needs_bash
def test_upstream_script_keeps_one_in(tmp_path):
    script = _read("deploy", "docker", "nginx", "participant-upstream.sh").replace("/etc/nginx/upstream", "$UP")
    path = tmp_path / "up.sh"
    path.write_text(script.replace("nginx -t -q && nginx -s reload", "echo reloaded"), encoding="utf-8", newline="\n")
    env = dict(os.environ, UP=(tmp_path / "u").as_posix())

    def run(*args):
        return bounded_run.run([BASH, path.as_posix(), *args], env=env, text=True, timeout=60 * BASH_SLOW)

    assert run().returncode == 0
    a = tmp_path / "u" / "participant-a.conf"
    assert a.read_text().strip() == "server participant-a:8000 resolve max_fails=0;"
    out = run("participant-a", "down")
    assert out.returncode == 0 and "reloaded" in out.stdout and a.read_text().strip().endswith(" down;")
    out = run("participant-b", "down")                                         # never both
    assert out.returncode == 1 and "down already" in out.stderr
    assert run("participant-a", "up").returncode == 0 and " down" not in a.read_text()
    out = run("participant-a", "up", "--handoff")
    assert out.returncode == 0 and out.stdout.count("reloaded") == 1
    assert " down" not in a.read_text() and (tmp_path/"u"/"participant-b.conf").read_text().strip().endswith(" down;")
    assert run("participant-a", "down").returncode == 1
    assert run("participant-b", "up").returncode == 0
    assert run("participant-c", "down").returncode == 2


def _not_rolling(tmp_path, current, migrations):
    """restart.sh's not_rolling() on a folder of migrations {name: text}."""
    versions = tmp_path / "migrations" / "versions"
    versions.mkdir(parents=True)
    for name, text in migrations.items():
        (versions / name).write_text(text, encoding="utf-8", newline="\n")
    work = tmp_path / "deploy" / "docker"
    work.mkdir(parents=True)
    script = _read("deploy", "docker", "restart.sh")
    func = script[script.index("not_rolling() {"):]
    func = func[:func.index("\n}\n") + 3]
    out = bounded_run.run([BASH, "-c", func + f'\nnot_rolling "{current}"'], cwd=work, text=True, timeout=60 * BASH_SLOW)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def _migration(rev, down, rolling=None, since=None):
    mark = "" if rolling is None else f"ROLLING = {rolling}\n"
    mark += f'DROPS_UNUSED_SINCE = "{since}"\n' if since else ""
    return f"revision = '{rev}'\ndown_revision = {repr(down)}\n{mark}\ndef upgrade():\n    pass\n"


@needs_bash4
def test_restart_rolls_only_over_marked_migrations(tmp_path):
    files = {"0001_a.py": _migration("0001", None), "0002_b.py": _migration("0002", "0001", True),
             "0003_c.py": _migration("0003", "0002", True), "0004_d.py": _migration("0004", "0003")}
    assert _not_rolling(tmp_path / "1", "0004", files) == []                  # nothing pending
    assert _not_rolling(tmp_path / "2", "0003", files) == ["0004_d.py"]       # one without the mark
    files["0004_d.py"] = _migration("0004", "0003", False)
    assert _not_rolling(tmp_path / "3", "0003", files) == ["0004_d.py"]
    assert _not_rolling(tmp_path / "4", "0002", {**files, "0004_d.py": _migration("0004", "0003", True)}) == []
    assert _not_rolling(tmp_path / "5", "0009", files)[-1] == "?"               # a version this code does not know


@needs_bash4
def test_a_drop_rolls_only_once_the_code_before_no_longer_reads_it(tmp_path):
    """DROPS_UNUSED_SINCE (konto-schluessel-2b-vorschlag.md, E1 A): a migration that drops what the code of a
    revision stopped using rolls when the database is already there, never in the same deploy."""
    files = {"0001_a.py": _migration("0001", None), "0002_b.py": _migration("0002", "0001", True),
             "0003_c.py": _migration("0003", "0002", False, since="0002")}
    assert _not_rolling(tmp_path / "1", "0002", files) == []                  # the code of 0002 runs: it rolls
    assert _not_rolling(tmp_path / "2", "0001", files) == ["0003_c.py"]       # 0002 comes with it: refused
    files["0003_c.py"] = _migration("0003", "0002", False, since="0099")
    assert _not_rolling(tmp_path / "3", "0002", files) == ["0003_c.py"]       # an unknown revision: refused


@pytest.mark.parametrize("name", sorted(f for f in os.listdir(VERSIONS) if f.endswith(".py")))
def test_a_drop_says_since_when_nothing_reads_it(name):
    """A migration that drops a column or a table and wants to roll names the revision since which no code reads
    it; that revision has to be an earlier migration."""
    text = _read("migrations", "versions", name)
    since = re.search(r"^DROPS_UNUSED_SINCE = ['\"]([^'\"]+)['\"]", text, re.M)
    if since is None:
        return
    assert _rolling(text) is False, f"{name}: DROPS_UNUSED_SINCE goes with ROLLING = False (the check rolls it)"
    revision = re.search(r"^revision = ['\"]([^'\"]+)['\"]", text, re.M).group(1)
    assert since.group(1) < revision and any(f.startswith(since.group(1) + "_") for f in os.listdir(VERSIONS)), name


# --- "no longer read" means "no longer mapped" (audit 2026-10-18, FUNC-01) ------------------------------------------
# SQLAlchemy names every mapped column of a class in every SELECT of it. The models of 0084 still mapped the key
# columns 0085 drops, so participant-b, still on the code of 0084, answered 503 on every survey with a consent while
# participant-a restarted. A migration with DROPS_UNUSED_SINCE therefore lists what it drops (DROPS, "table.column"
# or "table"), and the models of the revision <since> must map none of it: the column left the models one deploy
# before (or the table its class).
BUILT_BEFORE_THE_RULE = {"0085"}  # deployed on 2026-10-18 before this rule; its short window is past


def _git(*args):
    """git's output in this checkout, or None without git or its history (a deploy archive, a copy in WSL)."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        return None
    try:
        out = bounded_run.run(["git", "-C", HERE, *args], text=True, encoding="utf-8", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _snake(name):
    """The table name Flask-SQLAlchemy gives a model class without __tablename__, from its own function."""
    from flask_sqlalchemy.model import camel_to_snake_case

    return camel_to_snake_case(name)


def _func_name(call):
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


COLUMN_CALLS = ("mapped_column", "Column", "column_property", "deferred", "composite")
# what an annotated `x: Mapped[...] = ...` may be besides a column: not a column of the table
NOT_COLUMNS = ("relationship", "backref", "association_proxy", "synonym", "query_expression", "dynamic_loader")


def _mapped_annotation(annotation):
    """Mapped[...] (also sqlalchemy.orm.Mapped[...] and a quoted "Mapped[...]")."""
    text = annotation.value if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str) \
        else ast.unparse(annotation)
    return re.match(r"\s*(\w+\.)*Mapped\[", text) is not None


def _mapped(sources):
    """{table: {column}} of what these model sources map: classes of db.Model (also through mixins and
    bases) and tables of db.Table(...). A column counts under its attribute name or the name given first."""
    classes, tables = {}, {}
    for source in sources:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes[node.name] = node
            elif isinstance(node, ast.Call) and _func_name(node) == "Table" and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                cols = {a.args[0].value for a in node.args[1:] if isinstance(a, ast.Call) and _func_name(a) == "Column"
                        and a.args and isinstance(a.args[0], ast.Constant)}
                tables.setdefault(node.args[0].value, set()).update(cols)

    def bases(cls):
        return [b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else "" for b in cls.bases]

    def own(cls):
        name, cols = None, set()
        for stmt in cls.body:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
            value = getattr(stmt, "value", None)
            for t in targets:
                if getattr(t, "id", "") == "__tablename__" and isinstance(value, ast.Constant):
                    name = value.value
                elif isinstance(t, ast.Name) and isinstance(value, ast.Call) and _func_name(value) in COLUMN_CALLS:
                    first = value.args[0] if value.args else None
                    cols.add(first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else t.id)
                elif isinstance(t, ast.Name) and isinstance(stmt, ast.AnnAssign) and _mapped_annotation(stmt.annotation) \
                        and not (isinstance(value, ast.Call) and _func_name(value) in NOT_COLUMNS):
                    cols.add(t.id)  # `x: Mapped[datetime | None]` maps a column without mapped_column() (audit B9)
        return name, cols

    def columns(cls, seen=()):
        cols = set(own(cls)[1])
        for b in bases(cls):
            if b in classes and b not in seen:
                cols |= columns(classes[b], (*seen, b))
        return cols

    def is_model(cls, seen=()):
        return any(b == "Model" or (b in classes and b not in seen and is_model(classes[b], (*seen, b))) for b in bases(cls))

    for name, cls in classes.items():
        if not is_model(cls):
            continue
        table = own(cls)[0] or _snake(name)
        tables.setdefault(table, set()).update(columns(cls))
    return tables


def _models_at(commit):
    """The model sources of the app at this commit: every module that maps columns."""
    listed = _git("grep", "-l", "-E", r"mapped_column|Column\(|db\.Table\(", commit, "--", "app")
    if listed is None:
        return None
    return [_git("show", line.strip()) or "" for line in listed.splitlines() if line.strip().endswith(".py")]


def _shallow():
    """Is this a shallow clone (actions/checkout without fetch-depth: 0)? Its oldest commit is a cut, not the commit
    that brought a file: git log --diff-filter=A names the cut for every file, whose models are today's, and the check
    below would find "nothing mapped" for any drop (CI on 2026-10-19). Unknown counts as shallow."""
    out = _git("rev-parse", "--is-shallow-repository")
    return out is None or out.strip() != "false"


def _first_commit_of(revision):
    """The commit that brought the migration `revision`: the oldest code that runs once the database is there.
    None without the whole history (no git, a deploy archive, a shallow clone): then the models of it are unknown."""
    if _shallow():
        return None
    name = next((f for f in os.listdir(VERSIONS) if f.startswith(revision + "_")), None)
    out = _git("log", "--diff-filter=A", "--format=%H", "--", f"migrations/versions/{name}") if name else None
    lines = (out or "").split()
    if not lines:
        return None
    # the public repository starts with one commit that brought every migration at once (2026-10-23): its models are
    # today's, so the models of `revision` are unknown there as in a shallow clone
    roots = (_git("rev-list", "--max-parents=0", "HEAD") or "").split()
    return None if lines[-1] in roots else lines[-1]


def _drops(text):
    """DROPS of a migration: ("table.column", "table", ...), or None when it has none."""
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DROPS" for t in node.targets):
            value = ast.literal_eval(node.value)
            assert isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value), "DROPS = ('t.c', ...)"
            return tuple(value)
    return None


def _literal_drops(source):
    """What an upgrade drops by literal name: op.drop_column("t", "c"), op.drop_table("t") and drop_column("c")
    in `with op.batch_alter_table("t")`."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.With):
            table = next((i.context_expr.args[0].value for i in node.items if isinstance(i.context_expr, ast.Call)
                          and _func_name(i.context_expr) == "batch_alter_table" and i.context_expr.args
                          and isinstance(i.context_expr.args[0], ast.Constant)), None)
            for inner in ast.walk(node):
                if table and isinstance(inner, ast.Call) and _func_name(inner) == "drop_column" and len(inner.args) == 1 \
                        and isinstance(inner.args[0], ast.Constant):
                    found.add(f"{table}.{inner.args[0].value}")
        elif isinstance(node, ast.Call) and _func_name(node) in ("drop_column", "drop_table") and node.args \
                and all(isinstance(a, ast.Constant) for a in node.args[:2]):
            names = [a.value for a in node.args[:2]]
            if _func_name(node) == "drop_table":
                found.add(names[0])
            elif len(names) == 2:
                found.add(f"{names[0]}.{names[1]}")
    return found


_RECORDED = {}


def _recorded_drops():
    """{revision: {"table.column", "table"}} of what each upgrade really drops, recorded while every migration runs
    on an empty SQLite database: also drops in a loop or a helper, whose names the syntax tree does not know (audit
    2026-10-21, B11: 0085 and 0091 drop in loops). The revision is the migration module on the call stack."""
    if _RECORDED:
        return _RECORDED
    import inspect as pyinspect

    from alembic import command
    from alembic.operations import BatchOperations, Operations
    from sqlalchemy import create_engine

    from app import migrations

    versions = os.path.normcase(os.path.abspath(VERSIONS))
    found = {}

    def revision():
        for info in pyinspect.stack(0):
            if os.path.normcase(os.path.dirname(os.path.abspath(info.filename))) == versions:
                return info.frame.f_globals.get("revision")
        return None

    def note(what):
        found.setdefault(revision(), set()).add(what)

    originals = {(cls, name): getattr(cls, name) for cls in (Operations, BatchOperations)
                 for name in ("drop_column", "drop_table") if name in vars(cls) or hasattr(cls, name)}

    def op_drop_column(self, table_name, column_name, *args, **kw):
        note(f"{table_name}.{column_name}")
        return originals[(Operations, "drop_column")](self, table_name, column_name, *args, **kw)

    def op_drop_table(self, table_name, *args, **kw):
        note(table_name)
        return originals[(Operations, "drop_table")](self, table_name, *args, **kw)

    def batch_drop_column(self, column_name, *args, **kw):
        note(f"{self.impl.table_name}.{column_name}")
        return originals[(BatchOperations, "drop_column")](self, column_name, *args, **kw)

    patches = {(Operations, "drop_column"): op_drop_column, (Operations, "drop_table"): op_drop_table,
               (BatchOperations, "drop_column"): batch_drop_column}
    try:
        for (cls, name), fn in patches.items():
            setattr(cls, name, fn)
        engine = create_engine("sqlite://")
        with engine.connect() as connection:
            migrations._run_on(connection, command.upgrade)
        engine.dispose()
    finally:
        for (cls, name), fn in originals.items():
            setattr(cls, name, fn)
    _RECORDED.update(found)
    return _RECORDED


def _still_mapped(drops, mapped):
    out = []
    for drop in drops:
        table, _, column = drop.partition(".")
        if table in mapped and (not column or column in mapped[table]):
            out.append(drop)
    return out


@pytest.mark.parametrize("name", sorted(f for f in os.listdir(VERSIONS) if f.endswith(".py")))
def test_a_rolling_drop_is_no_longer_mapped_by_the_code_before(name):
    """FUNC-01 (audit 2026-10-18): what a DROPS_UNUSED_SINCE migration drops is listed in DROPS, and the models of
    the revision <since> (the code the participant not yet restarted runs) map none of it."""
    text = _read("migrations", "versions", name)
    since = re.search(r"^DROPS_UNUSED_SINCE = ['\"]([^'\"]+)['\"]", text, re.M)
    revision = re.search(r"^revision = ['\"]([^'\"]+)['\"]", text, re.M)
    if since is None or revision.group(1) in BUILT_BEFORE_THE_RULE:
        return
    drops = _drops(text)
    assert drops, f"{name}: DROPS_UNUSED_SINCE needs DROPS = ('table.column', ...), what the upgrade drops"
    missing = (_literal_drops(_upgrade_source(text)) | _recorded_drops().get(revision.group(1), set())) - set(drops)
    assert not missing, f"{name}: drops {sorted(missing)} without naming it in DROPS"
    commit = _first_commit_of(since.group(1))
    sources = _models_at(commit) if commit else None
    if not sources:
        pytest.skip(f"{name}: no git history of revision {since.group(1)} here (deploy archive, shallow clone or a "
                    "copy without .git): its models cannot be read; the check runs in the development checkout")
    still = _still_mapped(drops, _mapped(sources))
    assert not still, (f"{name}: the models of {since.group(1)} (commit {commit[:8]}) still map {still}; the old "
                       "participant would fail on every query of them. Take them out of the models one deploy "
                       "before the drop (FUNC-01)")


def test_the_mapping_check_finds_what_0085_dropped_while_0084_mapped_it():
    """The check above would have refused 0085: the models of 0084 still mapped the key columns."""
    commit = _first_commit_of("0084")
    sources = _models_at(commit) if commit else None
    if not sources:
        pytest.skip("no whole git history here (deploy archive, shallow clone or a copy without .git): the models "
                    "of revision 0084 cannot be read; the check runs in the development checkout")
    dropped = [f"{t}.orcid" for t in ("survey_member", "workgroup_member", "organisation_member", "design_share",
                                      "design_editor", "construct_share", "contact")]
    assert _still_mapped(dropped, _mapped(sources)) == dropped
    import app.models as now

    with open(now.__file__, encoding="utf-8") as f:
        assert _still_mapped(dropped, _mapped([f.read()])) == []  # today's models map none of them


def test_the_mapping_check_skips_without_the_whole_history(monkeypatch):
    """FUNC-01 in CI (2026-10-19): a shallow clone names its cut as the commit that brought every migration, whose
    models are today's, so the check found "nothing mapped". Without the whole history it skips with a message, and
    ci.yml (only by hand for now) checks out the whole history so that it runs there."""
    import sys

    monkeypatch.setattr(sys.modules[__name__], "_git", lambda *args: "true\n" if args[0] == "rev-parse" else "x\n")
    assert _first_commit_of("0084") is None
    monkeypatch.setattr(sys.modules[__name__], "_git", lambda *args: None)  # no git at all
    assert _first_commit_of("0084") is None
    ci = _read(".github", "workflows", "ci.yml")
    tests_job = ci.split("\n  tests:", 1)[1].split("\n  vulnerabilities:", 1)[0]
    assert re.search(r"actions/checkout@\S+.*\n(\s+#.*\n|\s+with:\n)*\s+fetch-depth: 0", tests_job)


def test_the_drop_lists_are_read():
    source = ("def upgrade():\n    op.drop_column('a', 'x')\n    op.drop_table('b')\n"
              "    with op.batch_alter_table('c') as batch:\n        batch.drop_column('y')\n")
    assert _literal_drops(source) == {"a.x", "b", "c.y"}
    models = ("class Mixin:\n    x: Mapped[int] = mapped_column()\n"
              "class ThingRow(Mixin, db.Model):\n    y = mapped_column('z', Integer)\n"
              "class Other(db.Model):\n    __tablename__ = 'b'\n    id = mapped_column(primary_key=True)\n"
              "t = db.Table('link', db.Column('a_id', Integer))\n")
    mapped = _mapped([models])
    assert mapped["thing_row"] == {"x", "z"} and mapped["b"] == {"id"} and mapped["link"] == {"a_id"}
    assert _still_mapped(("thing_row.x", "thing_row.y", "b", "c"), mapped) == ["thing_row.x", "b"]


def test_a_column_mapped_by_its_annotation_alone_counts():
    """Audit 2026-10-21, B9: `used_at: Mapped[datetime | None]` maps a column without mapped_column(); a drop of it
    while the old models have it would break the old participant like FUNC-01. A relationship is no column."""
    models = ("class DiaryInvite(db.Model):\n    __tablename__ = 'diary_invite'\n"
              "    id: Mapped[int] = mapped_column(primary_key=True)\n"
              "    used_at: Mapped[datetime | None]\n"
              "    note: 'Mapped[str]'\n"
              "    survey: Mapped['Survey'] = relationship(back_populates='invites')\n")
    mapped = _mapped([models])
    assert mapped["diary_invite"] == {"id", "used_at", "note"}
    assert _still_mapped(("diary_invite.used_at",), mapped) == ["diary_invite.used_at"]


def test_drops_in_loops_are_recorded():
    """Audit 2026-10-21, B11: 0085 and 0091 drop in loops, which the literal reading misses; the recorded run sees
    them, so a drop missing from DROPS is caught."""
    recorded = _recorded_drops()
    assert recorded.get("0091") == set(_drops(_read("migrations", "versions", "0091_drop_rwth_designs.py")))
    assert {f"{t}.orcid" for t in ("survey_member", "design_share", "contact")} <= recorded.get("0085", set())
    assert not _literal_drops(_upgrade_source(_read("migrations", "versions", "0091_drop_rwth_designs.py")))


def test_the_old_index_list_names_only_what_it_has_to():
    for name in sorted(f for f in os.listdir(VERSIONS) if f.endswith(".py")):
        revision = name.split("_", 1)[0]
        if revision in BIG_INDEX_BEFORE_THE_RULE:
            assert _big_index_steps(_read("migrations", "versions", name)), f"{revision} needs no entry"


def test_status_whole_disrupted_only_when_both_are_down():
    groups = parse_redundant("participant-a|participant-b")
    assert groups == [{"participant-a", "participant-b"}]

    def svc(key, ok):
        return {"key": key, "last": {"ok": ok}}

    one = [svc("participant-a", False), svc("participant-b", True), svc("db", True)]
    assert disrupted(one, groups) == []
    both = [svc("participant-a", False), svc("participant-b", False), svc("db", True)]
    assert [s["key"] for s in disrupted(both, groups)] == ["participant-a", "participant-b"]
    db = [svc("participant-a", True), svc("participant-b", True), svc("db", False)]
    assert [s["key"] for s in disrupted(db, groups)] == ["db"]
