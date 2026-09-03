-- 027 — config.properties edited in the console (D-018 §3).
--
-- ⛔ A change is a set of EDITS, not a file. The scan that feeds this screen
-- redacts credential-shaped values, so TMS's copy of a node's
-- config.properties holds the literal string [REDACTED] where the real
-- secrets are. Writing that copy back would replace a working keystore
-- password with that string on every node at once. So this table stores
-- "set this key / remove that key" and the playbook merges - every line TMS
-- never saw survives untouched.
--
-- ⛔ Why the gate, measured rather than assumed (TRINO_VERIFIED T1-8-1): an
-- unknown property name does not mean "that setting ignored". The server
-- refuses to start. A typo deployed to twenty nodes is twenty nodes down at
-- the same moment, which is exactly the accident this feature was asked for
-- to prevent.
--
-- Two gates answer different questions:
--   name check        a typo, caught against the list Trino itself printed
--                     at startup and the scan collected. Runs everywhere,
--                     including the development cluster.
--   development first everything a name check cannot see - a valid name whose
--                     value stops the server. One cluster pays that cost.
--
-- ⛔ No credential lands here. A credential-shaped key must carry
-- ${ENV:VAR}; the service refuses a literal when the row is written and
-- again immediately before it is deployed.

CREATE TABLE IF NOT EXISTS config_change (
    id            BIGSERIAL   PRIMARY KEY,
    title         TEXT        NOT NULL,

    -- 'all' | 'coordinator' | 'worker'. A closed vocabulary of three words,
    -- and it is what reaches Ansible as the host pattern. No host name ever
    -- does (D-009) - that is what makes mis-targeting impossible rather than
    -- unlikely.
    target_role   TEXT        NOT NULL,

    -- [{"key": ..., "action": "set"|"unset", "value": ...}]
    entries       JSONB       NOT NULL,
    notes         TEXT,

    -- Which cluster this was proved on, and when. NULL means nowhere, so it
    -- may only go to a development cluster. Cleared whenever the entries
    -- change: a change edited after it was proved is a different change, and
    -- carrying the mark over would let somebody prove one thing and ship
    -- another.
    verified_on   TEXT,
    verified_at   TIMESTAMPTZ,

    created_by    TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ,

    CONSTRAINT config_change_role_valid
        CHECK (target_role IN ('all', 'coordinator', 'worker')),
    CONSTRAINT config_change_title_present CHECK (btrim(title) <> ''),
    CONSTRAINT config_change_entries_present
        CHECK (jsonb_typeof(entries) = 'array' AND jsonb_array_length(entries) > 0)
);

-- Every deploy, whether it worked or not. Append-only: a change that stopped a
-- cluster booting is the row somebody will need most.
CREATE TABLE IF NOT EXISTS config_deployment (
    id            BIGSERIAL   PRIMARY KEY,
    change_id     BIGINT      REFERENCES config_change (id) ON DELETE SET NULL,
    title         TEXT        NOT NULL,
    cluster       TEXT        NOT NULL,
    target_role   TEXT        NOT NULL,
    -- ⛔ By value. The draft can be edited afterwards, and "what did we
    -- actually put on prod-a last Tuesday" has to stay answerable.
    entries       JSONB       NOT NULL,
    reason        TEXT        NOT NULL,
    actor         TEXT        NOT NULL,
    state         TEXT        NOT NULL DEFAULT 'RUNNING',
    detail        TEXT,
    -- What the playbook printed. Kept because the answer to "why did this
    -- fail" is in there and nowhere else.
    log           TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,

    CONSTRAINT config_deployment_state_valid
        CHECK (state IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    CONSTRAINT config_deployment_reason_present CHECK (btrim(reason) <> '')
);

CREATE INDEX IF NOT EXISTS config_deployment_cluster_idx
    ON config_deployment (cluster, started_at DESC);

-- ── audit ────────────────────────────────────────────────────────────
--
-- ⛔ DROP and re-CREATE, naming every action that exists at this point.

BEGIN;

ALTER TABLE audit_action DROP CONSTRAINT IF EXISTS audit_action_type_valid;
ALTER TABLE audit_action ADD CONSTRAINT audit_action_type_valid CHECK (
        action_type IN ('QUERY_KILL', 'HEALTH_TEST_TOGGLE', 'HEALTH_ROLLUP_TOGGLE',
                        'HEALTH_THRESHOLD_CHANGE', 'AUDIT_EXPORT',
                        'CLUSTER_RESTART', 'NODE_SHUTDOWN',
                        'RESOURCE_GROUP_CHANGE', 'RESOURCE_GROUP_REVERT',
                        'FLEET_JOB_RUN', 'BENCHMARK_RUN', 'BENCHMARK_QUERY_CHANGE',
                        'BENCHMARK_SCHEDULE_CHANGE',
                        'CATALOG_CHANGE', 'CATALOG_DEPLOY',
                        'CLUSTER_NODE_CHANGE',
                        'CONFIG_CHANGE', 'CONFIG_DEPLOY')
    );

COMMIT;
