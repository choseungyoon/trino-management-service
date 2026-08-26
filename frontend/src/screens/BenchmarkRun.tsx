import { useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError, api } from "../api";
import { Icon } from "../components/Icon";
import { Status } from "../components/Status";
import { duration, integer } from "../format";
import { useApi } from "../useApi";

interface QueryRow {
  name: string;
  runs: number;
  failures: number;
  median_ms: number | null;
  fastest_ms: number | null;
  median_cpu_ms: number | null;
  rows_processed: number | null;
  rows_varied: boolean;
  rows_range: [number, number] | null;
  all_failed: boolean;
  error: string | null;
}

interface Run {
  id: number;
  cluster: string;
  query_set: string;
  label: string | null;
  state: string;
  repetitions: number;
  reason: string;
  actor: string;
  error: string | null;
  started_at: string;
  results: { state: string }[];
  by_query: QueryRow[];
  guard: { ok: boolean; advice: { code: string; text: string }[] };
}

interface Side {
  id: number;
  cluster: string;
  label: string | null;
  started_at: string;
}

interface Comparison {
  baseline: Side;
  candidate: Side;
  rows: {
    name: string;
    baseline: { median_ms: number | null } | null;
    candidate: { median_ms: number | null } | null;
    delta_ms: number | null;
    delta_percent: number | null;
    verdict: string;
    statement_changed: boolean;
  }[];
  summary: { faster: number; slower: number; same: number; unmatched: number };
  warnings: string[];
}

/* `slower` is bad and `faster` is good only because the candidate is on the
   right. The sign alone does not say that, so nothing here derives it from
   the number. */
const VERDICT: Record<string, { klass: string; word: string; dim?: boolean }> = {
  slower: { klass: "bad", word: "Slower" },
  faster: { klass: "good", word: "Faster" },
  same: { klass: "unknown", word: "Unchanged", dim: true },
  only_baseline: { klass: "concerning", word: "Not in this run", dim: true },
  only_candidate: { klass: "concerning", word: "Not in the baseline", dim: true },
};

