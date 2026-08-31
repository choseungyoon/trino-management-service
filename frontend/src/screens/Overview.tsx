import { Link } from "react-router";

import { Icon } from "../components/Icon";
import { Status, statusClass } from "../components/Status";
import { relativeTime } from "../format";
import { useApi } from "../useApi";

interface ClusterCard {
  name: string;
  coordinator_url: string;
  /** Null unless configured — see `Coordinator` for why it is not derived. */
  trino_ui_url: string | null;
  expected_workers: number;
  active_workers: number | null;
  planned_out: number;
  running: number;
  queued: number;
  failure_rate: number | null;
  rollup_state: string;
  stale: boolean;
  collected_at: string | null;
  tests: { id: string; name: string; state: string }[];
}

/**
 * Where the cluster is, and a way into its own web UI.
 *
 * ⛔ Linked only when `trino_ui_url` is configured. The address could be
 * turned into a link on its own — Trino serves its UI from the coordinator —
 * but that URL is the one *TMS* reaches, and this link is clicked from a
 * browser that may be on another network or behind another proxy. A link that
 * works from the server and 404s for the operator is worse than no link, so
 * the address stays text and the tooltip names the setting that would make it
 * one.
 */
function Coordinator({ cluster }: { cluster: ClusterCard }) {
  const address = hostOf(cluster.coordinator_url);
  if (!address) return null;
  if (!cluster.trino_ui_url) {
    return (
      <span className="cluster__host mono"
            title={`${cluster.coordinator_url}\n\nSet trino_ui_url for this `
                   + `cluster in config.yaml to link straight to its Trino UI.`}>
        {address}
      </span>
    );
  }
  return (
    <a className="cluster__host mono" href={cluster.trino_ui_url}
       target="_blank" rel="noopener noreferrer"
       title={`Open the Trino UI for ${cluster.name}`}>
      {address}
      <Icon name="external" size={11} />
    </a>
  );
}

/** `https://host:8443/` -> `host:8443`. The scheme is noise on a card. */
function hostOf(url: string | null | undefined): string {
  if (!url) return "";
  try {
    const parsed = new URL(url);
    return parsed.host;
  } catch {
    return url;
  }
}

export function Overview() {
  // Fifteen seconds, matching the collector's health cadence. Asking faster
  // returns the same snapshot and only costs the browser a repaint.
  const { data, error, loading } = useApi<{ clusters: ClusterCard[] }>(
    "/overview", 15_000);

  const oldest = data?.clusters.reduce<string | null>(
    (worst, c) => (c.collected_at && (!worst || c.collected_at < worst)
      ? c.collected_at : worst), null) ?? null;
  const anyStale = data?.clusters.some((c) => c.stale) ?? false;

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Overview</span>
        {data ? (
          <div className="freshness" data-stale={anyStale ? "true" : "false"}>
            <span className="freshness__dot" aria-hidden="true" />
            <span>updated {relativeTime(oldest)}</span>
          </div>
        ) : null}
      </header>

      <main className="content" id="main">
        {/* ⛔ Said once, loudly. Values below are as of that time and are
            never presented as current. */}
        {anyStale ? (
          <div className="banner banner--concerning" role="status">
            <Icon name="clock" size={15} stroke={2} />
            <div>
              <b>Data is stale — last collected {relativeTime(oldest)}.</b> The
              collector may be down or unable to reach a coordinator. Values
              below are shown as of that time, never as current.
            </div>
          </div>
        ) : null}

        {error && !data ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{error.message}</div>
          </div>
        ) : null}

        {loading ? (
          <div className="empty">
            <div className="empty__title">Loading…</div>
          </div>
        ) : null}

        <div className="cluster-grid">
          {data?.clusters.map((cluster) => (
            <section className="panel cluster" key={cluster.name}>
              <div className="cluster__top">
                <div className="cluster__id">
                  <h2 className="cluster__name">{cluster.name}</h2>
                  <Coordinator cluster={cluster} />
                </div>
                <Status state={cluster.rollup_state} large />
              </div>

              <div className="facts">
                <div className="fact">
                  <div className="fact__value num">
                    {cluster.active_workers ?? "—"}
                    <small>/{cluster.expected_workers}</small>
                    {cluster.planned_out ? (
                      <span className="fact__planned">
                        +{cluster.planned_out} draining
                      </span>
                    ) : null}
                  </div>
                  <div className="fact__key">Active workers</div>
                </div>
                <div className="fact">
                  <div className="fact__value num">{cluster.running}</div>
                  <div className="fact__key">Running</div>
                </div>
                <div className="fact">
                  <div className="fact__value num">{cluster.queued}</div>
                  <div className="fact__key">Queued</div>
                </div>
                <div className="fact">
                  <div className="fact__value num">
                    {cluster.failure_rate === null
                      ? "—"
                      : `${cluster.failure_rate.toFixed(1)}%`}
                  </div>
                  <div className="fact__key">Failure rate · 5m</div>
                </div>
              </div>

              {cluster.tests.length ? (
                <div className="test-strip">
                  <span className="test-strip__label">TESTS</span>
                  {cluster.tests.map((test) => (
                    <Link
                      key={test.id}
                      className={`test-chip test-chip--${statusClass(test.state)}`}
                      to={`/cluster-health?cluster=${cluster.name}#${test.id}`}
                      title={`${test.name} — ${test.state}`}
                    >
                      <i aria-hidden="true" />
                      {test.id}
                    </Link>
                  ))}
                </div>
              ) : null}

              <div className="test-strip">
                <Link className="btn btn--sm" to={`/queries?cluster=${cluster.name}`}>
                  <Icon name="queries" size={12} />
                  Live queries
                </Link>
                <Link className="btn btn--sm" to={`/cluster-health?cluster=${cluster.name}`}>
                  <Icon name="health" size={12} />
                  Health detail
                </Link>
                {cluster.stale ? (
                  <span className="muted" style={{ fontSize: "11.5px" }}>
                    snapshot {relativeTime(cluster.collected_at)}
                  </span>
                ) : null}
              </div>
            </section>
          ))}
        </div>
      </main>
    </>
  );
}
