import { useState } from "react";
import { Link } from "react-router";

import { ApiError, api, type Envelope } from "../api";
import { ClusterTabs, useCluster } from "../components/ClusterTabs";
import { Freshness } from "../components/Freshness";
import { Icon } from "../components/Icon";
import { relativeTime } from "../format";
import { MANAGE_HEALTH, useCapability } from "../useCapability";
import { useApi } from "../useApi";

interface Node {
  host: string;
  address: string;
  role: string;
  state: string | null;
  version: string | null;
  environment: string | null;
  uptime: string | null;
  reachable: boolean;
  error: string | null;
}

interface FleetData {
  enabled: boolean;
  nodes: Node[];
  summary: {
    total?: number; reachable?: number; unreachable?: number; shutting_down?: number;
  };
  notes?: string[];
  limits?: string[];
  node_counts?: Record<string, number>;
  can_identify?: boolean;
  unavailable_reason?: string | null;
  advice?: string | null;
}

interface JobRun {
  id: number;
  job: string;
  actor: string;
  reason: string;
  state: string;
  started_at: string;
}

interface Jobs {
  enabled: boolean;
  definitions: {
    key: string;
    title: string;
    description: string;
    parameters: { name: string; label: string; min: number; max: number; default: number }[];
  }[];
  runs: JobRun[];
  active: { id: number } | null;
}

const JOB_STATE: Record<string, { klass: string; text: string; title?: string }> = {
  RUNNING: { klass: "status status--running", text: "running" },
  SUCCEEDED: { klass: "status status--good", text: "succeeded" },
  UNKNOWN: {
    klass: "status status--unknown", text: "unknown",
    title: "TMS restarted while this was running, so its outcome was never observed",
  },
};

