-- 016 — benchmark runs (FR-BM-01/03/04).
--
-- A run is one declared query set, executed N times against one cluster, on an
-- operator's request. What it exists to answer is the question that put
-- FR-BENCHMARK into R2: "why is cluster A slower than cluster B".
--
-- ⛔ FR-BM-04 is not a policy this schema can enforce, and pretending otherwise
-- would be worse than being explicit. What the schema does hold is the
-- *evidence*: `guard` records what TMS checked before it agreed to start -
-- which backends it found deactivated, how many queries were running, how old
-- that observation was. A result whose guard says the cluster was still in
-- rotation is a result that must not be compared with anything, and six months
-- from now that column is the only way anybody could tell.
--
-- `benchmark_result` is append-only. A measurement is evidence, and evidence
-- that can be rewritten is not evidence. There is no delete: a run that should
-- not have happened still happened, and the guard column says why it did.
--
-- Run as the schema owner, then 017 for the grants.

BEGIN;

CREATE TABLE IF NOT EXISTS benchmark_run (
    id          BIGSERIAL    PRIMARY KEY,
    cluster     VARCHAR(128) NOT NULL,
    -- The declared set's key, not SQL. SQL here would mean the request chose
    -- what to execute; the key only selects among what an administrator
    -- already wrote into config (same reasoning as fleet_job_run.job).
    query_set   VARCHAR(64)  NOT NULL,
    label       VARCHAR(200),
    state       VARCHAR(16)  NOT NULL,
    reason      TEXT         NOT NULL,
    actor       VARCHAR(128) NOT NULL,
    actor_roles TEXT[],
    repetitions INTEGER      NOT NULL DEFAULT 1,
    -- What TMS verified before starting. See the header: this is the only
    -- thing that makes an old result trustworthy or not.
    guard       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    started_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    error       TEXT,

    CONSTRAINT benchmark_run_reason_not_blank CHECK (btrim(reason) <> ''),
    CONSTRAINT benchmark_run_repetitions_sane CHECK (
        repetitions BETWEEN 1 AND 20
    ),
    CONSTRAINT benchmark_run_state_valid CHECK (
        state IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'ABORTED', 'UNKNOWN')
    ),
    -- UNKNOWN carries no finish time, for the same reason as fleet_job_run:
    -- tms-api died mid-run and nobody can say when the last query stopped.
    CONSTRAINT benchmark_run_finished_consistent CHECK (
        (state IN ('SUCCEEDED', 'FAILED', 'ABORTED')) = (finished_at IS NOT NULL)
    )
);

-- One benchmark at a time per cluster. Two overlapping runs measure each other.
CREATE UNIQUE INDEX IF NOT EXISTS benchmark_run_one_active_per_cluster
    ON benchmark_run (cluster) WHERE state = 'RUNNING';

CREATE INDEX IF NOT EXISTS benchmark_run_started_at
    ON benchmark_run (started_at DESC);

-- Comparison (FR-BM-03) always asks "the same set, elsewhere or earlier".
CREATE INDEX IF NOT EXISTS benchmark_run_set_lookup
    ON benchmark_run (query_set, cluster, started_at DESC);

CREATE TABLE IF NOT EXISTS benchmark_result (
    id             BIGSERIAL    PRIMARY KEY,
    run_id         BIGINT       NOT NULL REFERENCES benchmark_run(id),
    query_name     VARCHAR(64)  NOT NULL,
    -- 1-based, so a report can say "run 3 of 5" without arithmetic.
    iteration      INTEGER      NOT NULL,
    -- Trino's own id. Without it a slow result cannot be looked up in the
    -- query history system afterwards, which is where the answer usually is.
    trino_query_id VARCHAR(64),
    state          VARCHAR(16)  NOT NULL,
    -- Wall time as TMS measured it: submission to last row. Always present,
    -- including for a failure, because "it failed after 40 seconds" and "it
    -- failed immediately" are different findings.
    elapsed_ms     BIGINT       NOT NULL,
    -- Trino's own numbers, from the final statement payload. Nullable: a query
    -- that failed before planning has none, and a zero there would read as a
    -- measurement rather than an absence.
    trino_elapsed_ms   BIGINT,
    trino_cpu_ms       BIGINT,
    trino_queued_ms    BIGINT,
    trino_planning_ms  BIGINT,
    processed_rows     BIGINT,
    processed_bytes    BIGINT,
    peak_memory_bytes  BIGINT,
    error          TEXT,
    occurred_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT benchmark_result_iteration_positive CHECK (iteration >= 1),
    CONSTRAINT benchmark_result_elapsed_not_negative CHECK (elapsed_ms >= 0),
    CONSTRAINT benchmark_result_state_valid CHECK (
        state IN ('SUCCEEDED', 'FAILED')
    ),
    CONSTRAINT benchmark_result_failure_has_error CHECK (
        state <> 'FAILED' OR btrim(coalesce(error, '')) <> ''
    )
);

CREATE INDEX IF NOT EXISTS benchmark_result_run
    ON benchmark_result (run_id, id);

ALTER TABLE audit_action
    DROP CONSTRAINT IF EXISTS audit_action_type_valid;

ALTER TABLE audit_action
    ADD CONSTRAINT audit_action_type_valid CHECK (
        action_type IN ('QUERY_KILL', 'HEALTH_TEST_TOGGLE', 'HEALTH_ROLLUP_TOGGLE',
                        'HEALTH_THRESHOLD_CHANGE', 'AUDIT_EXPORT',
                        'CLUSTER_RESTART', 'NODE_SHUTDOWN',
                        'RESOURCE_GROUP_CHANGE', 'RESOURCE_GROUP_REVERT',
                        'FLEET_JOB_RUN', 'BENCHMARK_RUN')
    );

COMMIT;
