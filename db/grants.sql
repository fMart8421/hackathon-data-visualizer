-- Read-only access for Grafana. Re-applied on every `make up`, so tables and
-- views added by later migrations are covered without a manual step.
--
-- Grafana must never be able to write: a panel query with a typo should fail,
-- not mutate the flight. Called with -v grafana_user=... from db/apply.sh.

GRANT USAGE ON SCHEMA public TO :"grafana_user";

GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"grafana_user";

-- Covers tables created by future migrations, which run as this same role.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO :"grafana_user";

-- Belt and braces: PostgreSQL 16 already keeps CREATE on public away from
-- PUBLIC, but the datasource role should never own objects either.
REVOKE CREATE ON SCHEMA public FROM :"grafana_user";