export function Fleet() {
  const [cluster, selectCluster, names] = useCluster();
  const canManage = useCapability(MANAGE_HEALTH) === true;
  const base = `/clusters/${encodeURIComponent(cluster)}/fleet`;
  const nodes = useApi<Envelope<FleetData>>(cluster ? base : null, 15_000);
  const jobs = useApi<Jobs>(cluster ? `${base}/jobs` : null, 5_000);
  const [notice, setNotice] = useState<{ good: boolean; text: string } | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const fleet = nodes.data?.data;

  async function identify() {
    setNotice(null);
    try {
      const result = await api.post<{
        available: boolean; error: string | null; advice: string | null;
        unjoined: { host?: string; address?: string }[];
        unexpected: { http_uri?: string }[];
      }>(`${base}/identify`);
      if (!result.available) {
        setNotice({ good: false, text: result.advice || result.error
          || "The coordinator's node list could not be read." });
      } else if (result.unjoined.length) {
        setNotice({ good: false, text: `Not joined to discovery: ${
          result.unjoined.map((n) => n.host || n.address || "?").join(", ")}` });
      } else if (result.unexpected.length) {
        setNotice({ good: false, text: `Serving queries but not in the inventory: ${
          result.unexpected.map((r) => r.http_uri || "?").join(", ")}` });
      } else {
        setNotice({ good: true,
                    text: "Every node in the inventory is joined to discovery." });
      }
    } catch (caught) {
      setNotice({ good: false,
                  text: caught instanceof ApiError ? caught.message : String(caught) });
    }
  }

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Fleet</span>
        <span className="spacer" />
        <ClusterTabs selected={cluster} names={names} onSelect={selectCluster} />
        {nodes.data ? (
          <Freshness collectedAt={nodes.data.collected_at} stale={nodes.data.stale} />
        ) : null}
      </header>

      <main className="content" id="main">
        {nodes.error ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" size={15} stroke={2} />
            <div>{nodes.error.message}</div>
          </div>
        ) : null}

        {notice ? (
          <div className={`banner banner--${notice.good ? "good" : "bad"}`} role="alert">
            <Icon name={notice.good ? "good" : "bad"} size={15} stroke={2} />
            <div>{notice.text}</div>
          </div>
        ) : null}

        {fleet?.unavailable_reason ? (
          <div className="banner banner--concerning" role="status">
            <Icon name="concerning" size={15} stroke={2} />
            <div>
              <b>{fleet.unavailable_reason}</b>
              {fleet.advice ? <div>{fleet.advice}</div> : null}
            </div>
          </div>
        ) : null}

        {/* Disagreements across nodes. Each is invisible in a single row and
            obvious across the fleet, and each one is a real incident shape. */}
        {(fleet?.notes ?? []).map((note) => (
          <div className="banner banner--concerning" role="status" key={note}>
            <Icon name="concerning" size={15} stroke={2} />
            <div>{note}</div>
          </div>
        ))}

        {fleet?.nodes.length ? (
          <>
            <div className="panel">
              <div className="facts">
                <Fact value={fleet.summary.total} label="In the inventory" />
                <Fact value={fleet.summary.reachable} label="Answering" />
                <Fact value={fleet.summary.unreachable} label="Not answering"
                      planned={!!fleet.summary.unreachable} />
                {/* Draining is deliberate, so it is amber and never red. */}
                <Fact value={fleet.summary.shutting_down} label="Shutting down"
                      planned={!!fleet.summary.shutting_down} />
              </div>
            </div>

            <div className="panel">
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Node</th>
                      <th scope="col">Role</th>
                      <th scope="col">State</th>
                      <th scope="col">Version</th>
                      <th scope="col">Environment</th>
                      <th scope="col" className="num">Uptime</th>
                      <th scope="col" />
                    </tr>
                  </thead>
                  <tbody>
                    {fleet.nodes.map((node) => (
                      <NodeRow key={node.host} node={node} base={base}
                               canManage={canManage}
                               confirming={confirming === node.host}
                               onConfirm={() => setConfirming(node.host)}
                               onCancel={() => setConfirming(null)}
                               onDone={() => { setConfirming(null); nodes.reload(); }}
                               onFail={(text) => setNotice({ good: false, text })} />
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* What this screen cannot tell you. Stated rather than left to be
                inferred from a missing column — an omitted fact reads as a
                fact that is fine. */}
            <div className="panel">
              <div className="panel__head">
                <span className="panel__title">What this screen does not know</span>
              </div>
              <div className="seq__act">
                {(fleet.limits ?? []).map((limit) => (
                  <p className="seq__act-why" key={limit}>{limit}</p>
                ))}
                {fleet.can_identify ? (
                  <>
                    {/* Offered, not automatic: this costs the coordinator a
                        query slot, and D-012 only holds while these stay
                        rare. So it appears where the count already disagrees
                        and someone is looking at it. */}
                    <button className="btn btn--sm" type="button" onClick={identify}>
                      Ask the coordinator which node is missing
                    </button>
                    <p className="seq__act-why">
                      Runs one <code className="mono">SELECT</code> against{" "}
                      <code className="mono">system.runtime.nodes</code>. TMS
                      does not poll this — the count above comes from JMX, and
                      this is the only way to turn a count into a name.
                    </p>
                  </>
                ) : null}
                {fleet.node_counts ? (
                  <div className="kv">
                    {Object.entries(fleet.node_counts).sort().map(([key, value]) => (
                      <div className="kv__cell" key={key}>
                        <div className="kv__key">{key.replace("NodeCount", "")}</div>
                        <div className="kv__value num">{value}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>

            {jobs.data?.enabled ? (
              <JobsPanel jobs={jobs.data} base={base} cluster={cluster}
                         canManage={canManage} onChange={jobs.reload}
                         onFail={(text) => setNotice({ good: false, text })} />
            ) : null}
          </>
        ) : !fleet?.unavailable_reason && nodes.data ? (
          <div className="panel">
            <div className="empty">
              <Icon name="info" size={20} stroke={1.6} />
              <div className="empty__title">No nodes collected yet</div>
              <div className="empty__desc">
                This screen is where node addresses live — every coordinator and
                worker, with the address TMS reaches it on. TMS does not
                discover them: it reads the Ansible inventory you point it at,
                then asks each node about itself.
                {/* Three settings, and missing any one of them produces an
                    empty screen rather than an error. Naming all three beats
                    finding out one at a time. */}
                <div className="stack" style={{ marginTop: 10, textAlign: "left" }}>
                  <div>
                    <code className="mono">fleet.enabled: true</code>
                  </div>
                  <div>
                    <code className="mono">fleet.inventories</code> — a file per
                    cluster, with <code className="mono">[coordinator]</code> and{" "}
                    <code className="mono">[workers]</code> groups
                  </div>
                  <div>
                    <code className="mono">fleet.node_url_template</code> — an
                    inventory carries addresses, not schemes or ports
                  </div>
                </div>
                <div style={{ marginTop: 10 }}>
                  All three set? Give it one poll interval
                  {" "}(<code className="mono">fleet.poll_interval_seconds</code>).
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </main>
    </>
  );
}

function Fact({ value, label, planned }: {
  value: number | undefined; label: string; planned?: boolean;
}) {
  return (
    <div className="fact">
      <div className={`fact__value num${planned ? " fact__planned" : ""}`}>
        {value ?? 0}
      </div>
      <div className="fact__key">{label}</div>
    </div>
  );
}

function NodeRow({ node, base, canManage, confirming, onConfirm, onCancel, onDone, onFail }: {
  node: Node;
  base: string;
  canManage: boolean;
  confirming: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onDone: () => void;
  onFail: (message: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const drainable = canManage && node.role === "worker" && node.reachable
    && node.state !== "SHUTTING_DOWN";

  async function shutdown() {
    setBusy(true);
    try {
      await api.post(`${base}/nodes/${encodeURIComponent(node.host)}/shutdown`, { reason });
      onDone();
    } catch (caught) {
      onFail(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <tr>
        <td>
          <span className="mono">{node.host}</span>
          {node.address !== node.host ? (
            <div className="muted mono">{node.address}</div>
          ) : null}
        </td>
        <td><span className="tag">{node.role}</span></td>
        <td>
          {!node.reachable ? (
            <span className="status status--unknown" title={node.error ?? undefined}>
              <Icon name="unknown" size={12} stroke={2} />No answer
            </span>
          ) : node.state === "SHUTTING_DOWN" ? (
            <span className="status status--concerning">
              <Icon name="clock" size={12} stroke={2} />Draining
            </span>
          ) : node.state === "ACTIVE" ? (
            <span className="status status--good">
              <Icon name="good" size={12} stroke={2} />Active
            </span>
          ) : (
            <span className="status status--unknown">{node.state || "Unknown"}</span>
          )}
        </td>
        <td className="mono">{node.version || "—"}</td>
        <td className="mono">{node.environment || "—"}</td>
        <td className="num">{node.uptime || "—"}</td>
        <td className="row-actions">
          {drainable && !confirming ? (
            <button className="btn btn--sm" type="button" onClick={onConfirm}>
              Shut down
            </button>
          ) : null}
        </td>
      </tr>

      {confirming ? (
        <tr className="fleet__confirm">
          <td colSpan={7}>
            <div className="stack">
              <div className="field">
                <label htmlFor={`reason-${node.host}`}>
                  Why is {node.host} being shut down?{" "}
                  <span className="req">*</span>
                </label>
                <textarea id={`reason-${node.host}`} rows={2} required
                          placeholder="e.g. scaling down for the weekend, CHG-4482"
                          value={reason} onChange={(e) => setReason(e.target.value)} />
                <div className="field__hint">
                  <Icon name="concerning" size={12} stroke={2} />
                  <span>
                    Trino drains it first: it stops accepting new tasks,
                    finishes the ones it has, then exits. That takes at least
                    twice <code className="mono">shutdown.grace-period</code> —
                    about four minutes on the defaults — and the node stays
                    listed until it goes. <b>Running queries are not killed.</b>
                  </span>
                </div>
              </div>
              <div className="row">
                <button className="btn btn--danger" type="button"
                        disabled={busy || !reason.trim()} onClick={shutdown}>
                  {busy ? "Draining…" : `Drain and shut down ${node.host}`}
                </button>
                <button className="btn btn--ghost" type="button" onClick={onCancel}>
                  Cancel
                </button>
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function JobsPanel({ jobs, base, cluster, canManage, onChange, onFail }: {
  jobs: Jobs;
  base: string;
  cluster: string;
  canManage: boolean;
  onChange: () => void;
  onFail: (message: string) => void;
}) {
  return (
    /* Deliberately below the inventory: this screen is read far more often than
       it is acted on, and a control that changes the cluster should not be the
       first thing under the operator's cursor. */
    <div className="panel">
      <div className="panel__head">
        <span className="panel__title">Jobs</span>
        <span className="spacer" />
        <span className="panel__sub">configured playbooks, run on request</span>
      </div>

      {jobs.active ? (
        <div className="banner banner--concerning" role="status">
          <Icon name="clock" size={15} stroke={2} />
          <div>
            <b>A job is running on this cluster.</b>{" "}
            <Link to={`/fleet/jobs/${jobs.active.id}`}>Follow it</Link> — a
            second one cannot start until it ends. Two playbooks writing the
            same inventory at once is not a conflict anyone can untangle
            afterwards.
          </div>
        </div>
      ) : canManage ? (
        jobs.definitions.map((job) => (
          <JobForm key={job.key} job={job} base={base} cluster={cluster}
                   onChange={onChange} onFail={onFail} />
        ))
      ) : null}

      {jobs.runs.length ? (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Started</th>
                <th scope="col">Job</th>
                <th scope="col">By</th>
                <th scope="col">Reason</th>
                <th scope="col">Result</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {jobs.runs.map((run) => {
                const state = JOB_STATE[run.state]
                  ?? { klass: "status status--bad", text: "failed" };
                return (
                  <tr key={run.id}>
                    <td className="num">{relativeTime(run.started_at)}</td>
                    <td><code className="mono">{run.job}</code></td>
                    <td>{run.actor}</td>
                    <td className="wrap">{run.reason}</td>
                    <td>
                      <span className={state.klass} title={state.title}>
                        {state.text}
                      </span>
                    </td>
                    <td className="row-actions">
                      <Link className="btn btn--sm btn--ghost"
                            to={`/fleet/jobs/${run.id}`}>Open</Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      <p className="panel__note">
        ⛔ These are not restarts. TMS runs the playbook and reads its exit
        code; it does not know whether anything was drained first. Restarting a
        cluster goes through the{" "}
        <Link to={`/restart?cluster=${encodeURIComponent(cluster)}`}>
          safe restart sequence
        </Link>
        , which stops traffic before anything stops.
      </p>
    </div>
  );
}

function JobForm({ job, base, cluster, onChange, onFail }: {
  job: Jobs["definitions"][number];
  base: string;
  cluster: string;
  onChange: () => void;
  onFail: (message: string) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(job.parameters.map((p) => [p.name, String(p.default)])));
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      await api.post(`${base}/jobs/${encodeURIComponent(job.key)}`, {
        reason,
        parameters: Object.fromEntries(
          Object.entries(values).map(([name, value]) => [name, Number(value)])),
      });
      setReason("");
      setConfirming(false);
      onChange();
    } catch (caught) {
      onFail(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rg-add">
      <div className="rg-add__grid">
        <div className="field">
          <b>{job.title}</b>
          {job.description ? (
            <div className="panel__sub">{job.description}</div>
          ) : null}
        </div>
        {job.parameters.map((parameter) => (
          <label className="field" key={parameter.name}>
            {parameter.label}
            <input className="input num" type="number" min={parameter.min}
                   max={parameter.max} value={values[parameter.name]}
                   onChange={(e) => setValues({ ...values, [parameter.name]: e.target.value })} />
          </label>
        ))}
        <label className="field">
          Reason
          <input className="input" required placeholder="Why is this being run?"
                 value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        {confirming ? (
          <div className="row-actions">
            <span className="hint">Run {job.title} against {cluster}?</span>
            <button className="btn btn--danger" type="button" disabled={busy}
                    onClick={run}>
              {busy ? "Starting…" : "Yes, run it"}
            </button>
            <button className="btn btn--ghost" type="button"
                    onClick={() => setConfirming(false)}>Cancel</button>
          </div>
        ) : (
          <button className="btn btn--danger" type="button"
                  disabled={!reason.trim()} onClick={() => setConfirming(true)}>
            Run
          </button>
        )}
      </div>
    </div>
  );
}
