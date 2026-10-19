-- The portal's own database account (#337): rows only. It reads and writes the tables, but owns none of them, so it
-- can't change a table or drop the triggers that keep the audit log and the page ledger append-only. Migrations keep
-- running as the owner (portal). scripts/deploy.sh runs this as the database superuser, in database portal, after
-- the migrations of every deploy, with __APP_PASSWORD__ filled in (hex, from deploy/k8s/portal.env); it's sent on
-- standard input, never as a command's argument. Safe to run again.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'portal_app') THEN
    CREATE ROLE portal_app LOGIN;
  END IF;
END $$;
ALTER ROLE portal_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '__APP_PASSWORD__';
GRANT CONNECT ON DATABASE portal TO portal_app;
GRANT USAGE ON SCHEMA public TO portal_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO portal_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO portal_app;
-- What migrations make later is the app's too
ALTER DEFAULT PRIVILEGES FOR ROLE portal IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO portal_app;
ALTER DEFAULT PRIVILEGES FOR ROLE portal IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO portal_app;
-- The migrations' own bookkeeping is the owner's alone
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'alembic_version') THEN
    REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM portal_app;
  END IF;
END $$;
