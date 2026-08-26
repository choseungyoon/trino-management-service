import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { Icon } from "../components/Icon";
import { KillDialog } from "../components/KillDialog";
import { Status } from "../components/Status";
import { dataSize, duration, percent, relativeTime, resourceGroup } from "../format";
import type { Envelope } from "../api";
import { useApi } from "../useApi";

interface Query {
  query_id: string;
  cluster?: string;
  user: string | null;
  source: string | null;
  state: string;
  resource_group_id: string[] | null;
  elapsed_ms: number | null;
  total_cpu_ms: number | null;
  peak_user_memory_bytes: number | null;
  progress_percentage: number | null;
  long_running: boolean;
  links?: { logs?: string; history?: string };
}

interface QueryList {
  summary: { running: number; queued: number; long_running: number; total: number };
  queries: Query[];
  truncated: boolean;
}

const RUNNING = ["RUNNING", "FINISHING"];
const QUEUED = ["QUEUED", "WAITING_FOR_RESOURCES", "PLANNING", "STARTING", "DISPATCHING"];

export function Queries() {
  const [params, setParams] = useSearchParams();
  const cluster = params.get("cluster") ?? "prod-a";
  const filter = params.get("filter") ?? "";
  const [killing, setKilling] = useState<Query | null>(null);

  // Five seconds: this screen answers "what is happening right now", and the
  // collector polls queries on that cadence.
  const { data, error, loading, reload } = useApi<Envelope<QueryList>>(
    `/clusters/${encodeURIComponent(cluster)}/queries`, 5_000);

  const rows = useMemo(() => {
    const all = data?.data.queries ?? [];
    if (filter === "running") return all.filter((q) => RUNNING.includes(q.state));
    if (filter === "queued") return all.filter((q) => QUEUED.includes(q.state));
    if (filter === "long") return all.filter((q) => q.long_running);
    return all;
  }, [data, filter]);

  const summary = data?.data.summary;
  const setFilter = (next: string) => {
    const copy = new URLSearchParams(params);
    if (next) copy.set("filter", next);
    else copy.delete("filter");
    setParams(copy, { replace: true });
  };

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Live Queries</span>
        {data ? (
          <div className="freshness" data-stale={data.stale ? "true" : "false"}>
            <span className="freshness__dot" aria-hidden="true" />
            <span>updated {relativeTime(data.collected_at)}</span>
          </div>
        ) : null}
      </header>

      <main className="content" id="main">
        {data?.stale ? (
          <div className="banner banner--concerning" role="status">
            <Icon name="clock" size={15} stroke={2} />
            <div>
              <b>Data is stale — last collected {relativeTime(data.collected_at)}.</b>{" "}
              What is below is that snapshot, not what is running now.
            </div>
          </div>
        ) : null}

        {error && !data ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{error.message}</div>
          </div>
        ) : null}

        {/* The chips are the summary: the counts are the numbers an operator
            came for, and filtering is what they do next with them. */}
        <div className="filters">
          {[
            { key: "", label: "All", count: summary?.total ?? 0, alert: false },
            { key: "running", label: "Running", count: summary?.running ?? 0, alert: false },
            { key: "queued", label: "Queued", count: summary?.queued ?? 0, alert: false },
            {
              key: "long",
              label: "Long-running",
              count: summary?.long_running ?? 0,
              alert: (summary?.long_running ?? 0) > 0,
            },
          ].map((chip) => (
            <button
              key={chip.key || "all"}
              type="button"
              className={`chip${filter === chip.key ? " chip--on" : ""}${
                chip.alert ? " chip--alert" : ""}`}
              aria-pressed={filter === chip.key}
              onClick={() => setFilter(chip.key)}
            >
              {chip.label} <span className="chip__n">{chip.count}</span>
            </button>
          ))}
        </div>

        {loading ? (
          <div className="empty">
            <div className="empty__title">Loading…</div>
          </div>
        ) : rows.length === 0 ? (
          <div className="empty">
            <Icon name="queries" size={20} stroke={1.6} />
            <div className="empty__title">Nothing is running</div>
            <div className="empty__desc">
              {/* ⛔ An empty list and a permission denial look identical on
                  this endpoint. The H-09 self-check on the Health screen is
                  what tells them apart, so it is named here. */}
              If you expected queries here, check H-09 on the Health screen —
              a permission problem arrives as an empty list, not an error.
            </div>
          </div>
        ) : (
          <section className="panel">
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Query</th>
                    <th scope="col">User</th>
                    <th scope="col">Source</th>
                    <th scope="col">State</th>
                    <th scope="col">Resource group</th>
                    <th scope="col">Elapsed</th>
                    <th scope="col">CPU</th>
                    <th scope="col">Peak mem</th>
                    <th scope="col">Progress</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((q) => (
                    <tr key={q.query_id} className={q.long_running ? "is-long" : ""}>
                      <td>
                        <Link className="mono dim"
                              to={`/queries/${encodeURIComponent(q.query_id)}?cluster=${cluster}`}>
                          {q.query_id}
                        </Link>
                      </td>
                      <td className="strong">{q.user || "—"}</td>
                      <td className="dim">{q.source || "—"}</td>
                      <td>
                        <Status state={q.state} />
                      </td>
                      <td>
                        <span className="tag">{resourceGroup(q.resource_group_id)}</span>
                      </td>
                      <td className={`mono num${q.long_running ? " elapsed--long" : ""}`}>
                        {duration(q.elapsed_ms)}
                      </td>
                      <td className="mono num">{duration(q.total_cpu_ms)}</td>
                      <td className="mono num">{dataSize(q.peak_user_memory_bytes)}</td>
                      <td>
                        {q.progress_percentage === null ? (
                          <span className="dim">—</span>
                        ) : (
                          <div className="progress" title={percent(q.progress_percentage, 0)}>
                            <i style={{ width: `${Math.round(q.progress_percentage)}%` }} />
                          </div>
                        )}
                      </td>
                      <td>
                        <div className="row-actions">
                          {q.links?.logs ? (
                            <a className="row-btn" href={q.links.logs} target="_blank"
                               rel="noopener noreferrer" title="Logs around this query">
                              <Icon name="audit" size={13} />
                            </a>
                          ) : null}
                          {q.links?.history ? (
                            <a className="row-btn" href={q.links.history} target="_blank"
                               rel="noopener noreferrer" title="View in Query History">
                              <Icon name="history" size={13} />
                            </a>
                          ) : null}
                          <button className="row-btn row-btn--kill" type="button"
                                  title="Kill query" onClick={() => setKilling(q)}>
                            <Icon name="bad" size={13} stroke={2} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </main>

      {killing ? (
        <KillDialog query={killing} cluster={cluster}
                    onClose={() => setKilling(null)}
                    onKilled={() => { setKilling(null); reload(); }} />
      ) : null}
    </>
  );
}
