import { useState } from "react";

import { ApiError, api } from "../api";
import { Icon } from "./Icon";
import { relativeTime } from "../format";
import { useApi } from "../useApi";

interface ListedNode {
  host: string;
  address: string;
  role: string;
  source: string;
  reason: string | null;
  added_by: string;
  version: string | null;
  last_seen_at: string | null;
  answering: boolean;
  hand_entered: boolean;
}

interface NodeList {
  cluster: string;
  nodes: ListedNode[];
  counts: { total: number; workers: number; silent: number };
  can_scan: boolean;
  inventory_path: string | null;
}

/**
 * The cluster's node list, which TMS owns rather than reads.
 *
 * ⛔ Nothing here decides what a node's absence means. A node the coordinator
 * has stopped reporting is either decommissioned or down, and only a person
 * knows which — so a scan never removes one, and this panel's job is to make
 * the ones nobody has decided about impossible to miss.
 *
 * Renders nothing when the deployment still keeps its node list in hand-edited
 * inventory files: the API says 503 with a name, and a panel offering to edit
 * a list nobody reads would be worse than no panel.
 */
export function NodeListPanel({ cluster, canManage }: {
  cluster: string;
  canManage: boolean;
}) {
  const path = `/clusters/${encodeURIComponent(cluster)}/nodes`;
  const { data, error, reload } = useApi<NodeList>(cluster ? path : null);
  const [notice, setNotice] = useState<{ good: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);

  if (!data) {
    // 503 means the feature is off, which is not an error worth a banner.
    return error && error.status !== 503 ? (
      <div className="banner banner--bad" role="alert">
        <Icon name="bad" size={15} stroke={2} />
        <div>{error.message}</div>
      </div>
    ) : null;
  }

  async function run<T>(work: () => Promise<T>, good: (result: T) => string) {
    setBusy(true);
    setNotice(null);
    try {
      setNotice({ good: true, text: good(await work()) });
      reload();
    } catch (caught) {
      setNotice({ good: false,
                  text: caught instanceof ApiError ? caught.message : String(caught) });
    } finally {
      setBusy(false);
    }
  }

  function scan() {
    void run(
      () => api.post<{ added: string[]; refreshed: number; not_answering: string[] }>(
        `${path}/scan`),
      (result) => {
        const parts = [];
        if (result.added.length) parts.push(`added ${result.added.join(", ")}`);
        parts.push(`${result.refreshed} still answering`);
        // Named rather than counted: these are the ones somebody has to
        // decide about, and a number does not tell you which.
        if (result.not_answering.length) {
          parts.push(`no answer from ${result.not_answering.join(", ")} — still `
                     + `receiving configuration until removed`);
        }
        return parts.join(" · ");
      });
  }

  function add(form: HTMLFormElement) {
    const fields = new FormData(form);
    void run(
      () => api.post(path, {
        host: fields.get("host"), address: fields.get("address"),
        role: fields.get("role"), reason: fields.get("reason"),
      }),
      () => `${fields.get("host")} added.`).then(() => form.reset());
  }

  return (
    <div className="panel">
      <div className="panel__head">
        <span className="panel__title">Node list</span>
        <span className="panel__sub">
          {data.counts.total} node{data.counts.total === 1 ? "" : "s"} ·{" "}
          {data.counts.workers} worker{data.counts.workers === 1 ? "" : "s"}
        </span>
        <span className="spacer" />
        {canManage && data.can_scan ? (
          <button className="btn btn--sm" type="button" onClick={scan} disabled={busy}>
            <Icon name="clock" size={12} stroke={2} />
            {busy ? "Scanning…" : "Scan the coordinator"}
          </button>
        ) : null}
      </div>

      <div className="panel__note">
        Restarts and configuration deployments target <b>every</b> row here,
        including the ones that are not answering — a node that is down still
        has to come back with the same configuration as its siblings.
        {data.inventory_path ? (
          <> TMS writes this list to <code className="mono">{data.inventory_path}</code>.</>
        ) : null}
      </div>

      {notice ? (
        <div className={`banner banner--${notice.good ? "good" : "bad"}`} role="alert">
          <Icon name={notice.good ? "good" : "bad"} size={15} stroke={2} />
          <div>{notice.text}</div>
        </div>
      ) : null}

      {data.counts.silent ? (
        <div className="banner banner--concerning" role="status">
          <Icon name="concerning" size={15} stroke={2} />
          <div>
            <b>
              {data.counts.silent} node{data.counts.silent === 1 ? "" : "s"} the
              coordinator is not reporting.
            </b>{" "}
            Removing one means it stops receiving configuration. Leave it if it
            is coming back.
          </div>
        </div>
      ) : null}

      {data.nodes.length ? (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Host</th>
                <th scope="col">Address</th>
                <th scope="col">Role</th>
                <th scope="col">Known from</th>
                <th scope="col">Last seen</th>
                {canManage ? <th scope="col" /> : null}
              </tr>
            </thead>
            <tbody>
              {data.nodes.map((node) => (
                <tr key={node.host}
                    className={removing === node.host ? "row-deleting" : undefined}>
                  <td><code className="mono">{node.host}</code></td>
                  <td className="mono">
                    {node.address === node.host ? <span className="muted">—</span>
                                                : node.address}
                  </td>
                  <td>{node.role}</td>
                  <td>
                    {node.hand_entered ? (
                      <span className="tag" title={node.reason || undefined}>
                        added by {node.added_by}
                      </span>
                    ) : (
                      <span className="muted">discovered</span>
                    )}
                  </td>
                  <td>
                    {node.answering ? (
                      <span className="status status--good">answering</span>
                    ) : (
                      <span className="status status--concerning"
                            title="Still receives every deployment.">
                        {node.last_seen_at
                          ? `no answer · last ${relativeTime(node.last_seen_at)}`
                          : "never seen"}
                      </span>
                    )}
                  </td>
                  {canManage ? (
                    <td className="row-actions">
                      {removing === node.host ? (
                        <form onSubmit={(event) => {
                          event.preventDefault();
                          const reason = new FormData(event.currentTarget).get("reason");
                          void run(
                            () => api.post(
                              `${path}/${encodeURIComponent(node.host)}/remove`,
                              { reason }),
                            () => `${node.host} removed. It will not receive `
                                  + `configuration again unless a scan finds it.`)
                            .then(() => setRemoving(null));
                        }}>
                          <input className="input input--sm" name="reason" required
                                 autoFocus placeholder="Why it is gone for good" />
                          <button className="btn btn--sm btn--danger" type="submit"
                                  disabled={busy}>Remove</button>
                          <button className="btn btn--sm btn--ghost" type="button"
                                  onClick={() => setRemoving(null)}>Cancel</button>
                        </form>
                      ) : (
                        <button className="btn btn--sm btn--ghost" type="button"
                                onClick={() => setRemoving(node.host)}>Remove</button>
                      )}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty">
          <div className="empty__title">No nodes listed yet</div>
          <div className="empty__desc">
            {data.can_scan
              ? "Scan the coordinator to fill this in — it reports every node "
                + "that has joined."
              : "TMS cannot query this cluster, so nodes have to be added by hand."}
          </div>
        </div>
      )}

      {canManage ? (
        <details className="rg-add">
          <summary>Add a node the coordinator cannot see</summary>
          {/* Above the fields rather than under one of them: a hint inside the
              grid makes that column taller and knocks the labels out of line. */}
          <div className="panel__note">
            For a node that is down — discovery cannot see it, and it still has
            to receive configuration. Leave the address blank when the host name
            is what SSH connects to.
          </div>
          <form className="rg-add__grid"
                onSubmit={(event) => { event.preventDefault(); add(event.currentTarget); }}>
            <div className="field">
              <label htmlFor="node-host">
                Host <span className="req" aria-hidden="true">*</span>
              </label>
              <input className="input" id="node-host" name="host" required
                     placeholder="trino-w9" />
            </div>
            <div className="field">
              <label htmlFor="node-address">Address</label>
              <input className="input" id="node-address" name="address"
                     placeholder="10.0.0.19" />
            </div>
            <div className="field">
              <label htmlFor="node-role">Role</label>
              <select className="input" id="node-role" name="role"
                      defaultValue="worker">
                <option value="worker">worker</option>
                <option value="coordinator">coordinator</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="node-reason">
                Reason <span className="req" aria-hidden="true">*</span>
              </label>
              <input className="input" id="node-reason" name="reason" required
                     placeholder="Down for a disk swap; still needs config" />
            </div>
            <button className="btn btn--primary" type="submit" disabled={busy}>
              Add node
            </button>
          </form>
        </details>
      ) : null}
    </div>
  );
}
