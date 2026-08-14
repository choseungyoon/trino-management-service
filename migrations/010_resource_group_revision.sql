-- Resource group editing (FR-WL-08/09/10, DESIGN_WL07.md).
--
-- Trino's own tables carry no history and no `reason` column, and absolute
-- rule 3 requires both for every write. The audit row records that someone
-- changed something and why; this table records what the configuration looked
-- like on either side of that change.
--
-- Kept out of audit_action.details on purpose: the audit log is append-only and
-- exported as CSV (FR-AA-05), and a full tree snapshot on every row would make
-- those exports unreadable. `request_id` joins the two.
--
-- Idempotent. Safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS resource_group_revision (
    id           BIGSERIAL    PRIMARY KEY,
    occurred_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- Trino scopes every resource group row by node.environment, so a revision
    -- belongs to one cluster's configuration rather than to the cluster name
    -- TMS happens to use.
    environment  VARCHAR(128) NOT NULL,

    actor        VARCHAR(256) NOT NULL,
    reason       TEXT         NOT NULL,
    request_id   UUID         NOT NULL,

    kind         VARCHAR(32)  NOT NULL,
    target       VARCHAR(512) NOT NULL,

    -- Whole-tree snapshots rather than field-level diffs. A tree is a handful
    -- of rows, so there is nothing to save by storing less, and reverting
    -- becomes "apply this snapshot" instead of replaying a diff backwards.
    tree_before  JSONB        NOT NULL,
    tree_after   JSONB        NOT NULL,

    CONSTRAINT resource_group_revision_kind_valid CHECK (
        kind IN (
            'group_create',
            'group_update',
            'group_delete',
            'selector_create',
            'selector_update',
            'selector_delete',
            'revert'
        )
    ),
    CONSTRAINT resource_group_revision_reason_present CHECK (
        btrim(reason) <> ''
    )
);

-- The history screen reads one environment, newest first.
CREATE INDEX IF NOT EXISTS resource_group_revision_env_time_idx
    ON resource_group_revision (environment, occurred_at DESC);

-- There is no UPDATE and no DELETE path, for the same reason the audit log has
-- none: a record that can be rewritten is not a record. Reverting appends.

ALTER TABLE audit_action
    DROP CONSTRAINT IF EXISTS audit_action_type_valid;

ALTER TABLE audit_action
    ADD CONSTRAINT audit_action_type_valid CHECK (
        action_type IN ('QUERY_KILL', 'HEALTH_TEST_TOGGLE', 'HEALTH_ROLLUP_TOGGLE',
                        'HEALTH_THRESHOLD_CHANGE', 'AUDIT_EXPORT',
                        'CLUSTER_RESTART', 'NODE_SHUTDOWN',
                        -- REVERT is its own action rather than a flag on
                        -- CHANGE: "how often do we undo these" is a question
                        -- worth being able to ask.
                        'RESOURCE_GROUP_CHANGE', 'RESOURCE_GROUP_REVERT')
    );

COMMIT;
