-- 018 — benchmark query sets, edited from the console.
--
-- 016 stored `benchmark_run.query_set` as a key into config, and said so:
-- "SQL here would mean the request chose what to execute". That was the right
-- shape while sets lived in a git-reviewed YAML file. It is the wrong shape
-- once an administrator is expected to add a query without a deploy, which is
-- what an administrator now needs, so the sets move here.
--
-- ⛔ What that costs, stated plainly: the read-only allowlist in
-- `bench/queryset.py` used to be a *startup* check on a file somebody had
-- reviewed. It is now the only thing between a pasted `DELETE` and N
-- unattended executions. It is therefore enforced twice - once when the row is
-- written, and again when the runner picks the row up - and every write lands
-- an audit record with a reason. See DECISIONS.md D-014.
--
-- `benchmark_run.queries` is the other half of the price. A set that can be
-- edited is a set whose *key* no longer identifies what ran: comparing run 3
-- with run 9 would silently compare two different statements. The
-- snapshot is what actually executed, and compare.py refuses to be quiet when
-- two runs disagree about it.
--
-- Run as the schema owner, then 019 for the grants.

BEGIN;

CREATE TABLE IF NOT EXISTS benchmark_query_set (
    id          BIGSERIAL    PRIMARY KEY,
    key         VARCHAR(64)  NOT NULL UNIQUE,
    title       VARCHAR(200) NOT NULL DEFAULT '',
    description TEXT         NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by  VARCHAR(128) NOT NULL,
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by  VARCHAR(128) NOT NULL,

    -- Same shape the application enforces (queryset.NAME_PATTERN). Duplicated
    -- here on purpose: the key ends up in `benchmark_run.query_set`, in URLs,
    -- and in comparisons, and a row written by hand with a space in the key
    -- would break all three long after whoever wrote it had left.
    CONSTRAINT benchmark_query_set_key_shape CHECK (
        key ~ '^[a-z0-9][a-z0-9_-]*$'
    )
);

CREATE TABLE IF NOT EXISTS benchmark_query (
    id         BIGSERIAL    PRIMARY KEY,
    set_id     BIGINT       NOT NULL
               REFERENCES benchmark_query_set(id) ON DELETE CASCADE,
    name       VARCHAR(64)  NOT NULL,
    title      VARCHAR(200) NOT NULL DEFAULT '',
    -- `statement`, not `sql`: `sql` is a keyword in enough tools that every
    -- ad-hoc query against this table would need quoting.
    statement  TEXT         NOT NULL,
    -- Execution order within the set. Two queries may share a position; the
    -- name breaks the tie, so ordering is total either way.
    position   INTEGER      NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by VARCHAR(128) NOT NULL,
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by VARCHAR(128) NOT NULL,

    -- Results are keyed by name (016: `benchmark_result.query_name`). Two
    -- queries sharing one name inside a set would merge into a single column
    -- of the comparison and read as one query that changed its mind.
    CONSTRAINT benchmark_query_unique_name UNIQUE (set_id, name),
    CONSTRAINT benchmark_query_name_shape CHECK (
        name ~ '^[a-z0-9][a-z0-9_-]*$'
    ),
    CONSTRAINT benchmark_query_statement_not_blank CHECK (
        btrim(statement) <> ''
    )
);

CREATE INDEX IF NOT EXISTS benchmark_query_by_set
    ON benchmark_query (set_id, position, name);

-- What actually ran, frozen at start. Empty for the runs taken before this
-- migration: those used config, which git still has.
ALTER TABLE benchmark_run
    ADD COLUMN IF NOT EXISTS queries JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE audit_action
    DROP CONSTRAINT IF EXISTS audit_action_type_valid;

ALTER TABLE audit_action
    ADD CONSTRAINT audit_action_type_valid CHECK (
        action_type IN ('QUERY_KILL', 'HEALTH_TEST_TOGGLE', 'HEALTH_ROLLUP_TOGGLE',
                        'HEALTH_THRESHOLD_CHANGE', 'AUDIT_EXPORT',
                        'CLUSTER_RESTART', 'NODE_SHUTDOWN',
                        'RESOURCE_GROUP_CHANGE', 'RESOURCE_GROUP_REVERT',
                        'FLEET_JOB_RUN', 'BENCHMARK_RUN', 'BENCHMARK_QUERY_CHANGE')
    );

COMMIT;
