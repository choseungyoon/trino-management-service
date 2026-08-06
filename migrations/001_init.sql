-- TMS schema, revision 001.
--
-- Target: dedicated PostgreSQL instance (DECISIONS.md D-004).
-- Deliberately NOT the Trino Gateway database - that one sits in the query
-- routing path and TMS must not add load to it (NFR-ISOLATION).
--
-- Design contract: docs/AUDIT_MODEL.md, docs/HEALTH_TESTS.md

BEGIN;

-- ---------------------------------------------------------------------------
-- Audit log (FR-AUDIT-ACTION)
--
-- Append-only. The application contains no UPDATE or DELETE path for this
-- table, and the grants at the bottom of this file remove the privilege as a
-- second line of defence (AUDIT_MODEL.md AU3).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_action (
    id              BIGSERIAL    PRIMARY KEY,
    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- Who: the real requester, never the tms-svc service account (AU4).
    actor           VARCHAR(256) NOT NULL,
    actor_roles     TEXT[]       NOT NULL DEFAULT '{}',
    actor_ip        INET,

    -- What
    action_type     VARCHAR(64)  NOT NULL,
    target_kind     VARCHAR(64)  NOT NULL,
    target_id       VARCHAR(512) NOT NULL,
    target_cluster  VARCHAR(128),

    -- Why: a blank reason is rejected at the API boundary and again here (AU2).
    reason          TEXT         NOT NULL,

    -- Result: failed actions are recorded too, including 403s (AU5).
    outcome         VARCHAR(16)  NOT NULL,
    error_message   TEXT,

    request_id      UUID         NOT NULL,
    details         JSONB,

    CONSTRAINT audit_action_reason_not_blank CHECK (btrim(reason) <> ''),
    CONSTRAINT audit_action_outcome_valid CHECK (outcome IN ('SUCCESS', 'FAILURE')),
    CONSTRAINT audit_action_type_valid CHECK (
        action_type IN (
            'QUERY_KILL',
            'HEALTH_TEST_TOGGLE',
            'HEALTH_ROLLUP_TOGGLE',
            'HEALTH_THRESHOLD_CHANGE',
            'AUDIT_EXPORT'
        )
    )
);

COMMENT ON TABLE audit_action IS
    'Append-only operational audit log. R1 action types only; adding a type '
    'requires a requirements change (AUDIT_MODEL.md section 3).';
COMMENT ON COLUMN audit_action.actor IS
    'The human who requested the action, not the Trino service account.';
COMMENT ON COLUMN audit_action.reason IS
    'Mandatory. For QUERY_KILL this text is forwarded to Trino and shown to the '
    'user whose query was killed.';

CREATE INDEX IF NOT EXISTS audit_action_occurred_idx
    ON audit_action (occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS audit_action_actor_idx
    ON audit_action (actor, occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_action_target_idx
    ON audit_action (target_kind, target_id);

-- ---------------------------------------------------------------------------
-- Health state transitions (FR-CH-07)
--
-- Written only when a test changes state, and only after it has held the new
-- state for `health.stabilization_polls` consecutive polls. Recording every
-- poll would bury the signal (HEALTH_TESTS.md section 5).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS health_event (
    id              BIGSERIAL    PRIMARY KEY,
    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),

    cluster         VARCHAR(128) NOT NULL,
    test_id         VARCHAR(32)  NOT NULL,
    from_state      VARCHAR(16)  NOT NULL,
    to_state        VARCHAR(16)  NOT NULL,

    observed_value  TEXT,
    threshold       TEXT,
    -- Required whenever the new state is BAD or CONCERNING: an alert with no
    -- remedy wastes the responder's time (TEAMS.md sre-agent rule 1).
    advice          TEXT,

    CONSTRAINT health_event_states_valid CHECK (
        from_state IN ('GOOD', 'CONCERNING', 'BAD', 'UNKNOWN')
        AND to_state IN ('GOOD', 'CONCERNING', 'BAD', 'UNKNOWN')
    ),
    CONSTRAINT health_event_state_changed CHECK (from_state <> to_state),
    CONSTRAINT health_event_advice_present CHECK (
        to_state NOT IN ('BAD', 'CONCERNING') OR btrim(coalesce(advice, '')) <> ''
    )
);

CREATE INDEX IF NOT EXISTS health_event_cluster_idx
    ON health_event (cluster, occurred_at DESC);
CREATE INDEX IF NOT EXISTS health_event_test_idx
    ON health_event (test_id, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- Latest collector snapshot
--
-- One row per (cluster, kind). The collector replaces rows in place; the API
-- reads them. `collected_at` drives the stale badge - the API must never
-- present a stale snapshot as current (ARCHITECTURE.md section 2-1).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS collector_snapshot (
    cluster         VARCHAR(128) NOT NULL,
    kind            VARCHAR(32)  NOT NULL,
    collected_at    TIMESTAMPTZ  NOT NULL,
    payload         JSONB        NOT NULL,
    -- Set when the collector reached the coordinator but could not interpret
    -- the answer, e.g. a permission denial. Distinguishes "no data" from
    -- "everything is fine".
    collection_error TEXT,

    PRIMARY KEY (cluster, kind),
    CONSTRAINT collector_snapshot_kind_valid CHECK (
        kind IN ('queries', 'jmx', 'info', 'health')
    )
);

COMMENT ON COLUMN collector_snapshot.collection_error IS
    'Non-null means the snapshot is not trustworthy. The API surfaces this as '
    'UNKNOWN rather than as healthy or empty.';

-- ---------------------------------------------------------------------------
-- Health test overrides (FR-CH-03, FR-CH-04, FR-CH-05)
--
-- Absent row means "use the defaults from config.yaml".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS health_test_override (
    cluster         VARCHAR(128) NOT NULL,
    -- '*' addresses the roll-up rather than an individual test (FR-CH-04).
    test_id         VARCHAR(32)  NOT NULL,
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    thresholds      JSONB,
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by      VARCHAR(256) NOT NULL,

    PRIMARY KEY (cluster, test_id)
);

COMMENT ON TABLE health_test_override IS
    'Every change here is also written to audit_action: disabling a health test '
    'narrows what operators can see, so it leaves a trace.';

COMMIT;

-- Privileges live in 002_grants.sql. The application role is granted only
-- SELECT and INSERT on the append-only tables, so the guarantee holds by
-- construction rather than by revoking something first (AUDIT_MODEL.md AU3).
-- See docs/runbooks/db-setup.md for the full procedure.
