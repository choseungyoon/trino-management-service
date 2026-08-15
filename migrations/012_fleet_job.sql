-- 012 — fleet jobs (FR-FL-04/05).
--
-- A job is one configured Ansible playbook, run against one cluster, on an
-- operator's request. Scale-out is the case it was built for; anything else an
-- administrator declares in `fleet.jobs` works the same way.
--
-- Two tables, mirroring 004 and for the same two reasons.
--
-- `fleet_job_run` is the record that someone ran something. It outlives the
-- process on purpose: a job's subprocess dies with tms-api, so without this
-- there would be no trace that eight workers were half-provisioned when the
-- service was restarted.
--
-- `fleet_job_output` is the log the operator watches, append-only like
-- `audit_action` and `restart_sequence_event`. It is the record of what was
-- done to production, and a record that can be rewritten is not one.
--
-- ⛔ These runs are NOT the safe restart sequence. Nothing here checks that a
-- cluster was drained, because nothing here knows what the playbook does. That
-- is exactly why `fleet.jobs` must not be pointed at a playbook that restarts
-- anything - tms-config-check refuses that specific case, and D-009's reasoning
-- is why. Restarts go through FR-CO-02, which has the gates.
--
-- Run as the schema owner, then 013 for the grants.

BEGIN;

CREATE TABLE IF NOT EXISTS fleet_job_run (
    id          BIGSERIAL    PRIMARY KEY,
    cluster     VARCHAR(128) NOT NULL,
    -- The configured job's key, not a path. A path here would mean the request
    -- chose what to execute; the key only selects among what an administrator
    -- already declared.
    job         VARCHAR(64)  NOT NULL,
    state       VARCHAR(16)  NOT NULL,
    reason      TEXT         NOT NULL,
    actor       VARCHAR(128) NOT NULL,
    actor_roles TEXT[],
    -- Whatever typed parameters the job declared, as given. Integers only, and
    -- range-checked before they reach the playbook - see fleet/jobs.py.
    parameters  JSONB        NOT NULL DEFAULT '{}'::jsonb,
    started_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    exit_code   INTEGER,
    error       TEXT,

    CONSTRAINT fleet_job_run_reason_not_blank CHECK (btrim(reason) <> ''),
    CONSTRAINT fleet_job_run_state_valid CHECK (
        state IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')
    ),
    -- UNKNOWN is a finished state with no finish time on purpose: it means
    -- tms-api died while the job was running, so nobody can say when - or
    -- whether - it stopped. Claiming a timestamp there would be a guess.
    CONSTRAINT fleet_job_run_finished_consistent CHECK (
        (state IN ('SUCCEEDED', 'FAILED')) = (finished_at IS NOT NULL)
    )
);

-- One live job per cluster. Two scale-outs racing on the same inventory is not
-- a conflict to resolve afterwards.
CREATE UNIQUE INDEX IF NOT EXISTS fleet_job_run_one_active_per_cluster
    ON fleet_job_run (cluster) WHERE state = 'RUNNING';

CREATE INDEX IF NOT EXISTS fleet_job_run_started_at
    ON fleet_job_run (started_at DESC);

CREATE TABLE IF NOT EXISTS fleet_job_output (
    id          BIGSERIAL   PRIMARY KEY,
    run_id      BIGINT      NOT NULL REFERENCES fleet_job_run(id),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    level       VARCHAR(8)  NOT NULL DEFAULT 'output',
    message     TEXT        NOT NULL,

    CONSTRAINT fleet_job_output_message_not_blank CHECK (btrim(message) <> ''),
    CONSTRAINT fleet_job_output_level_valid CHECK (
        level IN ('info', 'output', 'warn', 'error')
    )
);

CREATE INDEX IF NOT EXISTS fleet_job_output_run
    ON fleet_job_output (run_id, id);

ALTER TABLE audit_action
    DROP CONSTRAINT IF EXISTS audit_action_type_valid;

ALTER TABLE audit_action
    ADD CONSTRAINT audit_action_type_valid CHECK (
        action_type IN ('QUERY_KILL', 'HEALTH_TEST_TOGGLE', 'HEALTH_ROLLUP_TOGGLE',
                        'HEALTH_THRESHOLD_CHANGE', 'AUDIT_EXPORT',
                        'CLUSTER_RESTART', 'NODE_SHUTDOWN',
                        'RESOURCE_GROUP_CHANGE', 'RESOURCE_GROUP_REVERT',
                        'FLEET_JOB_RUN')
    );

COMMIT;
