-- 004 — the safe restart sequence (FR-CO-02).
--
-- Two tables, for two different reasons.
--
-- `restart_sequence` is mutable state: one row per restart, carrying where the
-- sequence has got to. It has to survive a tms-api restart, because a sequence
-- that is forgotten mid-flight leaves a cluster deactivated and receiving no
-- traffic — an outage that looks like health, since every remaining cluster is
-- green.
--
-- `restart_sequence_event` is the progress log the operator watches, and it is
-- append-only for the same reason `audit_action` is: it is the record of what
-- was done to production. GRANTs in 002 are per-table, so 005 extends them.
--
-- One active sequence per cluster is enforced by a partial unique index rather
-- than by application logic. Two concurrent restarts of the same cluster is not
-- a race to resolve, it is an incident to prevent.
--
-- Run as the schema owner:
--   psql -h <db-host> -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/004_restart_sequence.sql

BEGIN;

CREATE TABLE IF NOT EXISTS restart_sequence (
    id              BIGSERIAL PRIMARY KEY,
    cluster         VARCHAR(128) NOT NULL,
    state           VARCHAR(32)  NOT NULL,
    reason          TEXT         NOT NULL,
    actor           VARCHAR(128) NOT NULL,
    actor_roles     TEXT[],
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    running_queries INTEGER,
    health_state    VARCHAR(16),
    force_reason    TEXT,

    CONSTRAINT restart_sequence_reason_not_blank CHECK (btrim(reason) <> ''),
    CONSTRAINT restart_sequence_state_valid CHECK (
        state IN ('PENDING', 'DRAINING', 'DRAINED', 'RESTARTING',
                  'VERIFYING', 'COMPLETED', 'ABORTING', 'ABORTED')
    ),
    -- A finished sequence must say when, and an unfinished one must not.
    CONSTRAINT restart_sequence_finished_consistent CHECK (
        (state IN ('COMPLETED', 'ABORTED')) = (finished_at IS NOT NULL)
    )
);

-- At most one live sequence per cluster.
CREATE UNIQUE INDEX IF NOT EXISTS restart_sequence_one_active_per_cluster
    ON restart_sequence (cluster)
    WHERE state NOT IN ('COMPLETED', 'ABORTED');

-- Finding the sequence that is still holding traffic back is the query the
-- whole UI hangs on, so it gets its own index.
CREATE INDEX IF NOT EXISTS restart_sequence_active
    ON restart_sequence (state) WHERE state NOT IN ('COMPLETED', 'ABORTED');

CREATE INDEX IF NOT EXISTS restart_sequence_started_at
    ON restart_sequence (started_at DESC);

CREATE TABLE IF NOT EXISTS restart_sequence_event (
    id          BIGSERIAL PRIMARY KEY,
    sequence_id BIGINT      NOT NULL REFERENCES restart_sequence(id),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    state       VARCHAR(32) NOT NULL,
    level       VARCHAR(8)  NOT NULL DEFAULT 'info',
    message     TEXT        NOT NULL,

    CONSTRAINT restart_sequence_event_message_not_blank CHECK (btrim(message) <> ''),
    CONSTRAINT restart_sequence_event_level_valid CHECK (
        level IN ('info', 'warn', 'error')
    )
);

CREATE INDEX IF NOT EXISTS restart_sequence_event_sequence
    ON restart_sequence_event (sequence_id, occurred_at, id);

COMMIT;
