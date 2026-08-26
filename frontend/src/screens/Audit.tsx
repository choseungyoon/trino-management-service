import { useSearchParams } from "react-router";

import { Icon } from "../components/Icon";
import { useApi } from "../useApi";

interface Record_ {
  occurred_at: string;
  actor: string;
  actor_ip: string | null;
  action_type: string;
  target_id: string;
  target_cluster: string | null;
  reason: string;
  outcome: string;
  error_message: string | null;
}

export function Audit() {
  const [params, setParams] = useSearchParams();
  const actor = params.get("actor") ?? "";
  const action = params.get("action_type") ?? "";

  const query = new URLSearchParams();
  if (actor) query.set("actor", actor);
  if (action) query.set("action_type", action);
  const suffix = query.toString();

  const { data, error, loading } = useApi<{ records: Record_[]; count: number }>(
    `/audit${suffix ? `?${suffix}` : ""}`);

  const set = (key: string, value: string) => {
    const copy = new URLSearchParams(params);
    if (value) copy.set(key, value);
    else copy.delete(key);
    setParams(copy, { replace: true });
  };

  const records = data?.records ?? [];
  const actions = Array.from(new Set(records.map((r) => r.action_type))).sort();

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Audit Log</span>
      </header>

      <main className="content" id="main">
        {error ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{error.message}</div>
          </div>
        ) : null}

        <div className="filters">
          <button type="button" className={`chip${action ? "" : " chip--on"}`}
                  aria-pressed={!action} onClick={() => set("action_type", "")}>
            All <span className="chip__n">{data?.count ?? 0}</span>
          </button>
          {actions.map((name) => (
            <button key={name} type="button"
                    className={`chip${action === name ? " chip--on" : ""}`}
                    aria-pressed={action === name}
                    onClick={() => set("action_type", name)}>
              {name}{" "}
              <span className="chip__n">
                {records.filter((r) => r.action_type === name).length}
              </span>
            </button>
          ))}
          <span className="filters__sep" aria-hidden="true" />
          <div className="search" role="search">
            <Icon name="audit" size={12} stroke={2} />
            <label className="sr-only" htmlFor="actor">Filter by actor</label>
            <input id="actor" value={actor} placeholder="Filter by actor…"
                   onChange={(e) => set("actor", e.target.value)} />
          </div>
        </div>

        <section className="panel" style={{ overflow: "hidden" }}>
          {loading ? (
            <div className="empty">
              <div className="empty__title">Loading…</div>
            </div>
          ) : records.length ? (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Time</th>
                    <th scope="col">Actor</th>
                    <th scope="col">Action</th>
                    <th scope="col">Target</th>
                    <th scope="col">Reason</th>
                    <th scope="col">Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {records.map((r, index) => (
                    <tr key={`${r.occurred_at}-${index}`}>
                      <td className="mono num dim">
                        {new Date(r.occurred_at).toLocaleString()}
                      </td>
                      <td>
                        <span className="strong">{r.actor}</span>{" "}
                        {r.actor_ip ? (
                          <span className="mono dim">{r.actor_ip}</span>
                        ) : null}
                      </td>
                      <td>
                        <span className="action-badge">{r.action_type}</span>
                      </td>
                      <td className="mono dim">
                        {r.target_id}
                        {r.target_cluster ? ` · ${r.target_cluster}` : ""}
                      </td>
                      {/* The reason is the payload, not metadata — it gets the
                          wide column. */}
                      <td className="wrap">{r.reason}</td>
                      <td>
                        {r.outcome === "SUCCESS" ? (
                          <span className="outcome outcome--success">SUCCESS</span>
                        ) : (
                          <>
                            <span className="outcome outcome--failure">FAILED</span>
                            {r.error_message ? (
                              <div className="dim" style={{ whiteSpace: "normal" }}>
                                {r.error_message}
                              </div>
                            ) : null}
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <Icon name="audit" size={20} stroke={1.6} />
              <div className="empty__title">No matching actions</div>
              <div className="empty__desc">
                Write actions — kills, health overrides, exports — appear here
                with who performed them and why. Nothing matches the current
                filters.
              </div>
            </div>
          )}
        </section>
      </main>
    </>
  );
}
