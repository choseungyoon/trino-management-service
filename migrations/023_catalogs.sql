-- 023 — catalogs edited in the console and deployed to nodes (D-018 §2).
--
-- ⛔ Why this needs a gate at all, measured rather than assumed
-- (TRINO_VERIFIED T1-9-1): a catalog file Trino cannot load does not leave
-- "that catalog missing and the rest working". **The whole server refuses to
-- start.** An unknown connector, an unknown property inside a known connector,
-- and a `${ENV:VAR}` whose variable is absent all do the same thing. Deploying
-- a bad one to twenty-four nodes is twenty-four nodes that do not come back.
--
-- ⛔ And TMS cannot check it beforehand (T1-9-3). The connector list appears
-- only in the exception of a server that already failed; per-connector
-- property lists exist nowhere machine-readable. So the validator is not code
-- here - it is the development cluster, deployed to first, restarted, and
-- watched. `verified_on` / `verified_at` is that gate.
--
-- ⛔ No credential ever lands in this table. `connection-password` and its
-- relatives must be written as `${ENV:VAR}` (FR-CT-04, and T1-9-2 confirms
-- Trino resolves those from the node's process environment). The service
-- refuses a plaintext value on a credential-shaped key, when the row is
-- written and again before it is deployed.

CREATE TABLE IF NOT EXISTS catalog_definition (
    id            BIGSERIAL   PRIMARY KEY,
    name          TEXT        NOT NULL UNIQUE,
    connector     TEXT        NOT NULL,
    properties    JSONB       NOT NULL,
    notes         TEXT,

    -- Which cluster this draft was proven on, and when. NULL means it has
    -- never been deployed anywhere, so it may only go to a development
    -- cluster. Cleared whenever the properties change - a draft edited after
    -- it was proven is a different draft.
    verified_on   TEXT,
    verified_at   TIMESTAMPTZ,

    created_by    TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ,

    -- Trino reads `etc/catalog/<name>.properties`, so the name is a filename.
    CONSTRAINT catalog_definition_name_shape
        CHECK (name ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT catalog_definition_connector_present
        CHECK (btrim(connector) <> '')
);

-- Every deploy, whether it worked or not. Append-only: this is the record of
-- what was put on which cluster, and a catalog that broke a cluster is the
-- row somebody will need most.
CREATE TABLE IF NOT EXISTS catalog_deployment (
    id            BIGSERIAL   PRIMARY KEY,
    catalog_id    BIGINT      REFERENCES catalog_definition (id) ON DELETE SET NULL,
    catalog_name  TEXT        NOT NULL,
    cluster       TEXT        NOT NULL,
    action        TEXT        NOT NULL,
    -- ⛔ By value, not by reference. The draft can be edited afterwards, and
    -- "what did we actually put on prod-a last Tuesday" must stay answerable.
    properties    JSONB       NOT NULL,
    connector     TEXT        NOT NULL,
    state         TEXT        NOT NULL,
    detail        TEXT,
    reason        TEXT        NOT NULL,
    actor         TEXT        NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,

    CONSTRAINT catalog_deployment_action_valid
        CHECK (action IN ('deploy', 'remove')),
    CONSTRAINT catalog_deployment_state_valid
        CHECK (state IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    CONSTRAINT catalog_deployment_reason_present
        CHECK (btrim(reason) <> '')
);

CREATE INDEX IF NOT EXISTS catalog_deployment_recent_idx
    ON catalog_deployment (started_at DESC);

-- ── audit ────────────────────────────────────────────────────────────
--
-- ⛔ DROP and re-CREATE, naming every action that exists at this point. An
-- earlier migration re-run after this one removes what is added here and the
-- writes that need it start failing "audit unavailable".

BEGIN;

ALTER TABLE audit_action DROP CONSTRAINT IF EXISTS audit_action_type_valid;
ALTER TABLE audit_action ADD CONSTRAINT audit_action_type_valid CHECK (
        action_type IN ('QUERY_KILL', 'HEALTH_TEST_TOGGLE', 'HEALTH_ROLLUP_TOGGLE',
                        'HEALTH_THRESHOLD_CHANGE', 'AUDIT_EXPORT',
                        'CLUSTER_RESTART', 'NODE_SHUTDOWN',
                        'RESOURCE_GROUP_CHANGE', 'RESOURCE_GROUP_REVERT',
                        'FLEET_JOB_RUN', 'BENCHMARK_RUN', 'BENCHMARK_QUERY_CHANGE',
                        'BENCHMARK_SCHEDULE_CHANGE',
                        'CATALOG_CHANGE', 'CATALOG_DEPLOY')
    );

COMMIT;
