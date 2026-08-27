import { useState } from "react";
import { Link } from "react-router";

import { ApiError, api } from "../api";
import { Icon } from "../components/Icon";
import { Status } from "../components/Status";
import { useApi } from "../useApi";

interface ClusterRow {
  name: string;
  ready: boolean;
  exclusive: boolean;
  busy: boolean;
  guard: { advice: { code: string; text: string }[] };
}

interface QuerySet {
  key: string;
  title: string;
  description: string;
  queries: { name: string }[];
}

interface Run {
  id: number;
  schedule_id: number | null;
  cluster: string;
  query_set: string;
  label: string | null;
  repetitions: number;
  state: string;
  guard: { ok: boolean };
  reason: string;
  actor: string;
  started_at: string;
}

interface Overview {
  clusters: ClusterRow[];
  query_sets: QuerySet[];
  runs: Run[];
  can_start: boolean;
  can_edit: boolean;
  max_repetitions: number;
}

export function Benchmark() {
  // While a run is in flight the picker has to come back on its own; the rest
  // of the form must not be thrown away with it, which is why only this
  // screen's data reloads rather than the page.
  const { data, error, reload } = useApi<Overview>("/benchmark", 5_000);
  const [chosen, setChosen] = useState<string[]>([]);
  const [set, setSet] = useState("");
  const [reps, setReps] = useState(3);
  const [label, setLabel] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const toggle = (name: string) =>
    setChosen((current) =>
      current.includes(name) ? current.filter((n) => n !== name) : [...current, name]);

  async function start(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFailure(null);
    try {
      const outcome = await api.post<{ started: Run[]; refused: { cluster: string; message: string }[] }>(
        "/benchmark",
        { clusters: chosen, query_set: set || data?.query_sets[0]?.key,
          reason, repetitions: reps, label });
      if (outcome.refused.length) {
        setFailure(
          `Started on ${outcome.started.map((r) => r.cluster).join(", ") || "nothing"}. ` +
          `Refused on ${outcome.refused.map((r) => `${r.cluster} (${r.message})`).join("; ")}`);
      }
      setChosen([]);
      setReason("");
      reload();
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Benchmark</span>
        <span className="spacer" />
        <Link className="btn btn--sm" to="/benchmark/schedules">
          <Icon name="clock" size={12} /> Schedules
        </Link>
        {data?.can_edit ? (
          <Link className="btn btn--primary btn--sm" to="/benchmark/sets">
            New query set
          </Link>
        ) : null}
      </header>

      <main className="content" id="main">
        {error && !data ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{error.message}</div>
          </div>
        ) : null}

        {failure ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{failure}</div>
          </div>
        ) : null}

        {data?.can_start ? (
          <section className="panel">
            <div className="panel__head">
              <div className="panel__title">Run a benchmark</div>
              <div className="panel__sub">
                Pick the clusters and the query set. The same set on two clusters
                is how you compare them.
              </div>
            </div>
            <form className="wi-form" onSubmit={start}>
              {/* Every cluster is selectable. Serving production is a caveat on
                  the numbers, not a refusal — the point of running these on a
                  schedule is to watch clusters people are actually using. */}
              <fieldset className="fieldgroup">
                <legend className="fieldgroup__legend">Clusters</legend>
                {data.clusters.map((cluster) => (
                  <div className={`pick__row${cluster.ready ? "" : " pick__row--blocked"}`}
                       key={cluster.name}>
                    <label className="pick__pick">
                      <input type="checkbox" value={cluster.name}
                             checked={chosen.includes(cluster.name)}
                             disabled={!cluster.ready}
                             onChange={() => toggle(cluster.name)} />
                      <span className="pick__name mono">{cluster.name}</span>
                    </label>
                    {cluster.busy ? (
                      <span className="test-chip test-chip--bad">
                        A benchmark is already running here
                      </span>
                    ) : cluster.exclusive ? (
                      <span className="test-chip test-chip--good">
                        Quiet — out of rotation and idle
                      </span>
                    ) : (
                      <>
                        <span className="test-chip test-chip--concerning">
                          Serving traffic
                        </span>
                        <div className="pick__why">
                          {cluster.guard.advice.map((advice) => (
                            <div className="bench-refusal" key={advice.code}>
                              {advice.text}
                            </div>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </fieldset>

              <label className="field">
                <span>Query set</span>
                <select className="input" value={set} required
                        disabled={!data.query_sets.length}
                        onChange={(e) => setSet(e.target.value)}>
                  {data.query_sets.length ? (
                    data.query_sets.map((qs) => (
                      <option key={qs.key} value={qs.key}>
                        {qs.title} — {qs.queries.length} quer
                        {qs.queries.length === 1 ? "y" : "ies"}
                      </option>
                    ))
                  ) : (
                    <option value="">No query sets yet</option>
                  )}
                </select>
              </label>

              <div className="rg-add__grid">
                <label className="field">
                  Repetitions
                  <input className="input num" type="number" min={1}
                         max={data.max_repetitions} value={reps}
                         onChange={(e) => setReps(Number(e.target.value))} />
                </label>
                <label className="field">
                  <span>
                    Label <span className="dim">(optional)</span>
                  </span>
                  <input className="input" maxLength={200} value={label}
                         placeholder="e.g. after raising the heap to 400G"
                         onChange={(e) => setLabel(e.target.value)} />
                </label>
              </div>

              <label className="field">
                <span>
                  Reason <span className="dim">(required)</span>
                </span>
                <input className="input" required maxLength={500} value={reason}
                       placeholder="Why run this now, on these clusters"
                       onChange={(e) => setReason(e.target.value)} />
              </label>

              <div className="row-actions">
                <button className="btn btn--primary" type="submit"
                        disabled={busy || !chosen.length || !reason.trim()
                                  || !data.query_sets.length}>
                  {busy ? "Starting…" : "Run"}
                </button>
              </div>
            </form>

            <p className="panel__note">
              A run takes the cluster's capacity. On a cluster that is serving,
              the set competes with real queries for the same workers — it can
              cause the slowdown it is measuring, and its numbers include
              whatever else was running. That is recorded with every run rather
              than blocked, so compare quiet against quiet and serving against
              serving.
            </p>
          </section>
        ) : null}

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">Query sets</div>
            <div className="panel__sub">
              A set is the queries that run together and get compared together
            </div>
          </div>
          {data?.query_sets.length ? (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Set</th>
                    <th scope="col">Queries</th>
                    <th scope="col">What it measures</th>
                  </tr>
                </thead>
                <tbody>
                  {data.query_sets.map((qs) => (
                    <tr key={qs.key}>
                      <td>
                        <Link to={`/benchmark/sets/${qs.key}`}>{qs.title}</Link>
                        <div className="dim mono">{qs.key}</div>
                      </td>
                      <td className="num">{qs.queries.length}</td>
                      <td className="wrap dim">{qs.description || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <Icon name="queries" size={20} stroke={1.6} />
              <div className="empty__title">No query sets yet</div>
              <div className="empty__desc">
                Nothing ships by default. A bundled set would have to name a
                catalog, and which catalogs exist is a fact about this
                deployment — a default that fails on first use teaches you the
                feature is broken.
              </div>
            </div>
          )}
        </section>

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">Recent runs</div>
            <div className="panel__sub">
              Open a run to see per-query timings and to compare it with another
            </div>
          </div>
          {data?.runs.length ? (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">#</th>
                    <th scope="col">Started</th>
                    <th scope="col">Cluster</th>
                    <th scope="col">Set</th>
                    <th scope="col">Reps</th>
                    <th scope="col">State</th>
                    <th scope="col">Cluster was</th>
                    <th scope="col">Reason</th>
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
                      <td className="mono">{run.query_set}</td>
                      <td className="num">{run.repetitions}</td>
                      <td>
                        <Status state={run.state} />
                      </td>
                      <td>
                        {/* Not a pass/fail. It is the condition the numbers
                            were taken under, and two runs taken under
                            different conditions are not two measurements of
                            the same thing. */}
                        {run.guard?.ok ? (
                          <span className="test-chip test-chip--good">Quiet</span>
                        ) : (
                          <span className="test-chip test-chip--concerning">
                            Serving traffic
                          </span>
                        )}
                      </td>
                      <td className="wrap">{run.reason}</td>
                      <td>
                        {run.actor}
                        {/* Started by a schedule, not by a person sitting
                            there. The actor is still the schedule's owner —
                            that is what makes the run legal — but "who was at
                            the keyboard" and "whose schedule" are different
                            questions. */}
                        {run.schedule_id ? (
                          <div className="dim">on a schedule</div>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <Icon name="clock" size={20} stroke={1.6} />
              <div className="empty__title">Nothing has been run yet</div>
              <div className="empty__desc">
                Pick a set, give a reason, and run it. Runs of the same set can
                be compared side by side afterwards.
              </div>
            </div>
          )}
        </section>
      </main>
    </>
  );
}
