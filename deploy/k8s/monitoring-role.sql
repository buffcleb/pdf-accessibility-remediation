-- The Postgres exporter's database account (#397, MONITORING=on): Postgres's own statistics (pg_monitor) and no
-- table's rows. scripts/deploy.sh runs this as the database superuser, in database portal, on every deploy with
-- monitoring on, with __MONITOR_PASSWORD__ filled in (hex, from deploy/k8s/portal.env); it's sent on standard input,
-- never as a command's argument. Safe to run again.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pdfrem_monitor') THEN
    CREATE ROLE pdfrem_monitor LOGIN;
  END IF;
END $$;
ALTER ROLE pdfrem_monitor WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 3
  PASSWORD '__MONITOR_PASSWORD__';
GRANT pg_monitor TO pdfrem_monitor;
GRANT CONNECT ON DATABASE portal TO pdfrem_monitor;
