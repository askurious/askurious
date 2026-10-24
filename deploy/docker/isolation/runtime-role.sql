-- DP-15 preparation: apply only as the local PostgreSQL administrator, after a backup (on a new server
-- db-app-role.sh --new-install applies it on the empty database).
-- Fixed existing migration owner askurious_app; a new role, with no inherited privileges.
-- No password in this file. Set a generated runtime password separately through stdin.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'askurious_app'
                 AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole) THEN
    RAISE EXCEPTION 'Expected restricted migration owner askurious_app';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'askurious_run') THEN
    RAISE EXCEPTION 'askurious_run already exists; inspect it instead of overwriting privileges';
  END IF;
  IF (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()) <> 'askurious_app'
     OR current_database() <> 'askurious' THEN
    RAISE EXCEPTION 'Expected askurious database owned by askurious_app';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S')
             AND pg_get_userbyid(c.relowner) <> 'askurious_app') THEN
    RAISE EXCEPTION 'Inspect objects with unexpected owners first';
  END IF;
END $$;
CREATE ROLE askurious_run NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE askurious FROM PUBLIC;
GRANT CONNECT ON DATABASE askurious TO askurious_app, askurious_run;
GRANT USAGE ON SCHEMA public TO askurious_run;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO askurious_run;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO askurious_run;
ALTER DEFAULT PRIVILEGES FOR ROLE askurious_app IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO askurious_run;
ALTER DEFAULT PRIVILEGES FOR ROLE askurious_app IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO askurious_run;
-- The runtime must never change the migration marker. A new server's still empty database has none yet
-- (db-app-role.sh --new-install): the migration job revokes it there after the first migration.
DO $$ BEGIN
  IF to_regclass('public.alembic_version') IS NOT NULL THEN
    REVOKE INSERT, UPDATE, DELETE ON TABLE public.alembic_version FROM askurious_run;
  END IF;
END $$;
COMMIT;
