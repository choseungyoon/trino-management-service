import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { ApiError, api } from "../api";
import { Icon } from "../components/Icon";
import { useApi } from "../useApi";

interface SetRow {
  key: string;
  title: string;
  description: string;
  queries: { name: string }[];
  has_runs: boolean;
}

export function QuerySets() {
  const { data, error, reload } = useApi<{ sets: SetRow[]; can_edit: boolean }>(
    "/benchmark/sets");
  const navigate = useNavigate();
  const [form, setForm] = useState({
    key: "", title: "", description: "", name: "", statement: "", reason: "",
  });
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const set = (field: keyof typeof form) =>
    (event: { target: { value: string } }) =>
      setForm((current) => ({ ...current, [field]: event.target.value }));

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFailure(null);
    try {
      await api.post("/benchmark/sets", form);
      reload();
      navigate(`/benchmark/sets/${form.key}`);
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Query sets</span>
        <span className="spacer" />
        <Link className="btn btn--sm" to="/benchmark">← Benchmark</Link>
      </header>

      <main className="content" id="main">
        {error && !data ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{error.message}</div>
          </div>
        ) : null}

        {/* Sets used to live in a config file, where a pull request reviewed
            every SQL statement before it shipped. They are edited here now so
            that adding a query does not need a deploy — and this banner is the
            price of that: the allowlist and the audit record are what replaced
            the review. */}
        <div className="banner" role="note">
          <Icon name="lock" size={15} />
          <div>
            <strong>
              Whatever you write here will later run N times against a cluster.
            </strong>{" "}
            Only read-only statements are accepted (
            <span className="mono">SELECT · WITH · SHOW · EXPLAIN · DESCRIBE ·
            VALUES · TABLE</span>), checked when you save and again immediately
            before each execution. No result rows are ever shown — the only
            thing a run gives back is timings.
          </div>
        </div>

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">Sets</div>
            <div className="panel__sub">
              A set is the queries that run together and get compared together
            </div>
          </div>
          {data?.sets.length ? (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Set</th>
                    <th scope="col">Queries</th>
                    <th scope="col">Has been run</th>
                    <th scope="col">What it measures</th>
                  </tr>
                </thead>
                <tbody>
                  {data.sets.map((row) => (
                    <tr key={row.key}>
                      <td>
                        <Link to={`/benchmark/sets/${row.key}`}>{row.title}</Link>
                        <div className="dim mono">{row.key}</div>
                      </td>
                      <td className="num">{row.queries.length}</td>
                      <td>
                        {row.has_runs ? (
                          <span className="test-chip test-chip--good">Yes</span>
                        ) : (
                          <span className="dim">Not yet</span>
                        )}
                      </td>
                      <td className="wrap dim">{row.description || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <Icon name="queries" size={20} stroke={1.6} />
              <div className="empty__title">No sets yet</div>
              <div className="empty__desc">
                Nothing ships by default. A bundled set would have to name a
                catalog, and which catalogs exist is a fact about this
                deployment — a default that fails on first use teaches you the
                feature is broken. Create one below.
              </div>
            </div>
          )}
        </section>

        {data?.can_edit ? (
          <section className="panel">
            <div className="panel__head">
              <div className="panel__title">New query set</div>
              <div className="panel__sub">
                The set, plus one query to start it off — add the rest on the
                set's own page
              </div>
            </div>
            {failure ? (
              <div className="banner banner--bad" role="alert"
                   style={{ margin: "0 16px 10px" }}>
                <Icon name="bad" size={15} />
                <div>{failure}</div>
              </div>
            ) : null}
            <form className="wi-form" onSubmit={create}>
              <div className="rg-add__grid">
                <label className="field">
                  <span>
                    Set ID{" "}
                    <span className="dim">
                      (lowercase, digits, <span className="mono">-</span>,{" "}
                      <span className="mono">_</span>)
                    </span>
                  </span>
                  <input className="input mono" required maxLength={64}
                         pattern="[a-z0-9][a-z0-9_-]*" placeholder="nightly"
                         value={form.key} onChange={set("key")} />
                </label>
                <label className="field">
                  <span>Name <span className="dim">(optional)</span></span>
                  <input className="input" maxLength={200}
                         placeholder="Nightly regression set"
                         value={form.title} onChange={set("title")} />
                </label>
              </div>
              <label className="field">
                <span>What it measures <span className="dim">(optional)</span></span>
                <input className="input" maxLength={500}
                       placeholder="The question you want this set to answer"
                       value={form.description} onChange={set("description")} />
              </label>

              <fieldset className="fieldgroup">
                {/* "First" as in the one that comes with the set, not as in a
                    first name. A set with no queries cannot be run, so one is
                    created together with it and the rest are added after. */}
                <legend className="fieldgroup__legend">
                  The query this set starts with
                </legend>
                <label className="field">
                  <span>
                    Query name{" "}
                    <span className="dim">(results are keyed by this)</span>
                  </span>
                  <input className="input mono" required maxLength={64}
                         pattern="[a-z0-9][a-z0-9_-]*" placeholder="scan_orders"
                         value={form.name} onChange={set("name")} />
                </label>
                <label className="field">
                  <span>
                    SQL{" "}
                    <span className="dim">
                      (one read-only statement, no trailing{" "}
                      <span className="mono">;</span> needed)
                    </span>
                  </span>
                  <textarea className="input mono" rows={8} required
                            placeholder="SELECT count(*) FROM hive.reporting.events WHERE dt = DATE '2026-08-01'"
                            value={form.statement} onChange={set("statement")} />
                </label>
              </fieldset>

              <label className="field">
                <span>Reason <span className="dim">(required)</span></span>
                <input className="input" required maxLength={500}
                       placeholder="Why this set is needed"
                       value={form.reason} onChange={set("reason")} />
              </label>
              <div className="row-actions">
                <button className="btn btn--primary" type="submit" disabled={busy}>
                  {busy ? "Creating…" : "Create"}
                </button>
              </div>
            </form>
            <p className="panel__note">
              The set ID cannot be changed later. Runs are recorded against it
              and only runs of the same set can be compared, so renaming it
              would orphan every measurement taken so far.
            </p>
          </section>
        ) : null}
      </main>
    </>
  );
}
