import { useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError, api } from "../api";
import { Icon } from "../components/Icon";
import { useApi } from "../useApi";

interface TimelineEntry {
  kind: "comment" | "status";
  at: string | null;
  actor: string;
  body?: string;
  from_label?: string;
  to_label?: string;
}

interface Item {
  key: string;
  title: string;
  kind: string;
  status: string;
  release: string | null;
  blocked_by: string | null;
  source_doc: string | null;
  body: string;
  created_by: string;
  created_at: string;
  timeline: TimelineEntry[];
}

const STATUSES = [
  ["needs_decision", "Needs a decision", "Waiting on a person"],
  ["blocked", "Blocked", "Waiting on something named"],
  ["in_progress", "In progress", "Being built now"],
  ["planned", "Planned", "Agreed and unblocked"],
  ["done", "Done", "Built and in the repository"],
  ["dropped", "Dropped", "Decided against"],
] as const;

export function WorkItem() {
  const { key = "" } = useParams();
  const { data, error, reload } = useApi<Item>(`/work/${encodeURIComponent(key)}`);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function run(work: () => Promise<unknown>) {
    setBusy(true);
    setFailure(null);
    try {
      await work();
      reload();
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <>
        <header className="topbar">
          <span className="topbar__title">{key}</span>
        </header>
        <main className="content" id="main">
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{error.message}</div>
          </div>
        </main>
      </>
    );
  }

  if (!data) return null;

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">
          <span className="mono">{data.key}</span> · {data.title}
        </span>
        <span className="spacer" />
        <Link className="btn btn--sm" to="/work">
          <Icon name="board" size={12} />
          Board
        </Link>
      </header>

      <main className="content" id="main">
        {data.blocked_by ? (
          <div className="banner banner--concerning" role="status">
            <Icon name="lock" size={15} />
            <div>
              <strong>Blocked by</strong> — {data.blocked_by}
            </div>
          </div>
        ) : null}

        {data.source_doc ? (
          /* A path, not a link. TMS does not serve the repository, and a link
             that 404s is worse than a path someone can paste into an editor. */
          <div className="banner" role="note">
            <Icon name="overview" size={15} />
            <div>
              <strong>The reasoning lives in a document</strong> —{" "}
              <span className="mono">{data.source_doc}</span>. Where this screen
              and that document disagree, the document is right.
            </div>
          </div>
        ) : null}

        {failure ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{failure}</div>
          </div>
        ) : null}

        <section className="panel">
          <div className="facts">
            <div className="fact">
              <div className="fact__value">
                <span className={`board__mark board__mark--${data.status}`}
                      aria-hidden="true" />
                {STATUSES.find((s) => s[0] === data.status)?.[1] ?? data.status}
              </div>
              <div className="fact__key">Status</div>
            </div>
            <div className="fact">
              <div className="fact__value">{data.kind}</div>
              <div className="fact__key">Kind</div>
            </div>
            <div className="fact">
              <div className="fact__value mono">{data.release ?? "—"}</div>
              <div className="fact__key">Release</div>
            </div>
            <div className="fact">
              <div className="fact__value">{data.created_by}</div>
              <div className="fact__key">
                Raised · {new Date(data.created_at).toLocaleString()}
              </div>
            </div>
          </div>
          {data.body ? <p className="panel__note wrap">{data.body}</p> : null}
        </section>

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">Move it</div>
            <div className="panel__sub">Your note is kept as a comment</div>
          </div>
          <MoveForm current={data.status} busy={busy}
                    onSubmit={(status, note) =>
                      run(() => api.put(`/work/${encodeURIComponent(data.key)}/status`,
                                        { status, note }))} />
        </section>

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">History</div>
            <div className="panel__sub">Comments and moves in one thread</div>
          </div>
          {data.timeline?.length ? (
            <ol className="wi-log">
              {data.timeline.map((entry, index) => (
                <li className={`wi-log__item wi-log__item--${entry.kind}`} key={index}>
                  <div className="wi-log__meta">
                    <span className="wi-log__who">{entry.actor}</span>
                    <span className="wi-log__at">
                      {entry.at ? new Date(entry.at).toLocaleString() : "—"}
                    </span>
                  </div>
                  {entry.kind === "status" ? (
                    <div className="wi-log__body">
                      {entry.from_label} → <strong>{entry.to_label}</strong>
                    </div>
                  ) : (
                    <div className="wi-log__body wrap">{entry.body}</div>
                  )}
                </li>
              ))}
            </ol>
          ) : (
            <p className="panel__note">Nothing has happened yet.</p>
          )}

          <CommentForm busy={busy}
                       onSubmit={(body) =>
                         run(() => api.post(
                           `/work/${encodeURIComponent(data.key)}/comments`, { body }))} />
        </section>
      </main>
    </>
  );
}

function MoveForm({ current, busy, onSubmit }: {
  current: string;
  busy: boolean;
  onSubmit: (status: string, note: string) => void;
}) {
  const [status, setStatus] = useState(current);
  const [note, setNote] = useState("");

  return (
    <form className="wi-form"
          onSubmit={(e) => { e.preventDefault(); onSubmit(status, note); setNote(""); }}>
      <label className="field">
        Status
        <select className="input" value={status}
                onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map(([value, label, meaning]) => (
            <option key={value} value={value}>
              {label} — {meaning}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>
          Note <span className="dim">(optional)</span>
        </span>
        <input className="input" value={note} maxLength={500}
               placeholder="Why you are moving it"
               onChange={(e) => setNote(e.target.value)} />
      </label>
      <div className="row-actions">
        <button className="btn btn--primary" type="submit" disabled={busy}>
          Move
        </button>
      </div>
    </form>
  );
}

function CommentForm({ busy, onSubmit }: {
  busy: boolean;
  onSubmit: (body: string) => void;
}) {
  const [body, setBody] = useState("");
  return (
    <form className="wi-form"
          onSubmit={(e) => { e.preventDefault(); onSubmit(body); setBody(""); }}>
      <label className="field">
        <span className="sr-only">Comment</span>
        <textarea className="input" rows={3} required value={body}
                  placeholder="Add a comment"
                  onChange={(e) => setBody(e.target.value)} />
      </label>
      <div className="row-actions">
        <button className="btn" type="submit" disabled={busy || !body.trim()}>
          Comment
        </button>
      </div>
    </form>
  );
}
