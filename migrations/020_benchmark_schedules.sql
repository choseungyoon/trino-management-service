-- 020 — benchmark schedules, edited from the console.
--
-- D-015 removed the "cluster must be out of rotation" gate on benchmarks, and
-- the reason it gave was this: what is actually needed is "운영 중인 클러스터의
-- 성능을 주기적으로 재서 추세를 보는 것". The gate went; the periodic part was
-- never built, so every point on the trend chart still needed somebody to
-- press a button. This is that half.
--
-- ⛔ A scheduled run has no human at the moment it executes, and absolute rule
-- 3 requires an actor and a reason on every write. Both are taken from the
-- schedule: `created_by` is the actor and `reason` is carried onto each run.
-- That is why `reason` is NOT NULL here and why it cannot be blanked later -
-- an unattended write with no recorded why is the thing the rule exists to
-- prevent.
--
-- ⛔ The period is `interval_minutes` + `next_run_at`, not a cron expression.
-- A cron parser is a dependency and a timezone argument; two columns express
-- "every day at 03:00", "every six hours" and "weekly" without either. What
-- they cannot express is "weekdays only" - when that is asked for, it is a new
-- column, not a cron string bolted on.
--
-- See DECISIONS.md D-017.

CREATE TABLE IF NOT EXISTS benchmark_schedule (
    id                   BIGSERIAL   PRIMARY KEY,
    name                 TEXT        NOT NULL UNIQUE,
    query_set            TEXT        NOT NULL,
    clusters             JSONB       NOT NULL,
    repetitions          INTEGER     NOT NULL DEFAULT 1,
    label                TEXT,
    reason               TEXT        NOT NULL,
    interval_minutes     INTEGER     NOT NULL,
    next_run_at          TIMESTAMPTZ NOT NULL,
    enabled              BOOLEAN     NOT NULL DEFAULT TRUE,

    -- Why it stopped, when TMS stopped it rather than a person. Separate from
    -- `enabled` so "somebody switched this off" and "this broke and was
    -- switched off for them" are different answers on the screen.
    paused_reason        TEXT,
    consecutive_failures INTEGER     NOT NULL DEFAULT 0,

    last_run_at          TIMESTAMPTZ,
    last_outcome         TEXT,
    created_by           TEXT        NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT benchmark_schedule_interval_positive CHECK (interval_minutes > 0),
    CONSTRAINT benchmark_schedule_repetitions_sane
        CHECK (repetitions BETWEEN 1 AND 50),
    CONSTRAINT benchmark_schedule_reason_present
        CHECK (btrim(reason) <> '')
);

-- The claim query orders by this and filters on it. Small table, but the
-- lookup runs on every scheduler tick.
CREATE INDEX IF NOT EXISTS benchmark_schedule_due_idx
    ON benchmark_schedule (next_run_at)
    WHERE enabled;

-- Which schedule produced a run, so the run list can say "this was not a
-- person". NULL for everything started by hand, which is every row before now.
ALTER TABLE benchmark_run
    ADD COLUMN IF NOT EXISTS schedule_id BIGINT
        REFERENCES benchmark_schedule (id) ON DELETE SET NULL;

-- ⛔ ON DELETE SET NULL, not CASCADE. Deleting a schedule must not delete the
-- measurements it took: the numbers outlive the reason they were collected,
-- and a trend chart that empties because somebody tidied up a schedule is a
-- trend chart nobody can trust.

-- ── audit ────────────────────────────────────────────────────────────
--
-- ⛔ DROP and re-CREATE, listing every action that exists at this point. If an
-- earlier migration is re-run after this one, the actions added here vanish
-- and the writes that need them start failing "audit unavailable". See the
-- ordering warning in docs/TODO.md.

BEGIN;

ALTER TABLE audit_action DROP CONSTRAINT IF EXISTS audit_action_type_valid;
ALTER TABLE audit_action ADD CONSTRAINT audit_action_type_valid CHECK (
        action_type IN ('QUERY_KILL', 'HEALTH_TEST_TOGGLE', 'HEALTH_ROLLUP_TOGGLE',
                        'HEALTH_THRESHOLD_CHANGE', 'AUDIT_EXPORT',
                        'CLUSTER_RESTART', 'NODE_SHUTDOWN',
                        'RESOURCE_GROUP_CHANGE', 'RESOURCE_GROUP_REVERT',
                        'FLEET_JOB_RUN', 'BENCHMARK_RUN', 'BENCHMARK_QUERY_CHANGE',
                        'BENCHMARK_SCHEDULE_CHANGE')
    );

COMMIT;
