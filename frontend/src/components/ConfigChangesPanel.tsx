import { useState } from "react";

import { ApiError, api } from "../api";
import { Icon } from "./Icon";
import { relativeTime } from "../format";
import { useApi } from "../useApi";

interface Entry {
  key: string;
  action: "set" | "unset";
  value: string | null;
}

interface Target {
  cluster: string;
  development: boolean;
  refusal: string | null;
}

interface Change {
  id: number;
  title: string;
  target_role: string;
  entries: Entry[];
  notes: string | null;
  summary: string;
  unknown_names: string[];
  advice: string[];
  targets: Target[];
  verified_on: string | null;
  verified_at: string | null;
  created_by: string;
  created_at: string;
}

interface Deployment {
  id: number;
  title: string;
  cluster: string;
  target_role: string;
  reason: string;
  actor: string;
  state: string;
  detail: string | null;
  started_at: string;
}

interface Page {
  cluster: string;
  changes: Change[];
  deployments: Deployment[];
  scanned: boolean;
  known_property_count: number;
  busy: boolean;
}

const STATE: Record<string, string> = {
  RUNNING: "status status--running",
  SUCCEEDED: "status status--good",
  FAILED: "status status--bad",
};

const ROLE_HINT: Record<string, string> = {
  all: "every node",
  coordinator: "the coordinator only",
  worker: "the workers only",
};

/**
 * Parse the editor's text into the edits the API takes.
 *
 * The text is a properties file, because that is what it becomes. A line
 * beginning with `-` removes the property instead of setting it — the one
 * piece of syntax, and it exists because "set this to empty" and "take this
 * line out" are different requests and a properties file cannot tell them
 * apart on its own.
 */
export function parseEdits(text: string): Entry[] {
  const entries: Entry[] = [];
  for (const raw of (text || "").split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("-")) {
      entries.push({ key: line.slice(1).trim(), action: "unset", value: null });
      continue;
    }
    const at = line.indexOf("=");
    // No `=` at all still goes to the server: it answers with the sentence
    // about what a property line looks like, and one voice saying that is
    // better than two.
    entries.push({
      key: (at === -1 ? line : line.slice(0, at)).trim(),
      action: "set",
      value: at === -1 ? "" : line.slice(at + 1).trim(),
    });
  }
  return entries;
}

function toText(entries: Entry[]): string {
  return entries
    .map((e) => (e.action === "unset" ? `-${e.key}` : `${e.key}=${e.value ?? ""}`))
    .join("\n");
}

/**
 * Changes to `config.properties`, and where each one may go.
 *
 * ⛔ Every verdict here comes from the server. Whether a change may reach a
 * cluster depends on that cluster's scan, the development list and the proof
 * mark; deciding it again in the browser would produce a second answer that
 * drifts from the one the deploy endpoint applies.
 *
 * Renders nothing when no deploy playbook is configured — the API says 503
 * with a name, and offering an editor that cannot deploy would be worse than
 * offering nothing.
 */
