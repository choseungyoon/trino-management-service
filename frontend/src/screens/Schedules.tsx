import { useState } from "react";
import { Link } from "react-router";

import { ApiError, api } from "../api";
import { Icon } from "../components/Icon";
import { relativeTime, untilTime } from "../format";
import { useApi } from "../useApi";

interface Schedule {
  id: number;
  name: string;
  query_set: string;
  clusters: string[];
  repetitions: number;
  label: string | null;
  reason: string;
  interval_minutes: number;
  next_run_at: string;
  enabled: boolean;
  paused_reason: string | null;
  paused_by_tms: boolean;
  consecutive_failures: number;
  last_run_at: string | null;
  last_outcome: string | null;
  created_by: string;
}

interface Page {
  available: boolean;
  schedules: Schedule[];
  can_edit: boolean;
  min_interval_minutes: number;
  failure_limit: number;
}

interface QuerySet {
  key: string;
  title: string;
}

/** The intervals anyone actually asks for, in minutes. */
const PERIODS = [
  [60, "Every hour"],
  [360, "Every 6 hours"],
  [720, "Every 12 hours"],
  [1440, "Daily"],
  [10080, "Weekly"],
] as const;

function every(minutes: number): string {
  const named = PERIODS.find(([m]) => m === minutes);
  if (named) return named[1];
  if (minutes % 1440 === 0) return `Every ${minutes / 1440} days`;
  if (minutes % 60 === 0) return `Every ${minutes / 60} hours`;
  return `Every ${minutes} minutes`;
}

