import { Link, useParams } from "react-router";

import { Icon } from "../components/Icon";
import { LineChart, type Series } from "../components/LineChart";
import { duration } from "../format";
import { useApi } from "../useApi";

interface Execution {
  run_id: number;
  cluster: string;
  label: string | null;
  iteration: number;
  elapsed_ms: number;
  trino_cpu_ms: number | null;
  processed_rows: number | null;
  trino_query_id: string | null;
  state: string;
  error: string | null;
  run_started_at: string;
  statement: string | null;
  differs: boolean;
}

interface Summary {
  cluster: string;
  runs: number;
  count: number;
  avg: number | null;
  median: number | null;
  min: number | null;
  max: number | null;
}

interface History {
  set: { key: string; title: string };
  query: { name: string; title: string; sql: string };
  history: Execution[];
  changed: boolean;
  trend: { series: Series[]; summaries: Summary[]; drawable: boolean };
}

export function QueryHistory() {
  const { key = "", name = "" } = useParams();
  const { data, error } = useApi<History>(
    `/benchmark/sets/${encodeURIComponent(key)}/queries/${encodeURIComponent(name)}/history`);

  if (error) {
    return (
      <>
        <header className="topbar">
          <span className="topbar__title">{name}</span>
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

  const clock = (iso: string) => new Date(iso).toLocaleString();

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">
          {data.query.name} <span className="dim mono">{data.set.key}</span>
        </span>
        <span className="spacer" />
        <Link className="btn btn--sm" to={`/benchmark/sets/${data.set.key}`}>
          ← {data.set.title}
        </Link>
      </header>

      <main className="content" id="main">
        {/* ⛔ Why this screen exists. Once a set can be edited, "same name" no
            longer means "same query", and an unmarked table would line up the
            timings of two different statements and call it a trend. */}
        {data.changed ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" size={15} />
            <div>
              <strong>The SQL for this query changed at some point.</strong> Rows
              marked <span className="test-chip test-chip--bad">Changed</span>{" "}
              below measured the statement as it was then, not as it is now.
              Comparing across that boundary is not a trend.
            </div>
          </div>
        ) : null}

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">Current SQL</div>
            <div className="panel__sub">What runs today when this set is executed</div>
          </div>
          <pre className="sql">{data.query.sql}</pre>
        </section>

        {data.trend.summaries.length ? (
          <section className="panel">
            <div className="panel__head">
              <div className="panel__title">How long it takes, over time</div>
              <div className="panel__sub">
                One point per run — that run's median across its repetitions
              </div>
            </div>
            {data.trend.drawable ? (
              <LineChart series={data.trend.series}
                         label="Median elapsed time per run, one line per cluster" />
            ) : (
              /* One run per cluster is a dot and no line. The table below says
                 the same thing without the chart pretending to show a trend. */
              <p className="panel__note">
                Not enough runs to draw a line yet — a cluster needs at least
                two. The numbers are below.
              </p>
            )}
            <SummaryTable rows={data.trend.summaries} />
          </section>
        ) : null}

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">Executions</div>
            <div className="panel__sub">
              One row per execution, not a median — the outlier is usually the
              answer here
            </div>
          </div>
          {data.history.length ? (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Run</th>
                    <th scope="col">Started</th>
                    <th scope="col">Cluster</th>
                    <th scope="col">Rep</th>
                    <th scope="col">Elapsed</th>
                    <th scope="col">CPU</th>
                    <th scope="col">Rows read</th>
                    <th scope="col">Trino ID</th>
                    <th scope="col">SQL</th>
                  </tr>
                </thead>
                <tbody>
                  {data.history.map((row, index) => (
                    <tr key={`${row.run_id}-${row.iteration}-${index}`}>
                      <td>
                        <Link className="mono" to={`/benchmark/runs/${row.run_id}`}>
                          #{row.run_id}
                        </Link>
                      </td>
                      <td className="mono num dim">{clock(row.run_started_at)}</td>
                      <td className="mono">
                        {row.cluster}
                        {row.label ? <div className="dim">{row.label}</div> : null}
                      </td>
                      <td className="num dim">{row.iteration}</td>
                      <td className="num mono">
                        {row.state === "FAILED" ? (
                          <span className="status status--bad">FAILED</span>
                        ) : (
                          duration(row.elapsed_ms)
                        )}
                      </td>
                      <td className="num mono dim">{duration(row.trino_cpu_ms)}</td>
                      <td className="num mono dim">
                        {row.processed_rows?.toLocaleString() ?? "—"}
                      </td>
                      <td className="mono dim">{row.trino_query_id ?? "—"}</td>
                      <td>
                        {row.statement === null ? (
                          /* An older run that kept no snapshot. Showing today's
                             SQL for it would be a guess. */
                          <span className="dim" title="This run did not record the statement it used">
                            Not recorded
                          </span>
                        ) : row.differs ? (
                          <span className="test-chip test-chip--bad">Changed</span>
                        ) : (
                          <span className="test-chip test-chip--good">Same</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <Icon name="clock" size={20} stroke={1.6} />
              <div className="empty__title">This query has never run</div>
              <div className="empty__desc">
                Run the set once and every repetition shows up here as its own row.
              </div>
            </div>
          )}
        </section>
      </main>
    </>
  );
}

function SummaryTable({ rows }: { rows: Summary[] }) {
  return (
    <>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Cluster</th>
              <th scope="col">Runs</th>
              <th scope="col">Executions</th>
              <th scope="col">Average</th>
              <th scope="col">Median</th>
              <th scope="col">Fastest</th>
              <th scope="col">Slowest</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, slot) => (
              <tr key={row.cluster}>
                <td>
                  <span className={`chart__swatch chart__series--${slot % 4}`}
                        aria-hidden="true" />{" "}
                  <span className="mono">{row.cluster}</span>
                </td>
                <td className="num dim">{row.runs}</td>
                <td className="num dim">{row.count}</td>
                <td className="num">{duration(row.avg)}</td>
                <td className="num">{duration(row.median)}</td>
                <td className="num dim">{duration(row.min)}</td>
                <td className="num dim">{duration(row.max)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="panel__note">
        {/* Both, because the gap between them is itself a reading. */}
        <strong>Average</strong> covers every execution; <strong>median</strong>{" "}
        is what the comparison screen judges on, because one cold first
        execution drags a mean and the median survives it. When the two are far
        apart, the query has a slow tail.
      </p>
    </>
  );
}
