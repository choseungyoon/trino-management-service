-- 014 — the work board (admin-facing status and requests).
--
-- ⛔ This table does not replace the documents. That distinction is the whole
-- design, and this project has already been bitten by ignoring it: BACKLOG.md
-- and REQUIREMENTS.md appendix B disagreed about three features for weeks
-- because both claimed to say where things sat.
--
-- So the split is:
--
--   the DOCUMENT owns the reasoning   - why a decision went the way it did,
--                                       what a requirement means, what is
--                                       blocked and by what. Reviewed in Git.
--   this TABLE owns the status        - where the item sits today, and the
--                                       conversation about it. Changes often,
--                                       by people who do not send pull
--                                       requests.
--
-- Every item that has a document carries `source_doc`, and the screen says the
-- document wins. An item with no document is a request nobody has written up
-- yet, which is exactly the thing that had nowhere to live before this.
--
-- Run as the schema owner, then 015 for the grants.

BEGIN;

CREATE TABLE IF NOT EXISTS work_item (
    id          BIGSERIAL    PRIMARY KEY,
    -- Human-facing identifier: 'D-012', 'FR-BM-01', 'REQ-3'. Stable, because
    -- people will paste it into chat and expect it to still mean the same
    -- thing next month.
    key         VARCHAR(32)  NOT NULL UNIQUE,
    kind        VARCHAR(16)  NOT NULL,
    title       TEXT         NOT NULL,
    status      VARCHAR(16)  NOT NULL,
    release     VARCHAR(8),
    -- Free text on purpose. "what is blocking this" is a sentence, not an
    -- enum, and the sentence is the part that helps.
    blocked_by  TEXT,
    -- 'docs/DECISIONS.md#D-012'. Null for requests that have no write-up yet.
    source_doc  VARCHAR(256),
    body        TEXT,
    created_by  VARCHAR(128) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT work_item_title_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT work_item_kind_valid CHECK (
        kind IN ('decision', 'requirement', 'request', 'task')
    ),
    CONSTRAINT work_item_status_valid CHECK (
        status IN ('needs_decision', 'blocked', 'planned',
                   'in_progress', 'done', 'dropped')
    )
);

CREATE INDEX IF NOT EXISTS work_item_status ON work_item (status, key);
CREATE INDEX IF NOT EXISTS work_item_updated ON work_item (updated_at DESC);

-- Append-only, like every other record of what people said and did here.
-- There is no edit and no delete: a comment someone regrets is answered by
-- another comment, not by rewriting the first one.
CREATE TABLE IF NOT EXISTS work_item_comment (
    id         BIGSERIAL    PRIMARY KEY,
    item_id    BIGINT       NOT NULL REFERENCES work_item(id) ON DELETE CASCADE,
    author     VARCHAR(128) NOT NULL,
    body       TEXT         NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT work_item_comment_body_not_blank CHECK (btrim(body) <> '')
);

CREATE INDEX IF NOT EXISTS work_item_comment_item
    ON work_item_comment (item_id, id);

-- Status changes, so "who moved this to done and when" is answerable. Kept
-- here rather than in audit_action: that table is the record of actions taken
-- against Trino, and filling it with board bookkeeping would bury the rows
-- someone opens it to find.
CREATE TABLE IF NOT EXISTS work_item_event (
    id          BIGSERIAL    PRIMARY KEY,
    item_id     BIGINT       NOT NULL REFERENCES work_item(id) ON DELETE CASCADE,
    occurred_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    actor       VARCHAR(128) NOT NULL,
    from_status VARCHAR(16),
    to_status   VARCHAR(16)  NOT NULL
);

CREATE INDEX IF NOT EXISTS work_item_event_item ON work_item_event (item_id, id);

COMMIT;
