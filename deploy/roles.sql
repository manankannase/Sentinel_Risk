-- Run as the migration owner after migrations, in the Sentinel database.
-- Grant these NOLOGIN group roles to separately provisioned service login roles.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sentinel_api') THEN
    CREATE ROLE sentinel_api NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sentinel_worker') THEN
    CREATE ROLE sentinel_worker NOLOGIN;
  END IF;
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO sentinel_api, sentinel_worker;
GRANT SELECT ON schema_versions TO sentinel_api, sentinel_worker;
GRANT SELECT, INSERT ON transactions, feedback_events TO sentinel_api;
GRANT SELECT, INSERT, UPDATE ON accounts, feedback TO sentinel_api;
GRANT SELECT, INSERT ON outbox TO sentinel_api;
GRANT SELECT ON consumed_events TO sentinel_api;
GRANT SELECT, UPDATE ON outbox TO sentinel_worker;
GRANT SELECT, INSERT ON consumed_events, quarantine TO sentinel_worker;
-- API cannot UPDATE/DELETE transaction decisions or feedback history.
-- Worker cannot rewrite original transaction decisions.
