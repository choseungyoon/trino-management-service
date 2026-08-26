import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError, api } from "../api";
import { ClusterTabs, useCluster } from "../components/ClusterTabs";
import { Icon } from "../components/Icon";
import { duration } from "../format";
import { MANAGE_HEALTH, useCapability } from "../useCapability";
import { useApi } from "../useApi";

interface Step {
  state: string;
  label: string;
  status?: string;
  number: number;
}

interface Sequence {
  id: number;
  cluster: string;
  reason: string;
  actor: string;
  state: string;
  label: string;
  running_queries: number | null;
  health_state: string | null;
  config_store_ready: boolean | null;
  config_store_detail: string | null;
  traffic_stopped: boolean;
  is_terminal: boolean;
  automated: boolean;
  executor_state: string | null;
  executor: { title: string; instructions: string; waiting: string };
  steps: Step[];
  duration_ms: number | null;
  started_at: string;
  finished_at: string | null;
  history: { at: string; level: string; message: string }[];
}

interface Overview {
  recent: Sequence[];
  active: Sequence[];
  preview: Step[];
}

/** The whole restart, on one screen. `/restart` starts one; `/restarts/:id` watches one. */
export function Restart() {
  const [cluster, selectCluster, names] = useCluster();
  const canManage = useCapability(MANAGE_HEALTH) === true;
  const navigate = useNavigate();
  const overview = useApi<Overview>("/restarts", 5_000);
  const [failure, setFailure] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  // ⛔ A cluster held out of rotation is invisible on every other screen: the
  // ones that remain look healthy while traffic is being refused. So an active
  // sequence takes over this screen rather than sitting in a list.
  const active = overview.data?.active.find((s) => s.cluster === cluster);
  if (active) return <Live sequenceId={active.id} />;

  const recent = overview.data?.recent.filter((s) => s.cluster === cluster) ?? [];
  const unavailable = overview.error?.unavailable;

  async function begin() {
    setBusy(true);
    setFailure(null);
    try {
      const started = await api.post<Sequence>(
        `/clusters/${encodeURIComponent(cluster)}/restarts`, { reason });
      navigate(`/restarts/${started.id}`);
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : String(caught));
      setBusy(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Safe Restart</span>
        <span className="spacer" />
        <ClusterTabs selected={cluster} names={names} onSelect={selectCluster} />
      </header>

      <main className="content" id="main">
        {failure ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" size={15} stroke={2} />
            <div>{failure}</div>
          </div>
        ) : null}

        {unavailable ? (
          <section className="panel">
            <div className="empty">
              <Icon name="unknown" size={20} stroke={1.6} />
              <div className="empty__title">Restarts are not available</div>
              <div className="empty__desc">
                Stopping traffic to a cluster needs the Trino Gateway, and the
                Gateway integration is off. A restart that skipped that step
                would kill every query running on the cluster, so TMS does not
                offer one.
              </div>
            </div>
          </section>
        ) : (
          <>
            {/* Same two-column shape as a running restart: steps and controls
                on the left, progress on the right. The panel is idle rather
                than absent so the layout does not rearrange itself the moment
                someone starts a restart. */}
            <div className="seq">
              <section className="panel seq__steps">
                <div className="panel__head">
                  <span className="panel__title">{cluster}</span>
                  <span className="panel__sub">
                    six steps, in order, none of them skippable
                  </span>
                </div>
                {/* The order is the feature. Someone about to take a cluster
                    out of rotation should see what TMS is going to do before
                    they start, not discover it one button at a time. */}
                <Steps steps={overview.data?.preview ?? []} />

                <div className="seq__act">
                  {canManage ? (
                    <div className="stack">
                      <div className="field">
                        <label htmlFor="reason">
                          Why is {cluster} being restarted?{" "}
                          <span className="req">*</span>
                        </label>
                        <textarea id="reason" rows={3} required value={reason}
                                  placeholder="e.g. applying the new memory configuration from CHG-4471"
                                  onChange={(e) => setReason(e.target.value)} />
                        <div className="field__hint">
                          <Icon name="concerning" size={12} stroke={2} />
                          <span>
                            Submitting stops traffic to {cluster} immediately.
                            Queries already running keep going; TMS waits for
                            them.
                          </span>
                        </div>
                      </div>
                      <button className="btn btn--danger btn--block" type="button"
                              disabled={busy || !reason.trim()} onClick={begin}>
                        {busy ? "Stopping traffic…" : "Begin the restart sequence"}
                      </button>
                    </div>
                  ) : (
                    <div className="field__hint">
                      <Icon name="lock" size={12} stroke={2} />
                      <span>Restarting a cluster is restricted to administrators.</span>
                    </div>
                  )}
                </div>
              </section>

              <IdleConsole />
            </div>

            <section className="panel">
              <div className="panel__head">
                <span className="panel__title">Earlier restarts</span>
                <span className="panel__sub">{cluster}</span>
              </div>
              {recent.length ? (
                <div className="table-scroll">
                  <table className="table">
                    <thead>
                      <tr>
                        <th scope="col">State</th>
                        <th scope="col">Started</th>
                        <th scope="col">By</th>
                        <th scope="col">Reason</th>
                        <th scope="col" />
                      </tr>
                    </thead>
                    <tbody>
                      {recent.map((item) => (
                        <tr key={item.id}>
                          <td><SequenceState state={item.state} /></td>
                          <td className="num">
                            {new Date(item.started_at).toLocaleString()}
                          </td>
                          <td>{item.actor}</td>
                          <td className="muted wrap">{item.reason}</td>
                          <td className="row-actions">
                            <Link className="btn btn--sm btn--ghost"
                                  to={`/restarts/${item.id}`}>Open</Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="empty">
                  <Icon name="history" size={20} stroke={1.6} />
                  <div className="empty__title">No restarts recorded</div>
                  <div className="empty__desc">
                    Restarts run through TMS appear here with who ran them and why.
                  </div>
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </>
  );
}

export function RestartSequenceScreen() {
  const { id = "" } = useParams();
  return <Live sequenceId={id} />;
}

function Live({ sequenceId }: { sequenceId: string | number }) {
  const canManage = useCapability(MANAGE_HEALTH) === true;
  // ⛔ Reading refreshes what the coordinator says, so the step shown is the
  // step the server would allow - not the one it allowed last time somebody
  // looked. Polling stops once the sequence is terminal.
  const { data: first, error } = useApi<Sequence>(`/restarts/${sequenceId}`);
  const live = useApi<Sequence>(
    first && !first.is_terminal ? `/restarts/${sequenceId}` : null, 3_000);
  const sequence = live.data ?? first;
  const [failure, setFailure] = useState<string | null>(null);

  async function act(path: string, body?: unknown) {
    setFailure(null);
    try {
      await api.post(`/restarts/${sequenceId}/${path}`, body ?? {});
      live.reload();
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : String(caught));
    }
  }

  if (error) {
    return (
      <>
        <header className="topbar">
          <span className="topbar__title">Safe Restart</span>
        </header>
        <main className="content" id="main">
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" size={15} stroke={2} />
            <div>{error.message}</div>
          </div>
        </main>
      </>
    );
  }
  if (!sequence) return null;

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">
          Safe Restart <span className="dim">{sequence.cluster}</span>
        </span>
        <span className="spacer" />
        {sequence.traffic_stopped ? (
          <span className="status status--concerning">
            <Icon name="concerning" size={12} stroke={2} />No traffic
          </span>
        ) : sequence.state === "COMPLETED" ? (
          <span className="status status--good">
            <Icon name="good" size={12} stroke={2} />In rotation
          </span>
        ) : null}
      </header>

      <main className="content" id="main">
        {failure ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" size={15} stroke={2} />
            <div>{failure}</div>
          </div>
        ) : null}

        <div className="seq">
          <section className="panel seq__steps">
            <div className="panel__head">
              <span className="panel__title">{sequence.cluster}</span>
            </div>
            <Steps steps={sequence.steps} />

            {/* What TMS currently knows about the cluster. Every gate below is
                decided on these two numbers, so they are shown rather than
                left implied. */}
            <div className="seq__facts">
              <div className="fact">
                <div className="fact__value num">
                  {sequence.running_queries ?? "—"}
                </div>
                <div className="fact__key">Running + queued</div>
              </div>
              <div className="fact">
                <div className="fact__value">{sequence.health_state || "UNKNOWN"}</div>
                <div className="fact__key">Health</div>
              </div>
            </div>

            {canManage && !sequence.is_terminal ? (
              <Controls sequence={sequence} act={act} />
            ) : !canManage && !sequence.is_terminal ? (
              <div className="seq__act">
                <p className="seq__act-why">
                  {sequence.actor} is restarting this cluster. You can watch, but
                  only an administrator can advance the sequence.
                </p>
              </div>
            ) : null}
          </section>

          <Console sequence={sequence} />
        </div>

        {sequence.is_terminal ? (
          <div className="seq__after">
            <Link className="btn"
                  to={`/restart?cluster=${encodeURIComponent(sequence.cluster)}`}>
              Back to {sequence.cluster}
            </Link>
            <Link className="btn btn--ghost"
                  to={`/cluster-health?cluster=${encodeURIComponent(sequence.cluster)}`}>
              Check health
            </Link>
          </div>
        ) : null}
      </main>
    </>
  );
}

function Controls({ sequence, act }: {
  sequence: Sequence;
  act: (path: string, body?: unknown) => Promise<void>;
}) {
  const [forceReason, setForceReason] = useState("");
  const [abortReason, setAbortReason] = useState("");
  const [openForce, setOpenForce] = useState(false);
  const [openAbort, setOpenAbort] = useState(false);
  const running = sequence.running_queries ?? 0;

  return (
    <div className="seq__act">
      {sequence.state === "DRAINING" ? (
        <>
          <p className="seq__act-why">
            TMS is waiting for the cluster to empty. Restarting now would kill
            every query still running on it.
          </p>
          <details className="seq__force" open={openForce}>
            <summary onClick={(e) => { e.preventDefault(); setOpenForce(!openForce); }}>
              A query is stuck and will not finish
            </summary>
            <div className="stack">
              <div className="field">
                <label htmlFor="force-reason">
                  Why override the drain? <span className="req">*</span>
                </label>
                <textarea id="force-reason" rows={2} required value={forceReason}
                          placeholder="e.g. query 20260809_… has been stuck for 40 minutes"
                          onChange={(e) => setForceReason(e.target.value)} />
                <div className="field__hint">
                  <Icon name="concerning" size={12} stroke={2} />
                  <span>
                    The {running || ""} running quer{running === 1 ? "y" : "ies"}{" "}
                    will be killed by the restart. This is recorded separately
                    from a normal drain.
                  </span>
                </div>
              </div>
              <button className="btn btn--danger btn--block" type="button"
                      disabled={!forceReason.trim()}
                      onClick={() => act("force-drain", { reason: forceReason })}>
                Skip the drain and continue
              </button>
            </div>
          </details>
        </>
      ) : null}

      {sequence.state === "DRAINED" ? (
        <>
          {/* The one check about coming back rather than going down: a Trino
              477 coordinator using the db resource group manager exits at
              startup if it cannot read that store (D-010). Stopping the
              cluster now would leave nothing able to restart it, with traffic
              already blocked. */}
          {sequence.config_store_ready === false ? (
            <p className="seq__act-why seq__act-why--blocked">
              <b>{sequence.cluster} would not start again.</b>{" "}
              {sequence.config_store_detail} Fix this first — the button below
              will refuse until it is resolved.
            </p>
          ) : (
            <p className="seq__act-why">{sequence.executor.instructions}</p>
          )}
          <button className="btn btn--danger btn--block" type="button"
                  disabled={sequence.config_store_ready === false}
                  onClick={() => act("restart")}>
            {sequence.executor.title}
          </button>
        </>
      ) : null}

      {sequence.state === "RESTARTING" ? (
        sequence.automated ? (
          sequence.executor_state === "failed" ? (
            /* Saying "the playbook is running" under a failure in the log is
               worse than saying nothing — the operator waits for something
               that already stopped. */
            <div className="banner banner--bad" role="alert">
              <Icon name="bad" size={15} stroke={2} />
              <div>
                <b>The restart failed — nothing was restarted.</b>{" "}
                {sequence.cluster} is drained and still receiving no queries.
                Put it back in rotation below, then start again once the cause
                is fixed. The reason is in the log on the right.
              </div>
            </div>
          ) : sequence.executor_state === "unknown" ? (
            <div className="banner banner--concerning" role="status">
              <Icon name="concerning" size={15} stroke={2} />
              <div>
                <b>TMS lost sight of this restart.</b> It was restarted while
                the playbook was running, so it cannot say whether the cluster
                came back. Check {sequence.cluster} yourself before restoring
                traffic.
              </div>
            </div>
          ) : (
            <p className="seq__act-why">
              The playbook is running. Its output is on the right; TMS moves on
              by itself when it finishes, and will not restore traffic until
              health is GOOD.
            </p>
          )
        ) : (
          <>
            {/* The operator has to act here. Saying nothing and offering only
                an "it is back up" button is how the first real run was read as
                TMS having silently failed. */}
            <div className="banner banner--concerning" role="status">
              <Icon name="concerning" size={15} stroke={2} />
              <div>
                <b>Your turn — TMS is not restarting anything.</b>{" "}
                {sequence.executor.waiting}
              </div>
            </div>
            <button className="btn btn--primary btn--block" type="button"
                    onClick={() => act("restarted")}>
              Done — {sequence.cluster} is back up
            </button>
          </>
        )
      ) : null}

      {sequence.state === "VERIFYING" ? (
        <>
          <p className="seq__act-why">
            {sequence.health_state === "GOOD"
              ? `Health is GOOD. Putting ${sequence.cluster} back in rotation is the last step.`
              : `Health is ${sequence.health_state || "UNKNOWN"}. Traffic is not restored until it is GOOD — this button will refuse until then.`}
          </p>
          <button className="btn btn--primary btn--block" type="button"
                  onClick={() => act("complete")}>
            Restore traffic
          </button>
        </>
      ) : null}

      {sequence.state === "ABORTING" ? (
        <p className="seq__act-why">
          TMS could not put {sequence.cluster} back in rotation. It is still
          receiving no queries — reactivate it in the Gateway, then abort again.
        </p>
      ) : (
        /* ⛔ Abort restores traffic. It is "put it back", not "stop" — a
           sequence abandoned without it leaves a cluster out of rotation with
           nobody watching it. */
        <details className="seq__abort" open={openAbort}>
          <summary onClick={(e) => { e.preventDefault(); setOpenAbort(!openAbort); }}>
            Stop and put the cluster back
          </summary>
          <div className="stack">
            <div className="field">
              <label htmlFor="abort-reason">
                Why stop? <span className="req">*</span>
              </label>
              <textarea id="abort-reason" rows={2} required value={abortReason}
                        placeholder="e.g. the change was called off"
                        onChange={(e) => setAbortReason(e.target.value)} />
            </div>
            <button className="btn btn--block" type="button"
                    disabled={!abortReason.trim()}
                    onClick={() => act("abort", { note: abortReason })}>
              Abort and restore traffic
            </button>
          </div>
        </details>
      )}
    </div>
  );
}

/**
 * The six steps as a numbered rail.
 *
 * Numbering is not decoration here: this is a fixed sequence whose order is
 * the feature, so a step's position is information the reader needs.
 */
function Steps({ steps }: { steps: Step[] }) {
  return (
    <ol className="steps">
      {steps.map((step) => (
        <li className={`step step--${step.status || "preview"}`} key={step.number}>
          <span className="step__mark" aria-hidden="true">
            {step.status === "done" ? (
              <Icon name="good" size={14} stroke={2.4} />
            ) : step.status === "aborted" ? (
              <Icon name="bad" size={12} stroke={2.4} />
            ) : (
              <span className="step__n num">{step.number}</span>
            )}
          </span>
          <span className="step__label">{step.label}</span>
          {step.status === "current" ? (
            <span className="step__now">
              in progress<span className="sr-only"> (current step)</span>
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function SequenceState({ state }: { state: string }) {
  if (state === "COMPLETED") {
    return (
      <span className="status status--good">
        <Icon name="good" size={12} stroke={2} />Completed
      </span>
    );
  }
  if (state === "ABORTED") {
    return (
      <span className="status status--unknown">
        <Icon name="bad" size={12} stroke={2} />Aborted
      </span>
    );
  }
  return (
    <span className="status status--concerning">
      <Icon name="clock" size={12} stroke={2} />
      {state.charAt(0) + state.slice(1).toLowerCase()}
    </span>
  );
}

function IdleConsole() {
  return (
    <section className="panel seq__log">
      <div className="seq__loghead">
        <div className="seq__logtitle">
          <span className="panel__title">Progress</span>
        </div>
      </div>
      <div className="console console--idle">
        <div className="empty">
          <Icon name="clock" size={20} stroke={1.6} />
          <div className="empty__title">No restart in progress</div>
          <div className="empty__desc">
            Every step appears here as it happens — including the restart tool's
            own output, line by line.
          </div>
        </div>
      </div>
    </section>
  );
}

function Console({ sequence }: { sequence: Sequence }) {
  // 24-hour, no date. Every line in this log happens within the same few
  // minutes, so the date says nothing and an "AM" wraps the column onto a
  // second row - halving the density of a log being read live.
  const clock = (iso: string | null) =>
    iso ? new Date(iso).toLocaleTimeString([], { hour12: false }) : "—";

  return (
    <section className="panel seq__log">
      <div className="seq__loghead">
        <div className="seq__logtitle">
          <span className="panel__title">Progress</span>
          {sequence.state === "COMPLETED" ? (
            <span className="status status--good">
              <Icon name="good" size={12} stroke={2} />Completed
            </span>
          ) : sequence.state === "ABORTED" ? (
            <span className="status status--unknown">
              <Icon name="bad" size={12} stroke={2} />Aborted
            </span>
          ) : sequence.state === "ABORTING" ? (
            <span className="status status--bad">
              <Icon name="bad" size={12} stroke={2} />Aborting
            </span>
          ) : (
            <span className="status status--running">
              <Icon name="clock" size={12} stroke={2} />
              {sequence.state.charAt(0) + sequence.state.slice(1).toLowerCase()}
            </span>
          )}
          <span className="spacer" />
          {!sequence.is_terminal ? (
            <span className="seq__live" role="status"><i aria-hidden="true" />live</span>
          ) : null}
        </div>

        {/* The four facts asked about a restart that is taking too long. */}
        <dl className="seq__meta">
          <div><dt>Started</dt><dd className="num">{clock(sequence.started_at)}</dd></div>
          <div>
            <dt>{sequence.is_terminal ? "Took" : "Elapsed"}</dt>
            <dd className="num">{duration(sequence.duration_ms)}</dd>
          </div>
          <div><dt>Finished</dt><dd className="num">{clock(sequence.finished_at)}</dd></div>
          <div><dt>By</dt><dd>{sequence.actor}</dd></div>
        </dl>
        <div className="seq__why" title={sequence.reason}>{sequence.reason}</div>
      </div>

      <div className="console" tabIndex={0} role="log" aria-label="Restart progress">
        {sequence.history.map((line, index) => (
          <div className={`console__line console__line--${line.level}`} key={index}>
            <span className="console__time num">{clock(line.at)}</span>
            <span className="console__text">{line.message}</span>
          </div>
        ))}
        {!sequence.is_terminal ? (
          <div className="console__line console__line--cursor" aria-hidden="true">
            <span className="console__time num" />
            <span className="console__text"><i /></span>
          </div>
        ) : null}
      </div>
    </section>
  );
}
