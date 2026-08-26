import { ClusterTabs, useCluster } from "../components/ClusterTabs";
import { Icon } from "../components/Icon";
import { Observed, type Segment } from "../components/Observed";
import { Status, statusClass } from "../components/Status";
import { relativeTime } from "../format";
import type { Envelope } from "../api";
import { useApi } from "../useApi";

interface Test {
  id: string;
  name: string;
  state: string;
  observed: Segment[];
  advice: string;
  links?: { logs?: string };
}

interface Health {
  rollup_state: string;
  rollup_enabled: boolean;
  tests: Test[];
}

interface Event {
  test_id: string;
  from_state: string;
  to_state: string;
  observed_value: string | null;
  occurred_at: string;
}

export function Health() {
  const [cluster, selectCluster, names] = useCluster();
  const path = cluster ? `/clusters/${encodeURIComponent(cluster)}/health` : null;
  const { data, error, loading } = useApi<Envelope<Health>>(path, 15_000);
  const events = useApi<{ events: Event[] }>(path ? `${path}/events` : null);

  const health = data?.data;
  const counts = (health?.tests ?? []).reduce(
    (acc, test) => {
      const key = statusClass(test.state);
      acc[key] = (acc[key] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>);

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Cluster Health</span>
        <ClusterTabs selected={cluster} names={names} onSelect={selectCluster} />
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
              Every test below is reported as UNKNOWN until fresh data arrives;
              an old reading is never presented as current.
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

        {health ? (
          <section className={`panel rollup--${statusClass(health.rollup_state)}`}>
            <div className="rollup">
              <div className="rollup__verdict">
                <Icon name={statusClass(health.rollup_state)} size={22} stroke={2.2} />
                {health.rollup_state}
              </div>
              <span className="rollup__count">
                {counts.concerning ?? 0} concerning · {counts.bad ?? 0} bad ·{" "}
                {counts.unknown ?? 0} unknown · {counts.good ?? 0} good
              </span>
              <div className="rollup__right">
                <span>Roll-up</span>
                <span className="switch" role="switch"
                      aria-checked={health.rollup_enabled}
                      aria-disabled="true" />
              </div>
            </div>

            {health.tests.map((test) => (
              <div className="test-row" id={test.id} key={test.id}>
                <div className="test-row__name">
                  <Status state={test.state} />
                  <span>{test.name}</span>
                  <span className="test-row__id">{test.id}</span>
                </div>
                <div className="test-row__observed">
                  {/* The words come from the server; this only restores the
                      emphasis, so the numbers are what the eye lands on. */}
                  <Observed segments={test.observed} />
                  {test.advice ? (
                    /* ⛔ Every non-GOOD state ships its remedy. Advice is
                       first-class here, not tooltip text. */
                    <div className={`advice advice--${statusClass(test.state)}`}>
                      <Icon name={statusClass(test.state)} size={14} stroke={2} />
                      <div>
                        {test.advice}{" "}
                        {test.links?.logs ? (
                          <a href={test.links.logs} target="_blank" rel="noopener noreferrer">
                            Logs around this time →
                          </a>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </section>
        ) : null}

        <section className="panel">
          <div className="panel__head">
            <span className="panel__title">State transitions</span>
            <span className="panel__sub">
              confirmed after several stable polls — single spikes never land here
            </span>
          </div>
          {events.data?.events?.length ? (
            events.data.events.map((event, index) => (
              <div className="event" key={`${event.test_id}-${index}`}>
                <span className="event__time">
                  {new Date(event.occurred_at).toLocaleTimeString()}
                </span>
                <div className="event__transition">
                  <span className="mono">{event.test_id}</span>
                  <Status state={event.from_state} />
                  <span className="event__arrow" aria-label="changed to">→</span>
                  <Status state={event.to_state} />
                  {event.observed_value ? (
                    <span className="event__why">{event.observed_value}</span>
                  ) : null}
                </div>
              </div>
            ))
          ) : (
            <div className="empty">
              <Icon name="good" size={20} stroke={1.6} />
              <div className="empty__title">No state changes recorded</div>
              <div className="empty__desc">
                Health has held steady since the collector started tracking this
                cluster.
              </div>
            </div>
          )}
        </section>
      </main>
    </>
  );
}