export function ConfigChangesPanel({ cluster, canManage }: {
  cluster: string;
  canManage: boolean;
}) {
  const path = `/clusters/${encodeURIComponent(cluster)}/config/changes`;
  const { data, error, reload } = useApi<Page>(cluster ? path : null, 5_000);
  const [notice, setNotice] = useState<{ good: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [deploying, setDeploying] = useState<string | null>(null);

  if (!data) {
    return error && error.status !== 503 ? (
      <div className="banner banner--bad" role="alert">
        <Icon name="bad" size={15} stroke={2} />
        <div>{error.message}</div>
      </div>
    ) : null;
  }

  async function run<T>(work: () => Promise<T>, good: string) {
    setBusy(true);
    setNotice(null);
    try {
      await work();
      setNotice({ good: true, text: good });
      reload();
      return true;
    } catch (caught) {
      setNotice({ good: false,
                  text: caught instanceof ApiError ? caught.message : String(caught) });
      return false;
    } finally {
      setBusy(false);
    }
  }

  function save(form: HTMLFormElement, changeId: number | null) {
    const fields = new FormData(form);
    const body = {
      title: fields.get("title"),
      target_role: fields.get("target_role"),
      entries: parseEdits(String(fields.get("edits") || "")),
      notes: fields.get("notes"),
      reason: fields.get("reason"),
    };
    void run(
      () => api.post(changeId === null
        ? "/config/changes" : `/config/changes/${changeId}`, body),
      changeId === null
        ? "Change created. It has to go to a development cluster first."
        : "Change saved. Editing cleared its development-cluster proof.",
    ).then((ok) => {
      if (!ok) return;
      if (changeId === null) form.reset();
      else setEditing(null);
    });
  }

  return (
    <section className="panel">
      <div className="panel__head">
        <span className="panel__title">Changes to config.properties</span>
        <span className="panel__sub">
          {data.known_property_count
            ? `${data.known_property_count} property names known to this cluster`
            : "no property names collected yet"}
        </span>
      </div>

      {/* ⛔ Said before anything else on this panel. An operator who reads
          "deployed" as "in effect" will wonder why nothing changed, and the
          honest answer belongs where the button is. */}
      <div className="panel__note">
        Deploying edits the file on the nodes and <strong>changes nothing
        else</strong>. Trino reads <code className="mono">config.properties</code>{" "}
        at startup, so the cluster keeps running the old values until the{" "}
        <a href="/restart">safe restart sequence</a> runs — that one stops
        traffic and drains first.
      </div>

      {!data.scanned || !data.known_property_count ? (
        <div className="banner banner--concerning" role="status">
          <Icon name="concerning" size={15} stroke={2} />
          <div>
            <b>Nothing can be deployed to {cluster} yet.</b> The typo check
            compares against the property names this cluster's Trino reported at
            startup, and {data.scanned
              ? "the scan collected none — check the log path in the scan playbook."
              : "this cluster has not been scanned."}{" "}
            An unrecognised name stops a Trino server from starting, so TMS
            deploys nothing rather than deploying unchecked.
          </div>
        </div>
      ) : null}

      {notice ? (
        <div className={`banner banner--${notice.good ? "good" : "bad"}`} role="alert">
          <Icon name={notice.good ? "good" : "bad"} size={15} stroke={2} />
          <div>{notice.text}</div>
        </div>
      ) : null}

      {data.changes.length ? (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Change</th>
                <th scope="col">Goes to</th>
                <th scope="col">Proved on</th>
                <th scope="col">Deploy to</th>
              </tr>
            </thead>
            <tbody>
              {data.changes.map((change) => (
                <ChangeRow key={change.id} change={change} canManage={canManage}
                           busy={busy} editing={editing === change.id}
                           deploying={deploying}
                           onEdit={() => setEditing(
                             editing === change.id ? null : change.id)}
                           onSave={save} onDeploying={setDeploying}
                           onDeploy={(target, reason) => void run(
                             () => api.post(
                               `/clusters/${encodeURIComponent(target)}/config/`
                               + `changes/${change.id}/deploy`, { reason }),
                             `Deploying to ${target}. The file will change; the `
                             + "cluster keeps running the old values until it is "
                             + "restarted.").then((ok) => ok && setDeploying(null))}
                           onDelete={(reason) => void run(
                             () => api.post(
                               `/config/changes/${change.id}/delete`, { reason }),
                             `${change.title} deleted. Its deployment history stays.`)}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty">
          <div className="empty__title">No changes yet</div>
          <div className="empty__desc">
            A change is a set of edits — set this property, remove that one —
            merged into the file on each node. Everything TMS did not touch is
            left exactly as it is.
          </div>
        </div>
      )}

      {canManage ? (
        <details className="rg-add">
          <summary>New change</summary>
          <ChangeForm busy={busy} onSubmit={(form) => save(form, null)} />
        </details>
      ) : null}

      {data.deployments.length ? (
        <>
          <div className="panel__head">
            <span className="panel__title">Deployments</span>
            <span className="panel__sub">what went where, and whether it worked</span>
          </div>
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Started</th>
                  <th scope="col">Change</th>
                  <th scope="col">Cluster</th>
                  <th scope="col">Nodes</th>
                  <th scope="col">By</th>
                  <th scope="col">Reason</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {data.deployments.map((run_) => (
                  <tr key={run_.id}>
                    <td className="num">{relativeTime(run_.started_at)}</td>
                    <td>{run_.title}</td>
                    <td><code className="mono">{run_.cluster}</code></td>
                    <td>{ROLE_HINT[run_.target_role] ?? run_.target_role}</td>
                    <td>{run_.actor}</td>
                    <td className="wrap">{run_.reason}</td>
                    <td>
                      <span className={STATE[run_.state] ?? "status status--unknown"}
                            title={run_.detail || undefined}>
                        {run_.state.toLowerCase()}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </section>
  );
}

function ChangeRow({ change, canManage, busy, editing, deploying,
                     onEdit, onSave, onDeploying, onDeploy, onDelete }: {
  change: Change;
  canManage: boolean;
  busy: boolean;
  editing: boolean;
  deploying: string | null;
  onEdit: () => void;
  onSave: (form: HTMLFormElement, changeId: number | null) => void;
  onDeploying: (key: string | null) => void;
  onDeploy: (cluster: string, reason: string) => void;
  onDelete: (reason: string) => void;
}) {
  return (
    <>
      <tr>
        <td>
          <div>{change.title}</div>
          <div className="muted mono">{change.summary}</div>
          {change.unknown_names.length ? (
            <div className="advice advice--bad">
              This cluster does not accept{" "}
              <code className="mono">{change.unknown_names.join(", ")}</code>.
            </div>
          ) : null}
          {change.advice.map((line) => (
            <div className="advice advice--concerning" key={line}>{line}</div>
          ))}
        </td>
        <td>{ROLE_HINT[change.target_role] ?? change.target_role}</td>
        <td>
          {change.verified_on ? (
            <span className="status status--good"
                  title={`Deployed there ${relativeTime(change.verified_at ?? "")}`}>
              {change.verified_on}
            </span>
          ) : (
            <span className="muted">nowhere yet</span>
          )}
        </td>
        <td className="row-actions">
          {canManage ? (
            <>
              {change.targets.map((target) => {
                const key = `${change.id}:${target.cluster}`;
                return deploying === key ? (
                  <form key={key} onSubmit={(event) => {
                    event.preventDefault();
                    onDeploy(target.cluster,
                             String(new FormData(event.currentTarget).get("reason")));
                  }}>
                    <input className="input input--sm" name="reason" required
                           autoFocus placeholder={`Why ${target.cluster}, now`} />
                    <button className="btn btn--sm btn--primary" type="submit"
                            disabled={busy}>Deploy</button>
                    <button className="btn btn--sm btn--ghost" type="button"
                            onClick={() => onDeploying(null)}>Cancel</button>
                  </form>
                ) : (
                  <button key={key} className="btn btn--sm" type="button"
                          /* ⛔ The reason it cannot go is on the button that
                             would send it, not in a note somewhere else. */
                          disabled={!!target.refusal || busy}
                          title={target.refusal ?? undefined}
                          onClick={() => onDeploying(key)}>
                    {target.development ? `${target.cluster} (dev)` : target.cluster}
                  </button>
                );
              })}
              <button className="btn btn--sm btn--ghost" type="button"
                      onClick={onEdit}>{editing ? "Close" : "Edit"}</button>
            </>
          ) : null}
        </td>
      </tr>
      {editing ? (
        <tr className="row-editing">
          <td colSpan={4}>
            <div className="panel__note">
              ⛔ Saving clears the development-cluster proof. A change edited
              after it was proved is a different change, and the test it passed
              never saw this version.
            </div>
            <ChangeForm busy={busy} change={change}
                        onSubmit={(form) => onSave(form, change.id)} />
            <form className="rg-add__grid" onSubmit={(event) => {
              event.preventDefault();
              onDelete(String(new FormData(event.currentTarget).get("reason")));
            }}>
              <div className="field">
                <label htmlFor={`del-${change.id}`}>Delete this change</label>
                <input className="input" id={`del-${change.id}`} name="reason"
                       required placeholder="Why it is no longer wanted" />
              </div>
              <button className="btn btn--danger" type="submit" disabled={busy}>
                Delete
              </button>
            </form>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function ChangeForm({ busy, change, onSubmit }: {
  busy: boolean;
  change?: Change;
  onSubmit: (form: HTMLFormElement) => void;
}) {
  const id = change ? `c${change.id}` : "new";
  return (
    <form className="rg-add__grid"
          onSubmit={(event) => { event.preventDefault(); onSubmit(event.currentTarget); }}>
      <div className="field">
        <label htmlFor={`title-${id}`}>
          Title <span className="req" aria-hidden="true">*</span>
        </label>
        <input className="input" id={`title-${id}`} name="title" required
               defaultValue={change?.title}
               placeholder="Raise the memory ceiling" />
      </div>
      <div className="field">
        <label htmlFor={`role-${id}`}>Deploy to</label>
        <select className="input" id={`role-${id}`} name="target_role"
                defaultValue={change?.target_role ?? "all"}>
          <option value="all">every node</option>
          <option value="coordinator">the coordinator only</option>
          <option value="worker">the workers only</option>
        </select>
      </div>
      <div className="field" style={{ gridColumn: "1 / -1" }}>
        <label htmlFor={`edits-${id}`}>
          Edits <span className="req" aria-hidden="true">*</span>
        </label>
        <textarea className="input" id={`edits-${id}`} name="edits" required
                  rows={5} spellCheck={false}
                  defaultValue={change ? toText(change.entries) : ""}
                  placeholder={"query.max-memory=900GB\n-node-scheduler.include-coordinator"} />
        <div className="field__hint">
          <Icon name="info" size={12} stroke={2} />
          <span>
            One property per line, as it will appear in the file. Start a line
            with <code className="mono">-</code> to remove that property
            instead. Lines TMS is not given are left untouched — a credential
            it never saw stays where it is.
          </span>
        </div>
      </div>
      <div className="field" style={{ gridColumn: "1 / -1" }}>
        <label htmlFor={`notes-${id}`}>Notes</label>
        <input className="input" id={`notes-${id}`} name="notes"
               defaultValue={change?.notes ?? ""} />
      </div>
      <div className="field" style={{ gridColumn: "1 / -1" }}>
        <label htmlFor={`reason-${id}`}>
          Reason <span className="req" aria-hidden="true">*</span>
        </label>
        <input className="input" id={`reason-${id}`} name="reason" required
               placeholder="Why this change is being made" />
      </div>
      <button className="btn btn--primary" type="submit" disabled={busy}>
        {change ? "Save change" : "Create change"}
      </button>
    </form>
  );
}
