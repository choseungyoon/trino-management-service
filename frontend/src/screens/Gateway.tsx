import { Link } from "react-router";

import { Icon } from "../components/Icon";
import { relativeTime } from "../format";
import type { Envelope } from "../api";
import { useApi } from "../useApi";

interface Backend {
  name: string;
  routing_group: string | null;
  proxy_to: string;
  cluster: string | null;
  matched_by?: string;
  active: boolean;
}

interface GatewayView {
  enabled: boolean;
  live: boolean;
  unavailable_reason: string | null;
  advice?: string;
  backends: Backend[];
  groups: { name: string; active: number; total: number; backends: { name: string }[] }[];
  routing_rules: { priority: number; name: string; description?: string;
                   condition: string; actions: string[] }[];
  unmonitored_backends: string[];
  unrouted_clusters: string[];
  inactive_backends?: string[];
}

function NameList({ names }: { names: string[] }) {
  return (
    <>
      {names.map((name, i) => (
        <span key={name}>
          <code className="mono">{name}</code>
          {i < names.length - 1 ? ", " : ""}
        </span>
      ))}
    </>
  );
}

export function Gateway() {
  const { data, error } = useApi<Envelope<GatewayView>>("/gateway", 30_000);
  const gw = data?.data;

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Gateway</span>
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

        {gw && !gw.enabled ? (
          <div className="banner" role="status">
            <Icon name="overview" size={15} stroke={2} />
            <div>
              <b>Gateway integration is off.</b> Set{" "}
              <code className="mono">gateway.enabled: true</code> and{" "}
              <code className="mono">gateway.base_url</code> to show routing here.
            </div>
          </div>
        ) : null}

        {gw?.enabled ? (
          <>
            {gw.unavailable_reason ? (
              <div className="banner banner--bad" role="alert">
                <Icon name="bad" size={15} stroke={2} />
                <div>
                  <b>{gw.unavailable_reason}</b>
                  {gw.advice ? <div>{gw.advice}</div> : null}
                </div>
              </div>
            ) : null}

            {data?.stale ? (
              <div className="banner banner--concerning" role="status">
                <Icon name="clock" size={15} stroke={2} />
                <div>
                  <b>Data is stale — last collected {relativeTime(data.collected_at)}.</b>
                </div>
              </div>
            ) : null}

            {/* ⛔ The two disagreements below are why this screen exists. Each
                side — the Gateway's own UI and the TMS cluster list — looks
                correct alone; only the join shows they disagree. */}
            {gw.unmonitored_backends?.length ? (
              <div className="banner banner--bad" role="alert">
                <Icon name="bad" size={15} stroke={2} />
                <div>
                  <b>
                    {gw.unmonitored_backends.length} backend(s) are not monitored
                    by TMS.
                  </b>
                  <div>
                    Queries are being routed to{" "}
                    <NameList names={gw.unmonitored_backends} /> and nobody is
                    watching them. Add them to{" "}
                    <code className="mono">config.yaml</code> or remove them from
                    the Gateway.
                  </div>
                </div>
              </div>
            ) : null}

            {gw.unrouted_clusters?.length ? (
              <div className="banner banner--concerning" role="status">
                <Icon name="concerning" size={15} stroke={2} />
                <div>
                  <b>
                    {gw.unrouted_clusters.length} monitored cluster(s) have no
                    Gateway backend.
                  </b>
                  <div>
                    <NameList names={gw.unrouted_clusters} /> receive no routed
                    traffic. Either they are not registered, or{" "}
                    <code className="mono">config.yaml</code> lists a cluster
                    that no longer exists.
                  </div>
                </div>
              </div>
            ) : null}

            {gw.inactive_backends?.length ? (
              <div className="banner banner--concerning" role="status">
                <Icon name="concerning" size={15} stroke={2} />
                <div>
                  <b>{gw.inactive_backends.length} backend(s) are deactivated.</b>
                  <div>
                    <NameList names={gw.inactive_backends} /> accept no new
                    queries. Ignore this if it is a deliberate drain.
                  </div>
                </div>
              </div>
            ) : null}

            {gw.backends?.length ? (
              <div className="panel">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Backend</th>
                      <th>Routing group</th>
                      <th>Routes to</th>
                      <th>TMS cluster</th>
                      <th>State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {gw.backends.map((b) => (
                      <tr key={b.name}>
                        <td className="mono">{b.name}</td>
                        <td>
                          {b.routing_group ? (
                            <span className="mono">{b.routing_group}</span>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                        <td className="mono dim">{b.proxy_to}</td>
                        <td>
                          {b.cluster ? (
                            <>
                              <Link to={`/health?cluster=${encodeURIComponent(b.cluster)}`}>
                                {b.cluster}
                              </Link>
                              {b.matched_by === "name" ? (
                                /* Matched on name, which drifts. Worth saying. */
                                <span className="muted" title="matched by name, not by URL">
                                  {" "}(by name)
                                </span>
                              ) : null}
                            </>
                          ) : (
                            <span className="status status--bad">
                              <Icon name="bad" size={12} stroke={2} /> not monitored
                            </span>
                          )}
                        </td>
                        <td>
                          {b.active ? (
                            <span className="status status--good">
                              <Icon name="good" size={12} stroke={2} /> active
                            </span>
                          ) : (
                            <span className="status status--concerning">
                              <Icon name="concerning" size={12} stroke={2} /> inactive
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}

            {gw.groups?.length ? (
              <>
                <div className="section-label">Routing groups</div>
                <div className="panel">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Group</th>
                        <th className="num">Active</th>
                        <th>Backends</th>
                      </tr>
                    </thead>
                    <tbody>
                      {gw.groups.map((g) => (
                        <tr key={g.name}>
                          <td className="mono">{g.name}</td>
                          <td className="num">
                            {g.active} / {g.total}
                          </td>
                          <td className="dim">
                            <NameList names={g.backends.map((b) => b.name)} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}

            {/* Routing rules come from an endpoint that is not in the Gateway
                documentation. Absent means "not configured, or gone after an
                upgrade" — both normal, so the section just does not appear. */}
            {gw.routing_rules?.length ? (
              <>
                <div className="section-label">Routing rules — read-only</div>
                <div className="panel">
                  <table className="table">
                    <thead>
                      <tr>
                        <th className="num">Priority</th>
                        <th>Name</th>
                        <th>Condition</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {gw.routing_rules.map((r) => (
                        <tr key={`${r.priority}-${r.name}`}>
                          <td className="num">{r.priority}</td>
                          <td>
                            {r.name}
                            {r.description ? (
                              <div className="muted">{r.description}</div>
                            ) : null}
                          </td>
                          <td className="mono dim">{r.condition}</td>
                          <td className="mono dim">
                            {(r.actions ?? []).map((a) => (
                              <div key={a}>{a}</div>
                            ))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}

            {gw.backends?.length ? (
              <div className="panel">
                <div className="panel__head">
                  <span className="panel__title">What this screen does not know</span>
                </div>
                <div className="seq__act">
                  <p className="seq__act-why">
                    TMS cannot tell whether this list came from the Gateway's
                    database or from its <code className="mono">databaseCache</code>.
                    The Gateway reports nothing that distinguishes the two, and a
                    list that has not changed looks the same either way.
                  </p>
                  <p className="seq__act-why">
                    <b>What the cache does and does not cover.</b> Only the backend
                    cluster list is cached. Query history and the queryId→backend
                    lookup are not, so a database outage stops those immediately
                    while routing carries on.
                  </p>
                  <p className="seq__act-why">
                    <b>And it expires.</b> After{" "}
                    <code className="mono">expireAfterWrite</code> the Gateway has
                    no stale value to fall back on and routing fails outright — it
                    does not degrade. Treat the cache as time to fix the database,
                    not as tolerance for losing it.
                  </p>
                </div>
              </div>
            ) : null}
          </>
        ) : null}
      </main>
    </>
  );
}