export function BenchmarkRun() {
  const { id = "" } = useParams();
  // A finished run never changes; a running one does. Polling both is simpler
  // than deciding, and 5s is what the server-rendered page did.
  const { data: run, error, reload } = useApi<Run>(`/benchmarks/${id}`, 5_000);
  const { data: others } = useApi<{ runs: Side[] }>(`/benchmarks/${id}/comparable`);
  const [against, setAgainst] = useState("");
  const { data: comparison, error: compareError } = useApi<Comparison>(
    against ? `/benchmarks/${against}/compare/${id}` : null);
  const [failure, setFailure] = useState<string | null>(null);

  async function abort() {
    try {
      await api.post(`/benchmarks/${id}/abort`, {});
      reload();
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : String(caught));
    }
  }

  if (error) {
    return (
      <>
        <header className="topbar">
          <span className="topbar__title">Benchmark #{id}</span>
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
  if (!run) return null;

  const executed = run.results?.length ?? 0;
  const failures = (run.results ?? []).filter((r) => r.state === "FAILED").length;

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">
          Benchmark <span className="mono">#{run.id}</span>{" "}
          <span className="dim">{run.cluster}</span>
        </span>
        <span className="spacer" />
        <Link className="btn btn--sm" to="/benchmark">
          <Icon name="clock" size={12} /> All benchmarks
        </Link>
      </header>

      <main className="content" id="main">
        {failure ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{failure}</div>
          </div>
        ) : null}

        {/* On every visit, because it is the condition the numbers were taken
            under and it does not stop being true once you scroll past it. */}
        {run.guard && !run.guard.ok ? (
          <div className="banner banner--concerning" role="status">
            <Icon name="concerning" size={15} />
            <div>
              <strong>The cluster was serving traffic while this ran.</strong>
              {run.guard.advice.map((advice) => (
                <div className="bench-refusal" key={advice.code}>{advice.text}</div>
              ))}
              <div className="dim" style={{ marginTop: 6 }}>
                Compare it with another run taken under the same conditions. A
                quiet cluster and a busy one are not two measurements of the
                same thing.
              </div>
            </div>
          </div>
        ) : null}

        <section className="panel">
          <div className="facts">
            <div className="fact">
              <div className="fact__value"><Status state={run.state} /></div>
              <div className="fact__key">State</div>
            </div>
            <div className="fact">
              <div className="fact__value mono">{run.query_set}</div>
              <div className="fact__key">
                Query set{run.label ? ` · ${run.label}` : ""}
              </div>
            </div>
            <div className="fact">
              <div className="fact__value num">{integer(run.repetitions)}</div>
              <div className="fact__key">Repetitions</div>
            </div>
            <div className="fact">
              <div className="fact__value num">
                {integer(executed)}
                {failures ? <span className="dim"> ({integer(failures)} failed)</span> : null}
              </div>
              <div className="fact__key">Executions</div>
            </div>
            <div className="fact">
              <div className="fact__value">{run.actor}</div>
              <div className="fact__key">{new Date(run.started_at).toLocaleString()}</div>
            </div>
          </div>
          <p className="panel__note wrap">
            <span className="strong">Reason</span> — {run.reason}
            {run.error ? (
              <>
                <br />
                <span className="strong">Outcome</span> — {run.error}
              </>
            ) : null}
          </p>
        </section>

        {run.state === "RUNNING" ? (
          <section className="panel">
            <div className="panel__head">
              <div className="panel__title">Running</div>
              <div className="panel__sub">This page refreshes every 5 seconds</div>
            </div>
            <div className="wi-form">
              <p className="dim" style={{ margin: 0 }}>
                Stopping takes effect{" "}
                <strong>after the query now in flight finishes</strong>. This
                console does not cancel its own statements — a cancelled query
                leaves something on the coordinator whose outcome nobody
                recorded.
              </p>
              <div className="row-actions">
                <button className="btn btn--danger" type="button" onClick={abort}>
                  Stop
                </button>
              </div>
            </div>
          </section>
        ) : null}

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">Results by query</div>
            <div className="panel__sub">
              Repetitions folded into a <strong>median</strong> — the first
              execution is never like the rest
            </div>
          </div>
          <p className="panel__note">
            <strong>Rows read</strong> counts rows the engine had to touch, not
            rows the query returned. Under a <span className="mono">LIMIT</span>{" "}
            it stops as soon as the limit is met, so the same query can read a
            different number each time depending on how the splits were
            scheduled. A range shown there means the repetitions did different
            amounts of work, and their timings are not strictly comparable.
          </p>
          {run.by_query?.length ? (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Query</th>
                    <th scope="col">Runs</th>
                    <th scope="col">Median</th>
                    <th scope="col">Fastest</th>
                    <th scope="col">Median CPU</th>
                    <th scope="col">Rows read</th>
                  </tr>
                </thead>
                <tbody>
                  {run.by_query.map((query) => (
                    <tr key={query.name}>
                      <td className="mono">
                        <Link to={`/benchmark/sets/${run.query_set}/queries/${
                          encodeURIComponent(query.name)}/history`}>
                          {query.name}
                        </Link>
                      </td>
                      <td className="num">
                        {integer(query.runs)}
                        {query.failures ? (
                          <> <span className="status status--bad">
                            {integer(query.failures)} failed
                          </span></>
                        ) : null}
                      </td>
                      {query.all_failed ? (
                        <td colSpan={4} className="wrap">
                          {query.error || "Every execution failed."}
                        </td>
                      ) : (
                        <>
                          <td className="num">{duration(query.median_ms)}</td>
                          <td className="num dim">{duration(query.fastest_ms)}</td>
                          <td className="num dim">{duration(query.median_cpu_ms)}</td>
                          <td className="num dim">
                            {integer(query.rows_processed)}
                            {query.rows_varied && query.rows_range ? (
                              <div>
                                <span className="test-chip test-chip--concerning"
                                      title="Rows read varies between repetitions, so the timings are not of equal work">
                                  {integer(query.rows_range[0])}–{integer(query.rows_range[1])}
                                </span>
                              </div>
                            ) : null}
                          </td>
                        </>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <Icon name="clock" size={20} stroke={1.6} />
              <div className="empty__title">No results yet</div>
              <div className="empty__desc">
                The first query to finish shows up here.
              </div>
            </div>
          )}
        </section>

        {others?.runs.length ? (
          <section className="panel">
            <div className="panel__head">
              <div className="panel__title">Compare</div>
              <div className="panel__sub">
                Only runs of the same set (
                <span className="mono">{run.query_set}</span>) can be chosen
              </div>
            </div>
            <div className="wi-form">
              <label className="field">
                Compare against
                <select className="input" value={against}
                        onChange={(e) => setAgainst(e.target.value)}>
                  <option value="">Choose a run…</option>
                  {others.runs.map((other) => (
                    <option key={other.id} value={other.id}>
                      #{other.id} · {other.cluster} ·{" "}
                      {new Date(other.started_at).toLocaleString()}
                      {other.label ? ` · ${other.label}` : ""}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {compareError ? (
              <div className="banner banner--bad" role="alert"
                   style={{ margin: "0 16px 10px" }}>
                <Icon name="bad" size={15} />
                <div>{compareError.message}</div>
              </div>
            ) : null}

            {comparison ? (
              <>
                {comparison.warnings.map((warning) => (
                  <div className="banner banner--concerning" role="alert"
                       key={warning} style={{ margin: "0 16px 10px" }}>
                    <Icon name="concerning" size={15} />
                    <div>{warning}</div>
                  </div>
                ))}
                <p className="panel__note">
                  Baseline <span className="mono">#{comparison.baseline.id}</span>{" "}
                  ({comparison.baseline.cluster}) → this run{" "}
                  <span className="mono">#{comparison.candidate.id}</span>{" "}
                  ({comparison.candidate.cluster}) ·{" "}
                  {comparison.summary.slower} slower · {comparison.summary.faster}{" "}
                  faster · {comparison.summary.same} unchanged
                  {comparison.summary.unmatched
                    ? ` · ${comparison.summary.unmatched} on one side only`
                    : ""}
                </p>
                <div className="table-scroll">
                  <table className="table">
                    <thead>
                      <tr>
                        <th scope="col">Query</th>
                        <th scope="col">#{comparison.baseline.id}</th>
                        <th scope="col">#{comparison.candidate.id}</th>
                        <th scope="col">Difference</th>
                        <th scope="col">Verdict</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparison.rows.map((row) => {
                        const verdict = VERDICT[row.verdict] ?? VERDICT.same;
                        return (
                          <tr key={row.name}>
                            <td className="mono">
                              {row.name}
                              {row.statement_changed ? (
                                <div>
                                  <span className="test-chip test-chip--bad">
                                    SQL changed between these runs
                                  </span>
                                </div>
                              ) : null}
                            </td>
                            <td className="num">
                              {duration(row.baseline?.median_ms ?? null)}
                            </td>
                            <td className="num">
                              {duration(row.candidate?.median_ms ?? null)}
                            </td>
                            {/* The sign lives here, not in `duration` — a
                                formatter for elapsed time renders anything
                                negative as an em dash, which turned "2%
                                faster" into "—". */}
                            <td className={`num delta delta--${verdict.klass}`}>
                              {row.delta_percent === null || row.delta_ms === null ? "—" : (
                                <>
                                  {row.delta_ms > 0 ? "+" : row.delta_ms < 0 ? "-" : ""}
                                  {duration(Math.abs(row.delta_ms))} (
                                  {row.delta_percent.toFixed(1)}%)
                                </>
                              )}
                            </td>
                            {/* The word, not just the colour. "Is red good
                                here" is a question nobody should have to ask
                                of a comparison table. */}
                            <td>
                              {verdict.dim ? (
                                <span className="dim">{verdict.word}</span>
                              ) : (
                                verdict.word
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="panel__note">
                  Anything under ±5% counts as unchanged. The verdict compares
                  per-query medians, not totals — a total is dominated by
                  whichever query is longest.
                </p>
              </>
            ) : null}
          </section>
        ) : null}
      </main>
    </>
  );
}
