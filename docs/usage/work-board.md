# Work board

A small board for the things a platform team owes each other: decisions waiting
on a person, work blocked on something named, and requests raised from the
console.

Six columns — needs a decision, blocked, in progress, planned, done, dropped —
and **empty columns are still shown**. A column that vanishes when it empties
makes "nothing is blocked" and "I forgot to look" indistinguishable.

Always on; there is nothing to configure.

---

## What belongs here, and what does not

| | |
|---|---|
| **The board owns status** | What is waiting, blocked or in progress |
| **Documents own reasoning** | Why it was decided that way, what the alternatives were |

Each item points at the document that holds its reasoning, and the item shows
that path. **When the board and a document disagree, the document wins.**

Copying rationale onto a card creates a second source of truth that quietly
drifts — which is exactly how a backlog and a release plan in this project came
to contradict each other for weeks.

---

## Using it

**Comments and status changes share one timeline**, oldest first. "Moved to
blocked" and the comment explaining what blocked it are the same event to the
person reading; splitting them into two panels makes the reader reconstruct the
order from timestamps.

**Raising a request** gives it a `REQ-n` key. Viewers see the board and not the
form.

**Status change requires a note** for anything moving to blocked — a blocked
item without a named blocker is a card nobody can act on.

Comments and status events are **append-only** at the database level, the same
as the audit log. If `UPDATE work_item_comment` succeeds as `tms_app`, the
grants migration was not applied.

---

## Reading it from outside

The board lives in the database, which is often behind a network somebody
working on the code cannot reach. `tms-work-export` writes it to a markdown
file:

```bash
venv/bin/tms-work-export --output docs/WORK_BOARD.md
```

Commit that file. It is the only way to read the board from outside, and
without it "read the board before starting" is an instruction nobody can
follow.

The same content is available from the console at `/work.md`.

> ⛔ The exported file is generated. Editing it by hand produces a change that
> the next export silently discards.
