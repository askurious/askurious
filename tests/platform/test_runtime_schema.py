"""DP-15: a runtime connection validates its schema and never repairs it."""
import pytest
from sqlalchemy import create_engine, inspect, text

from app import create_app, migrations
from app.extensions import db
from tests.conftest import _config


def test_runtime_start_does_not_run_schema_changes(app, monkeypatch):
    with app.app_context():
        app.config["DATABASE_SCHEMA_MODE"] = "check"
        monkeypatch.setattr(migrations, "_run", lambda *a, **k: pytest.fail("runtime migrated"))
        monkeypatch.setattr(migrations, "_lock", lambda *a: pytest.fail("runtime acquired migration lock"))
        assert migrations.upgrade(app) == "current"


@pytest.mark.parametrize("revision", [None, "0001", "unknown-newer-version"])
def test_runtime_refuses_missing_old_and_unknown_schema(app, monkeypatch, revision):
    with app.app_context():
        app.config["DATABASE_SCHEMA_MODE"] = "check"
        monkeypatch.setattr(migrations, "current", lambda: revision)
        monkeypatch.setattr(migrations, "_lock", lambda *a: pytest.fail("runtime acquired migration lock"))
        with pytest.raises(migrations.SchemaMismatch, match="separate migration job"):
            migrations.upgrade(app)


def test_runtime_mode_rejects_typo(app):
    with app.app_context():
        app.config["DATABASE_SCHEMA_MODE"] = "chek"
        with pytest.raises(migrations.SchemaMismatch, match="auto or check"):
            migrations.upgrade(app)


def test_empty_runtime_database_is_not_created(tmp_path):
    config = _config(tmp_path)
    config.DATABASE_SCHEMA_MODE = "check"
    with pytest.raises(migrations.SchemaMismatch):
        create_app(config)
    engine = create_engine(config.SQLALCHEMY_DATABASE_URI)
    assert inspect(engine).get_table_names() == []
    engine.dispose()


def test_full_runtime_app_starts_without_creating_index(app, tmp_path, monkeypatch):
    from app import linked

    with app.app_context():
        address = str(db.engine.url)
    config = _config(tmp_path, address)
    config.DATABASE_SCHEMA_MODE = "check"
    monkeypatch.setattr(linked, "ensure_index", lambda: pytest.fail("runtime created index"))
    runtime = create_app(config)
    with runtime.app_context():
        assert migrations.current() == migrations.head()
        assert db.session.execute(text("SELECT 1")).scalar() == 1
        db.engine.dispose()


@pytest.mark.parametrize("url", [None, "", "sqlite:///:memory:"])
def test_deployment_job_requires_explicit_postgres(url):
    from app.schema_migrate import run

    with pytest.raises(ValueError, match="PostgreSQL"):
        run(url)


def test_grants_only_requires_runtime_role_before_opening_database():
    from app.schema_migrate import run

    with pytest.raises(ValueError, match="requires RUNTIME_ROLE"):
        run("postgresql://owner:synthetic@db/d", grants_only=True)


@pytest.mark.parametrize("role", ["", "owner; DROP TABLE user", 'owner"', "Owner", "a" * 64])
def test_runtime_grant_identifier_is_validated_before_sql(role):
    from app.schema_migrate import grant_runtime

    with pytest.raises(ValueError, match="identifier"):
        grant_runtime(role)


class _Stop(Exception):
    pass


def test_the_migration_job_has_the_keys_of_stored_secrets(monkeypatch):
    """Audit 2026-10-21, B12: 0056 locks the dashboard and quota links with app/secrets_box.py, but the job's bare
    Flask app had neither SECRET_KEY nor TOKEN_KEY (TypeError, the deploy stopped). The job takes them from its
    environment, and nothing when they are missing (no development default that would lock with a wrong key)."""
    from flask import Flask

    from app import schema_migrate, secrets_box

    for name in schema_migrate.SECRET_KEYS + schema_migrate.SECRET_FALLBACKS:
        monkeypatch.delenv(name, raising=False)
    assert schema_migrate.secret_settings() == {}
    monkeypatch.setenv("SECRET_KEY", "synthetic-secret")
    monkeypatch.setenv("TOKEN_KEY", "synthetic-token")
    monkeypatch.setenv("TOKEN_KEY_FALLBACKS", "older, oldest")
    seen = {}

    def init_app(app):
        seen.update(app.config)
        raise _Stop

    monkeypatch.setattr(schema_migrate.db, "init_app", init_app)
    with pytest.raises(_Stop):
        schema_migrate.run("postgresql://owner:synthetic@db/d")
    assert seen["SECRET_KEY"] == "synthetic-secret" and seen["TOKEN_KEY"] == "synthetic-token"
    assert seen["TOKEN_KEY_FALLBACKS"] == ["older", "oldest"] and not seen.get("SECRET_KEY_FALLBACKS")
    job = Flask("schema_migrate")
    job.config.update(**schema_migrate.secret_settings())
    with job.app_context():
        token = secrets_box.encrypt("abc")  # TypeError before the fix
    later = Flask("app")
    later.config.update(SECRET_KEY="synthetic-secret", TOKEN_KEY="synthetic-token")
    with later.app_context():
        assert secrets_box.decrypt(token) == "abc"  # the app opens what the job locked
