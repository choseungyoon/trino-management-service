import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError, api } from "../api";
import { Icon } from "../components/Icon";
import { Status } from "../components/Status";
import { useApi } from "../useApi";

interface Query {
  name: string;
  title: string;
  sql: string;
  position: number;
}

interface Run {
  id: number;
  cluster: string;
  label: string | null;
  state: string;
  actor: string;
  started_at: string;
  guard: { ok: boolean };
}

interface SetPage {
  set: { key: string; title: string; description: string; queries: Query[] };
  runs: Run[];
  can_edit: boolean;
  max_queries: number;
}

/** An editor's fields. `previous` empty means this is a new query. */
interface Draft {
  previous: string;
  name: string;
  title: string;
  statement: string;
  position: number;
  reason: string;
}

const BLANK: Draft = {
  previous: "", name: "", title: "", statement: "", position: 0, reason: "",
};

export function QuerySet() {
  const { key = "" } = useParams();
  const { data, error, reload } = useApi<SetPage>(
    `/benchmark/sets/${encodeURIComponent(key)}`);
  const navigate = useNavigate();
  // ⛔ One editor at a time. Two open on one set are two people about to
  // overwrite each other.
  const [draft, setDraft] = useState<Draft | null>(null);
  const [fresh, setFresh] = useState<Draft>(BLANK);
  // A new query goes on the end. The count is not known until the set loads,
  // so the default lands here rather than in BLANK - and only while the form
  // is untouched, so it never overwrites a position somebody typed.
  const count = data?.set.queries.length ?? 0;
  useEffect(() => {
    setFresh((current) =>
      current.name || current.statement ? current : { ...current, position: count });
  }, [count]);
  const [failure, setFailure] = useState<string | null>(null);

  async function guarded(work: () => Promise<unknown>) {
    setFailure(null);
    try {
      await work();
      reload();
      return true;
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : String(caught));
      return false;
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
  const { set } = data;

  async function saveQuery(event: React.FormEvent) {
    event.preventDefault();
    if (!draft) return;
    const saved = await guarded(() =>
      api.put(`/benchmark/sets/${set.key}/queries/${encodeURIComponent(draft.name)}`, {
        title: draft.title,
        statement: draft.statement,
        position: draft.position,
        reason: draft.reason,
        previous_name: draft.previous || null,
      }));
    if (saved) setDraft(null);
  }

  async function addQuery(event: React.FormEvent) {
    event.preventDefault();
    const added = await guarded(() =>
      api.put(`/benchmark/sets/${set.key}/queries/${encodeURIComponent(fresh.name)}`, {
        title: fresh.title,
        statement: fresh.statement,
        position: fresh.position,
        reason: fresh.reason,
      }));
    if (added) setFresh({ ...BLANK, position: count + 1 });
  }

  async function removeQuery() {
    if (!draft?.previous) return;
    const removed = await guarded(() =>
      api.del(`/benchmark/sets/${set.key}/queries/${
        encodeURIComponent(draft.previous)}?reason=${encodeURIComponent(draft.reason)}`));
    if (removed) setDraft(null);
  }

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">
          {set.title} <span className="dim mono">{set.key}</span>
        </span>
        <span className="spacer" />
        <Link className="btn btn--sm" to="/benchmark/sets">← Query sets</Link>
        <Link className="btn btn--primary btn--sm" to="/benchmark">Run it</Link>
      </header>

      <main className="content" id="main">
        {set.description ? <p className="dim">{set.description}</p> : null}

        {failure ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{failure}</div>
          </div>
        ) : null}

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">
              Queries {set.queries.length}
              <span className="dim">/{data.max_queries}</span>
            </div>
            <div className="panel__sub">
              Run in order of position, then by name. Edit a query to change or
              remove it.
            </div>
          </div>
          {set.queries.length ? (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">#</th>
                    <th scope="col">Name</th>
                    <th scope="col">SQL</th>
                    <th scope="col" />
                  </tr>
                </thead>
                <tbody>
                  {set.queries.map((query) =>
                    draft?.previous === query.name ? (
                      <tr className="row-editing" key={query.name}>
                        <td colSpan={4}>
                          <QueryForm draft={draft} setDraft={setDraft}
                                     onSubmit={saveQuery}
                                     submitLabel="Save"
                                     onRemove={removeQuery}
                                     onCancel={() => setDraft(null)} />
                        </td>
                      </tr>
                    ) : (
                      <tr key={query.name}>
                        <td className="num dim">{query.position}</td>
                        <td>
                          <span className="mono">{query.name}</span>
                          {query.title && query.title !== query.name ? (
                            <div className="dim">{query.title}</div>
                          ) : null}
                        </td>
                        <td className="wrap">
                          <code className="mono">
                            {query.sql.length > 160
                              ? `${query.sql.slice(0, 160)}…`
                              : query.sql}
                          </code>
                        </td>
                        <td className="row-actions">
                          <button className="btn" type="button"
                                  onClick={() => navigate(
                                    `/benchmark/sets/${set.key}/queries/${
                                      encodeURIComponent(query.name)}/history`)}>
                            History
                          </button>
                          {data.can_edit ? (
                            <button className="btn" type="button"
                                    onClick={() => setDraft({
                                      previous: query.name,
                                      name: query.name,
                                      // `title` falls back to the name when
                                      // nobody set one; pre-filling the box
                                      // with it would look like a description
                                      // somebody wrote.
                                      title: query.title === query.name ? "" : query.title,
                                      statement: query.sql,
                                      position: query.position,
                                      reason: "",
                                    })}>
                              Edit
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <Icon name="queries" size={20} stroke={1.6} />
              <div className="empty__title">No queries in this set</div>
              <div className="empty__desc">
                The set exists but there is nothing to run. Add the first query
                below.
              </div>
            </div>
          )}
        </section>

        {/* Open, not behind a button: adding a query is what this page is for,
            and the form is hidden only while a row above is being edited so
            there is never a second editor on the same set. */}
        {data.can_edit && !draft ? (
          <section className="panel">
            <div className="panel__head">
              <div className="panel__title">Add a query</div>
              <div className="panel__sub">
                One read-only statement — statements cannot be chained with{" "}
                <span className="mono">;</span>
              </div>
            </div>
            <QueryForm draft={fresh} setDraft={setFresh} onSubmit={addQuery}
                       submitLabel="Add" />
          </section>
        ) : null}

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">Recent runs</div>
            <div className="panel__sub">This set only</div>
          </div>
          {data.runs.length ? (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">#</th>
                    <th scope="col">Started</th>
                    <th scope="col">Cluster</th>
                    <th scope="col">State</th>
                    <th scope="col">Cluster was</th>
                    <th scope="col">By</th>
                  </tr>
                </thead>
                <tbody>
                  {data.runs.map((run) => (
                    <tr key={run.id}>
                      <td>
                        <Link className="mono" to={`/benchmark/runs/${run.id}`}>
                          #{run.id}
                        </Link>
                      </td>
                      <td className="mono num dim">
                        {new Date(run.started_at).toLocaleString()}
                      </td>
                      <td className="mono">
                        {run.cluster}
                        {run.label ? <div className="dim">{run.label}</div> : null}
                      </td>
                      <td><Status state={run.state} /></td>
                      <td>
                        {run.guard?.ok ? (
                          <span className="test-chip test-chip--good">Quiet</span>
                        ) : (
                          <span className="test-chip test-chip--concerning">
                            Serving traffic
                          </span>
                        )}
                      </td>
                      <td>{run.actor}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="panel__note">
              Not yet. Pick this set on the benchmark page and run it.
            </p>
          )}
        </section>

        {data.can_edit ? <SetDetails page={data} guarded={guarded} /> : null}
      </main>
    </>
  );
}

function QueryForm({ draft, setDraft, onSubmit, submitLabel, onCancel, onRemove }: {
  draft: Draft;
  setDraft: (draft: Draft) => void;
  onSubmit: (event: React.FormEvent) => void;
  submitLabel: string;
  onCancel?: () => void;
  onRemove?: () => void;
}) {
  const edit = <K extends keyof Draft>(field: K) =>
    (event: { target: { value: string } }) =>
      setDraft({ ...draft, [field]: field === "position"
        ? Number(event.target.value) : event.target.value });

  return (
    <form className="wi-form" onSubmit={onSubmit}>
      <div className="rg-add__grid">
        <label className="field">
          <span>Name <span className="dim">(results are keyed by this)</span></span>
          <input className="input mono" required maxLength={64}
                 pattern="[a-z0-9][a-z0-9_-]*" placeholder="scan_orders"
                 value={draft.name} onChange={edit("name")} />
        </label>
        <label className="field">
          <span>Position</span>
          <input className="input num" type="number" value={draft.position}
                 onChange={edit("position")} />
        </label>
      </div>
      <label className="field">
        <span>What it measures <span className="dim">(optional)</span></span>
        <input className="input" maxLength={200}
               placeholder="Wide scan over a partitioned table"
               value={draft.title} onChange={edit("title")} />
      </label>
      <label className="field">
        <span>SQL <span className="dim">(one read-only statement)</span></span>
        <textarea className="input mono" rows={8} required
                  placeholder="SELECT count(*) FROM hive.reporting.events WHERE dt = DATE '2026-08-01'"
                  value={draft.statement} onChange={edit("statement")} />
      </label>
      <label className="field">
        <span>Reason <span className="dim">(required)</span></span>
        <input className="input" required maxLength={500}
               placeholder="What you are changing, and why"
               value={draft.reason} onChange={edit("reason")} />
      </label>
      <div className="row-actions">
        {/* Removing needs the reason too, and it is one field away rather than
            a text input inside every table row. */}
        {onRemove ? (
          <button className="btn btn--danger" type="button"
                  disabled={!draft.reason.trim()} onClick={onRemove}>
            Remove
          </button>
        ) : null}
        <span className="spacer" />
        {onCancel ? (
          <button className="btn" type="button" onClick={onCancel}>Cancel</button>
        ) : null}
        <button className="btn btn--primary" type="submit">{submitLabel}</button>
      </div>
    </form>
  );
}

function SetDetails({ page, guarded }: {
  page: SetPage;
  guarded: (work: () => Promise<unknown>) => Promise<boolean>;
}) {
  const navigate = useNavigate();
  const [title, setTitle] = useState(page.set.title);
  const [description, setDescription] = useState(page.set.description);
  const [reason, setReason] = useState("");
  const [deleteReason, setDeleteReason] = useState("");

  return (
    <>
      <section className="panel">
        <div className="panel__head">
          <div className="panel__title">Set details</div>
          <div className="panel__sub">
            The ID cannot change — runs are recorded against it
          </div>
        </div>
        <form className="wi-form" onSubmit={async (event) => {
          event.preventDefault();
          if (await guarded(() => api.put(`/benchmark/sets/${page.set.key}`,
                                          { title, description, reason }))) {
            setReason("");
          }
        }}>
          <label className="field">
            <span>Name</span>
            <input className="input" maxLength={200} value={title}
                   onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="field">
            <span>What it measures</span>
            <input className="input" maxLength={500} value={description}
                   onChange={(e) => setDescription(e.target.value)} />
          </label>
          <label className="field">
            <span>Reason <span className="dim">(required)</span></span>
            <input className="input" required maxLength={500} value={reason}
                   onChange={(e) => setReason(e.target.value)} />
          </label>
          <div className="row-actions">
            <button className="btn btn--primary" type="submit">Save</button>
          </div>
        </form>
      </section>

      <section className="panel">
        <div className="panel__head">
          <div className="panel__title">Delete this set</div>
          <div className="panel__sub">
            Past runs and their measurements are left alone
          </div>
        </div>
        <form className="wi-form" onSubmit={async (event) => {
          event.preventDefault();
          if (await guarded(() => api.del(
            `/benchmark/sets/${page.set.key}?reason=${encodeURIComponent(deleteReason)}`))) {
            navigate("/benchmark/sets");
          }
        }}>
          <label className="field">
            <span>Reason <span className="dim">(required)</span></span>
            <input className="input" required maxLength={500}
                   placeholder="Why it is no longer needed"
                   value={deleteReason}
                   onChange={(e) => setDeleteReason(e.target.value)} />
          </label>
          <div className="row-actions">
            <button className="btn btn--danger" type="submit">Delete</button>
          </div>
        </form>
        <p className="panel__note">
          Runs hold this set by value, not by reference — each one carries a
          snapshot of the SQL it actually executed. Deleting removes the set
          from future runs and takes nothing away from the measurements already
          taken.
        </p>
      </section>
    </>
  );
}