export function Schedules() {
  // 30s: the ticker fires on that cadence, so a schedule's next-run time never
  // sits more than one poll behind what the server would do.
  const { data, error, reload } = useApi<Page>("/benchmark/schedules", 30_000);
  const overview = useApi<{ query_sets: QuerySet[]; clusters: { name: string }[] }>(
    "/benchmark");
  const [failure, setFailure] = useState<string | null>(null);

  async function guarded(work: () => Promise<unknown>) {
    setFailure(null);
    try {
      await work();
      reload();
      return true;
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : String(caught));
      return false;
    }
  }

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Benchmark schedules</span>
        <span className="spacer" />
        <Link className="btn btn--sm" to="/benchmark">← Benchmark</Link>
      </header>

      <main className="content" id="main">
        {error && !data ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" size={15} stroke={2} />
            <div>{error.message}</div>
          </div>
        ) : null}
        {failure ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" size={15} stroke={2} />
            <div>{failure}</div>
          </div>
        ) : null}

        {data && !data.available ? (
          <div className="banner" role="status">
            <Icon name="info" size={15} stroke={2} />
            <div>
              <b>Schedules are not available on this deployment.</b> Migration
              020 has not been applied, or the schedule store could not be
              opened. Benchmarks still run when somebody starts them.
            </div>
          </div>
        ) : null}

        {/* ⛔ Said once, at the top. A schedule authorises runs against a
            production cluster at a time when nobody is watching, and the
            reason typed here is the only explanation their audit records will
            ever carry. */}
        <div className="banner" role="note">
          <Icon name="lock" size={15} stroke={2} />
          <div>
            <strong>
              A schedule starts real benchmarks with nobody watching.
            </strong>{" "}
            Every run it starts is recorded against you, with the reason you
            give below. A run whose cluster is already busy is skipped, not
            queued — and a schedule that fails {data?.failure_limit ?? 3} times
            in a row switches itself off rather than adding load nobody is
            reading.
          </div>
        </div>

        {data?.available ? (
          <section className="panel">
            <div className="panel__head">
              <div className="panel__title">Schedules</div>
              <div className="panel__sub">
                Times are this browser's. The server keeps them to the minute
                they were set, so a daily one does not drift.
              </div>
            </div>
            {data.schedules.length ? (
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Name</th>
                      <th scope="col">Runs</th>
                      <th scope="col">Clusters</th>
                      <th scope="col">Next</th>
                      <th scope="col">Last</th>
                      <th scope="col">State</th>
                      <th scope="col" />
                    </tr>
                  </thead>
                  <tbody>
                    {data.schedules.map((row) => (
                      <Row key={row.id} row={row} canEdit={data.can_edit}
                           guarded={guarded} />
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty">
                <Icon name="clock" size={20} stroke={1.6} />
                <div className="empty__title">Nothing runs by itself yet</div>
                <div className="empty__desc">
                  A trend needs points taken the same way at a regular interval.
                  Adding one below is how the chart fills in without anybody
                  remembering to press a button.
                </div>
              </div>
            )}

            {data.can_edit ? (
              <NewSchedule sets={overview.data?.query_sets ?? []}
                           clusters={overview.data?.clusters ?? []}
                           minInterval={data.min_interval_minutes}
                           guarded={guarded} />
            ) : null}
          </section>
        ) : null}
      </main>
    </>
  );
}

function Row({ row, canEdit, guarded }: {
  row: Schedule;
  canEdit: boolean;
  guarded: (work: () => Promise<unknown>) => Promise<boolean>;
}) {
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState<"toggle" | "delete" | null>(null);

  return (
    <>
      <tr>
        <td>
          <span className="mono">{row.name}</span>
          <div className="dim wrap">{row.reason}</div>
        </td>
        <td>
          <span className="mono">{row.query_set}</span>
          <div className="dim">
            {every(row.interval_minutes)} · {row.repetitions}×
          </div>
        </td>
        <td className="mono">{row.clusters.join(", ")}</td>
        <td className="num">
          {row.enabled ? (
            <span title={new Date(row.next_run_at).toLocaleString()}>
              {untilTime(row.next_run_at)}
            </span>
          ) : (
            <span className="dim">—</span>
          )}
        </td>
        <td className="num dim">
          {row.last_run_at ? relativeTime(row.last_run_at) : "never"}
          {row.last_outcome ? (
            <div className="wrap">{row.last_outcome}</div>
          ) : null}
        </td>
        <td>
          {/* ⛔ Three states, not two. "somebody switched this off" and "this
              broke and was switched off for them" need different answers. */}
          {row.enabled ? (
            <span className="status status--good">
              <Icon name="good" size={12} stroke={2} />on
            </span>
          ) : row.paused_by_tms ? (
            <span className="status status--bad" title={row.paused_reason ?? undefined}>
              <Icon name="bad" size={12} stroke={2} />paused by TMS
            </span>
          ) : (
            <span className="status status--unknown">off</span>
          )}
        </td>
        <td className="row-actions">
          {canEdit && !confirming ? (
            <>
              <button className="btn btn--sm" type="button"
                      onClick={() => setConfirming("toggle")}>
                {row.enabled ? "Turn off" : "Turn on"}
              </button>
              <button className="btn btn--sm btn--ghost" type="button"
                      onClick={() => setConfirming("delete")}>
                Delete
              </button>
            </>
          ) : null}
        </td>
      </tr>

      {confirming ? (
        <tr className="row-editing">
          <td colSpan={7}>
            <div className="confirm">
              <div className="confirm__body">
                {confirming === "delete" ? (
                  <>
                    <b>Delete <code className="mono">{row.name}</code>?</b>
                    <div className="confirm__impact">
                      The runs it already started stay — the measurements
                      outlive the reason they were taken.
                    </div>
                  </>
                ) : row.enabled ? (
                  <b>Stop <code className="mono">{row.name}</code> running?</b>
                ) : (
                  <>
                    <b>Start <code className="mono">{row.name}</code> again?</b>
                    {row.paused_by_tms ? (
                      <div className="confirm__impact">
                        TMS paused this: {row.paused_reason}. Turning it back on
                        clears the failure count, so fix the cause first.
                      </div>
                    ) : null}
                  </>
                )}
              </div>
              <div className="confirm__actions">
                <input className="input input--sm" required aria-label="Reason"
                       placeholder="Why" value={reason}
                       onChange={(e) => setReason(e.target.value)} />
                <button className={`btn btn--sm ${
                          confirming === "delete" ? "btn--danger" : "btn--primary"}`}
                        type="button" disabled={!reason.trim()}
                        onClick={async () => {
                          const done = await guarded(() =>
                            confirming === "delete"
                              ? api.del(`/benchmark/schedules/${row.id}`
                                  + `?reason=${encodeURIComponent(reason)}`)
                              : api.post(`/benchmark/schedules/${row.id}/enabled`,
                                         { enabled: !row.enabled, reason }));
                          if (done) { setConfirming(null); setReason(""); }
                        }}>
                  {confirming === "delete" ? "Delete"
                    : row.enabled ? "Turn off" : "Turn on"}
                </button>
                <button className="btn btn--sm btn--ghost" type="button"
                        onClick={() => setConfirming(null)}>Cancel</button>
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function NewSchedule({ sets, clusters, minInterval, guarded }: {
  sets: QuerySet[];
  clusters: { name: string }[];
  minInterval: number;
  guarded: (work: () => Promise<unknown>) => Promise<boolean>;
}) {
  const [form, setForm] = useState({
    name: "", query_set: "", interval_minutes: "1440", repetitions: "1",
    label: "", reason: "", starts_at: "",
  });
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const edit = (field: keyof typeof form) =>
    (e: { target: { value: string } }) => setForm({ ...form, [field]: e.target.value });

  return (
    <details className="rg-add">
      <summary>New schedule</summary>
      <div className="rg-add__grid">
        <label className="field">Name
          <input className="input mono" placeholder="nightly-adhoc" maxLength={200}
                 value={form.name} onChange={edit("name")} />
        </label>
        <label className="field">Query set
          <select className="input" value={form.query_set} onChange={edit("query_set")}>
            <option value="">choose a set…</option>
            {sets.map((s) => (
              <option key={s.key} value={s.key}>{s.title}</option>
            ))}
          </select>
        </label>
        <label className="field">How often
          <select className="input" value={form.interval_minutes}
                  onChange={edit("interval_minutes")}>
            {PERIODS.filter(([m]) => m >= minInterval).map(([m, text]) => (
              <option key={m} value={m}>{text}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>First run <span className="dim">(optional)</span></span>
          {/* Native datetime-local: it is what sets the anchor a daily
              schedule keeps. Empty means "now, then every interval". */}
          <input className="input" type="datetime-local" value={form.starts_at}
                 onChange={edit("starts_at")} />
        </label>
        <label className="field">Repetitions
          <input className="input num" type="number" min={1} max={50}
                 value={form.repetitions} onChange={edit("repetitions")} />
        </label>
        <label className="field">
          <span>Label <span className="dim">(optional)</span></span>
          <input className="input" maxLength={200} placeholder="e.g. nightly"
                 value={form.label} onChange={edit("label")} />
        </label>

        <fieldset className="fieldgroup">
          <legend className="fieldgroup__legend">Clusters</legend>
          {clusters.map((cluster) => (
            <label className="pick__pick" key={cluster.name}>
              <input type="checkbox" checked={chosen.includes(cluster.name)}
                     onChange={() => setChosen((current) =>
                       current.includes(cluster.name)
                         ? current.filter((c) => c !== cluster.name)
                         : [...current, cluster.name])} />
              <span className="pick__name mono">{cluster.name}</span>
            </label>
          ))}
        </fieldset>

        <label className="field">
          <span>Reason <span className="dim">(required)</span></span>
          <input className="input" required maxLength={500}
                 placeholder="Recorded on every run this starts"
                 value={form.reason} onChange={edit("reason")} />
        </label>

        <button className="btn btn--primary" type="button"
                disabled={busy || !form.name.trim() || !form.query_set
                          || !chosen.length || !form.reason.trim()}
                onClick={async () => {
                  setBusy(true);
                  const made = await guarded(() => api.post("/benchmark/schedules", {
                    name: form.name,
                    query_set: form.query_set,
                    clusters: chosen,
                    interval_minutes: Number(form.interval_minutes),
                    repetitions: Number(form.repetitions),
                    label: form.label || null,
                    reason: form.reason,
                    starts_at: form.starts_at
                      ? new Date(form.starts_at).toISOString() : null,
                  }));
                  setBusy(false);
                  if (made) {
                    setForm({ ...form, name: "", reason: "", starts_at: "" });
                    setChosen([]);
                  }
                }}>
          {busy ? "Creating…" : "Create schedule"}
        </button>
      </div>
    </details>
  );
}
