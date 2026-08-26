import { useMemo, useState } from "react";
import { Link } from "react-router";

import { ClusterTabs, useCluster } from "../components/ClusterTabs";
import { Icon } from "../components/Icon";
import { dataSize, duration, relativeTime } from "../format";
import type { Envelope } from "../api";
import { useApi } from "../useApi";

interface Group {
  id: string;
  name: string;
  depth: number;
  running: number | null;
  queued: number | null;
  oldest_queued_ms: number | null;
  hard_concurrency_limit: number | null;
  max_queued: number | null;
  cpu_ms: number | null;
  memory_bytes: number | null;
  bottleneck: string | null;
  bottleneck_text: string;
  children?: Group[];
}

interface Workload {
  enabled: boolean;
  unavailable_reason: string | null;
  advice?: string;
  tree: Group[];
  groups: Group[];
  summary?: {
    groups?: number; running?: number; queued?: number; blocked_groups?: number;
    blocked?: { id: string; reason: string; reason_text: string; queued: number }[];
  };
}

const SORTABLE = [
  { key: "running", label: "Running" },
  { key: "queued", label: "Queued" },
  { key: "oldest_queued_ms", label: "Queue age" },
  { key: "cpu_ms", label: "CPU" },
  { key: "memory_bytes", label: "Memory" },
] as const;

type SortKey = (typeof SORTABLE)[number]["key"];

/** Depth-first, so the table reads as the tree it represents. */
function flatten(tree: Group[]): Group[] {
  const rows: Group[] = [];
  const walk = (node: Group) => {
    rows.push(node);
    (node.children ?? []).forEach(walk);
  };
  (tree ?? []).forEach(walk);
  return rows;
}

