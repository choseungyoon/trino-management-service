import { useEffect, useRef } from "react";
import { Link, useParams } from "react-router";

import { Icon } from "../components/Icon";
import { useApi } from "../useApi";

interface Run {
  id: number;
  job: string;
  cluster: string;
  actor: string;
  reason: string;
  state: string;
  exit_code: number | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  parameters: Record<string, string | number>;
  output: { level: string; message: string }[];
}

export function FleetJob() {
  const { id = "" } = useParams();
  // Poll only while it is running. A finished run's log never changes, and
  // re-fetching it would scroll somebody off the failure they are reading.
  const first = useApi<Run>(`/fleet/jobs/${id}`);
  const running = first.data?.state === "RUNNING";
  const live = useApi<Run>(running ? `/fleet/jobs/${id}` : null, 2_000);
  const run = live.data ?? first.data;

  const console_ = useRef<HTMLDivElement>(null);
  const lines = run?.output?.length ?? 0;
  useEffect(() => {
    // Follow the tail while it is running. Once it stops, leave the scroll
    // position where the reader put it.
    if (run?.state === "RUNNING" && console_.current) {
      console_.current.scrollTop = console_.current.scrollHeight;
    }
  }, [lines, run?.state]);

  if (first.error) {
    return (
      <>
        <header className="topbar">
          <span className="topbar__title">Fleet job</span>
        </header>
        <main className="content" id="main">
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" size={15} stroke={2} />
            <div>{first.error.message}</div>
          </div>
        </main>
      </>
    );
  }
  if (!run) return null;

  const clock = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString() : "—";

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Fleet job</span>
        <span className="spacer" />
        <Link className="btn btn--sm btn--ghost"
              to={`/fleet?cluster=${encodeURIComponent(run.cluster)}`}>
          Back to fleet
        </Link>
      </header>

      <main className="content" id="main">
        {/* ⛔ Said on the page, not only in the code: this ran a playbook, and
            TMS has no idea what the playbook did beyond its exit code. Someone
            reading a green "succeeded" should know the limit of that claim. */}
        <div className="banner" role="status">
          <Icon name="info" size={15} stroke={2} />
          <div>
            TMS started this playbook and watched its output. It does not know
            what the playbook changed — only whether it exited cleanly. Confirm
            the result on the{" "}
            <Link to={`/fleet?cluster=${encodeURIComponent(run.cluster)}`}>
              fleet inventory
            </Link>.
          </div>
        </div>

        <section className="panel seq__log">
          <div className="seq__loghead">
            <div className="seq__logtitle">
              <span className="panel__title">{run.job} · {run.cluster}</span>
              <JobState state={run.state} />
              <span className="spacer" />
              {run.state === "RUNNING" ? (
                <span className="seq__live" role="status">
                  <i aria-hidden="true" />live
                </span>
              ) : null}
            </div>

            <dl className="seq__meta">
              <div><dt>Started</dt><dd className="num">{clock(run.started_at)}</dd></div>
              <div><dt>Finished</dt><dd className="num">{clock(run.finished_at)}</dd></div>
              <div><dt>By</dt><dd>{run.actor}</dd></div>
              <div><dt>Exit</dt>
                <dd className="num">{run.exit_code ?? "—"}</dd></div>
            </dl>
            <div className="seq__why" title={run.reason}>{run.reason}</div>
            {run.parameters && Object.keys(run.parameters).length ? (
              <div className="seq__why">
                {Object.entries(run.parameters).map(([name, value]) => (
                  <code className="mono" key={name}>{name}={value} </code>
                ))}
              </div>
            ) : null}
          </div>

          {run.state === "UNKNOWN" ? (
            /* Not a synonym for failure, and the difference is the whole
               point: the playbook may have finished perfectly. What is true is
               that nobody saw it end, so the nodes are the only place the
               answer exists. */
            <div className="banner banner--concerning" role="alert">
              <Icon name="concerning" size={15} stroke={2} />
              <div>
                <b>TMS stopped watching before this finished.</b> {run.error} The
                playbook may well have completed — TMS cannot say either way, so
                it says neither. Check the nodes before running it again.
              </div>
            </div>
          ) : run.error ? (
            <div className="banner banner--bad" role="alert">
              <Icon name="bad" size={15} stroke={2} />
              <div>
                <b>{run.error}</b> Whatever the playbook had already done on the
                nodes has been done — it was not rolled back.
              </div>
            </div>
          ) : null}

          {/* Verbatim text from the playbook, rendered as a terminal — never
              as something TMS is asserting. */}
          <div className="console" ref={console_} tabIndex={0} role="log"
               aria-label="Playbook output">
            {run.output?.length ? (
              run.output.map((line, index) => (
                <div className={`console__line console__line--${line.level}`}
                     key={index}>
                  {line.message}
                </div>
              ))
            ) : (
              <div className="console__line console__line--info">
                Waiting for the first line of output…
              </div>
            )}
          </div>
        </section>
      </main>
    </>
  );
}

function JobState({ state }: { state: string }) {
  if (state === "RUNNING") {
    return (
      <span className="status status--running">
        <Icon name="clock" size={12} stroke={2} />Running
      </span>
    );
  }
  if (state === "SUCCEEDED") {
    return (
      <span className="status status--good">
        <Icon name="good" size={12} stroke={2} />Succeeded
      </span>
    );
  }
  if (state === "UNKNOWN") {
    return (
      <span className="status status--unknown">
        <Icon name="unknown" size={12} stroke={2} />Unknown
      </span>
    );
  }
  return (
    <span className="status status--bad">
      <Icon name="bad" size={12} stroke={2} />Failed
    </span>
  );
}