export function Workload() {
  const [cluster, selectCluster, names] = useCluster();
  const [sort, setSort] = useState<SortKey | null>(null);
  const [desc, setDesc] = useState(true);

  const { data, error } = useApi<Envelope<Workload>>(
    cluster ? `/clusters/${encodeURIComponent(cluster)}/workload` : null, 15_000);
  const workload = data?.data;

  const rows = useMemo(() => {
    // The tree is the default view. `groups` is the flat list and is the
    // fallback when the collector reported groups but no hierarchy.
    const tree = flatten(workload?.tree ?? []);
    const flat = tree.length ? tree : (workload?.groups ?? []);
    if (!sort) return flat;
    // ⛔ Ranking is a different view, not a reordered tree. Indentation says
    // "this group is inside that one"; once rows are sorted by CPU that is no
    // longer true, and a child can end up under a stranger.
    //
    // Sorted here rather than on the server: it is a question about numbers
    // the browser already holds.
    return [...(workload?.groups?.length ? workload.groups : flat)].sort((a, b) => {
      // Missing is missing, not zero. A group with no reading is unknown.
      const av = a[sort] ?? -1;
      const bv = b[sort] ?? -1;
      return desc ? Number(bv) - Number(av) : Number(av) - Number(bv);
    });
  }, [workload, sort, desc]);

  const ranked = sort !== null;
  const sortLabel = SORTABLE.find((c) => c.key === sort)?.label ?? "";

  const clickSort = (key: SortKey) => {
    // Clicking the active column flips direction; a new column starts
    // descending, because "who is using the most" is the question being asked.
    if (sort === key) setDesc(!desc);
    else {
      setSort(key);
      setDesc(true);
    }
  };

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Workload</span>
        <ClusterTabs selected={cluster} names={names} onSelect={selectCluster} />
        {data ? (
          <div className="freshness" data-stale={data.stale ? "true" : "false"}>
            <span className="freshness__dot" aria-hidden="true" />
            <span>updated {relativeTime(data.collected_at)}</span>
          </div>
        ) : null}
      </header>

      <main className="content" id="main">
        {error && !data ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{error.message}</div>
          </div>
        ) : null}

        {workload && !workload.enabled ? (
          /* Off is a choice, not a fault. Say which, or people go hunting
             through resource-groups.json for a problem that is not there. */
          <div className="banner" role="status">
            <Icon name="overview" size={15} stroke={2} />
            <div>
              <b>Resource group collection is off.</b> Enable it with{" "}
              <code className="mono">workload.enabled: true</code> once the
              coordinator load budget has been re-measured in production —
              collection adds one JMX registry read plus one read per group on
              every poll.
            </div>
          </div>
        ) : null}

        {workload?.unavailable_reason ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" size={15} stroke={2} />
            <div>
              <b>{workload.unavailable_reason}</b>
              {workload.advice ? <div>{workload.advice}</div> : null}
            </div>
          </div>
        ) : null}

        {data?.stale && workload?.enabled ? (
          <div className="banner banner--concerning" role="status">
            <Icon name="clock" size={15} stroke={2} />
            <div>
              <b>Data is stale — last collected {relativeTime(data.collected_at)}.</b>{" "}
              Check that tms-collector is running.
            </div>
          </div>
        ) : null}

        {workload?.enabled && !workload.unavailable_reason && !rows.length ? (
          /* Enabled, reachable, and nothing came back. Without this the
             screen renders as a blank page, which reads as broken rather
             than as "no group has admitted a query yet". */
          <div className="empty">
            <Icon name="queries" size={20} stroke={1.6} />
            <div className="empty__title">No resource groups have run a query</div>
            <div className="empty__desc">
              Trino creates resource groups lazily, so one that has never
              admitted a query has no MBean and cannot be reported. This is not
              the configured list — it is the list that has seen traffic.
            </div>
          </div>
        ) : null}

        {workload?.enabled && rows.length ? (
          <>
            <div className="panel">
              <div className="facts">
                {[
                  ["Groups seen", workload.summary?.groups ?? rows.length],
                  ["Running", workload.summary?.running],
                  ["Queued", workload.summary?.queued],
                  ["Held back", workload.summary?.blocked_groups],
                ].map(([key, value]) => (
                  <div className="fact" key={String(key)}>
                    {/* A missing total shows as an em dash, never as blank.
                        An empty cell under a label reads as zero. */}
                    <div className="fact__value num">{value ?? "—"}</div>
                    <div className="fact__key">{key}</div>
                  </div>
                ))}
              </div>
            </div>

            {workload.summary?.blocked?.length ? (
              <div className="banner banner--concerning" role="status">
                <Icon name="concerning" size={15} stroke={2} />
                <div>
                  <b>
                    {workload.summary.blocked!.length} group(s) are holding
                    queries back.
                  </b>
                  {workload.summary.blocked!.map((item) => (
                    <div key={item.id}>
                      <code className="mono">{item.id}</code> — {item.reason_text}{" "}
                      ({item.queued} queued)
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="panel">
              <div className="panel__head">
                <span className="panel__title">
                  {ranked ? `Ranked by ${sortLabel}` : "Resource groups"}
                </span>
                <span className="panel__sub">
                  {ranked
                    ? "hierarchy is not shown while ranked — a sorted tree would put children under the wrong parents"
                    : "click a column to rank; click a group to see its queries"}
                </span>
                <span className="spacer" />
                {ranked ? (
                  <button className="btn btn--sm btn--ghost" type="button"
                          onClick={() => setSort(null)}>
                    Back to the tree
                  </button>
                ) : null}
              </div>
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Resource group</th>
                      {SORTABLE.slice(0, 3).map((col) => (
                        <SortHeader key={col.key} col={col} sort={sort} desc={desc}
                                    onClick={clickSort} />
                      ))}
                      <th className="num">Concurrency</th>
                      <th className="num">Max queued</th>
                      {SORTABLE.slice(3).map((col) => (
                        <SortHeader key={col.key} col={col} sort={sort} desc={desc}
                                    onClick={clickSort} />
                      ))}
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.id}>
                        <td>
                          {/* Straight to this group's live queries. Trino
                              admits queries to leaf groups, so a parent shows
                              its subtree. */}
                          <Link className="mono group-link"
                                to={`/queries?cluster=${encodeURIComponent(cluster)}&group=${encodeURIComponent(row.id)}`}
                                style={ranked ? undefined
                                              : { paddingLeft: `${row.depth * 18}px` }}>
                            {row.depth && !ranked ? "└ " : ""}
                            {ranked ? row.id : row.name}
                          </Link>
                        </td>
                        <td className="num">{row.running ?? 0}</td>
                        <td className="num">{row.queued ?? 0}</td>
                        <td className="num">
                          {row.oldest_queued_ms ? (
                            <span className={row.oldest_queued_ms > 60000 ? "elapsed--long" : ""}
                                  title="The longest-waiting queued query in this group">
                              {duration(row.oldest_queued_ms)}
                            </span>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                        <td className="num">{row.hard_concurrency_limit ?? "—"}</td>
                        <td className="num">{row.max_queued ?? "—"}</td>
                        <td className="num">{duration(row.cpu_ms)}</td>
                        <td className="num">{dataSize(row.memory_bytes)}</td>
                        <td>
                          {row.bottleneck ? (
                            <span className="status status--concerning">
                              <Icon name="concerning" size={12} stroke={2} />
                              {row.bottleneck_text}
                            </span>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* ⛔ The single most important caveat on this screen. */}
            <p className="muted" style={{ marginTop: "12px" }}>
              <Icon name="overview" size={13} stroke={2} /> Only groups that have
              admitted at least one query appear here — Trino creates resource
              groups lazily. This is not the full configured list.
            </p>
          </>
        ) : null}
      </main>
    </>
  );
}

function SortHeader({ col, sort, desc, onClick }: {
  col: { key: SortKey; label: string };
  sort: SortKey | null;
  desc: boolean;
  onClick: (key: SortKey) => void;
}) {
  const active = sort === col.key;
  return (
    <th className="num"
        aria-sort={active ? (desc ? "descending" : "ascending") : undefined}>
      <button className={`sortable${active ? " sortable--on" : ""}`} type="button"
              onClick={() => onClick(col.key)}>
        {col.label}
        {active ? <span aria-hidden="true">{desc ? " ↓" : " ↑"}</span> : null}
      </button>
    </th>
  );
}
