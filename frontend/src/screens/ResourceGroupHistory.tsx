import { useState } from "react";
import { Link } from "react-router";

import { ApiError, api } from "../api";
import { ClusterTabs, useCluster } from "../components/ClusterTabs";
import { Icon } from "../components/Icon";
import { relativeTime } from "../format";
import { MANAGE_HEALTH, useCapability } from "../useCapability";
import { useApi } from "../useApi";

interface Revision {
  id: string;
  occurred_at: string;
  actor: string;
  kind: string;
  target: string;
  reason: string;
}

export function ResourceGroupHistory() {
  const [cluster, selectCluster, names] = useCluster();
  const canEdit = useCapability(MANAGE_HEALTH) === true;
  const base = `/clusters/${encodeURIComponent(cluster)}/resource-groups`;
  const { data, error, reload } = useApi<{ revisions: Revision[] }>(
    cluster ? `${base}/revisions` : null);
  const [failure, setFailure] = useState<string | null>(null);

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Resource Group History</span>
        <span className="spacer" />
        <ClusterTabs selected={cluster} names={names} onSelect={selectCluster} />
        <Link className="btn btn--sm btn--ghost"
              to={`/resource-groups?cluster=${encodeURIComponent(cluster)}`}>
          Back to groups
        </Link>
      </header>

      <main className="content" id="main">
        {error ? (
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

        <div className="banner" role="status">
          <Icon name="info" size={15} stroke={2} />
          <div>
            Trino's own tables keep no history and have nowhere to record why a
            value was chosen, so TMS keeps this alongside them. Reverting{" "}
            <b>appends</b> — it never removes what came before.
          </div>
        </div>

        <section className="panel">
          <div className="panel__head">
            <span className="panel__title">Changes</span>
          </div>
          {data?.revisions.length ? (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">When</th>
                    <th scope="col">Who</th>
                    <th scope="col">What</th>
                    <th scope="col">Reason</th>
                    <th scope="col" />
                  </tr>
                </thead>
                <tbody>
                  {data.revisions.map((revision) => (
                    <tr key={revision.id}>
                      <td title={new Date(revision.occurred_at).toLocaleString()}>
                        {relativeTime(revision.occurred_at)}
                      </td>
                      <td><code className="mono">{revision.actor}</code></td>
                      <td>
                        <span className="status">
                          {revision.kind.replace(/_/g, " ")}
                        </span>{" "}
                        <code className="mono">{revision.target}</code>
                      </td>
                      <td className="wrap">{revision.reason}</td>
                      <td className="row-actions">
                        {canEdit ? (
                          <Revert base={base} revision={revision}
                                  onDone={reload} onFail={setFailure} />
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <Icon name="info" size={22} stroke={1.6} />
              <div className="empty__title">No changes recorded</div>
              <div className="empty__desc">
                Nothing has been changed through TMS for this cluster. Rows
                loaded by the setup script are not revisions — they had no TMS
                action behind them.
              </div>
            </div>
          )}
        </section>
      </main>
    </>
  );
}

function Revert({ base, revision, onDone, onFail }: {
  base: string;
  revision: Revision;
  onDone: () => void;
  onFail: (message: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function revert() {
    setBusy(true);
    try {
      await api.post(`${base}/revisions/${revision.id}/revert`, { reason });
      setReason("");
      setConfirming(false);
      onDone();
    } catch (caught) {
      onFail(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <input className="input input--sm" required aria-label="Reason"
             placeholder="Why revert?" value={reason}
             onChange={(e) => setReason(e.target.value)} />
      {/* Restores the whole environment, not one field: partial undo multiplies
          the states that have to be validated, and every one of them is a way
          to leave the tree in a shape nobody tested. */}
      {confirming ? (
        <>
          <span className="hint">Restore the whole tree to how it was before this change?</span>
          <button type="button" className="btn btn--sm btn--danger"
                  disabled={busy} onClick={revert}>
            {busy ? "Reverting…" : "Yes, revert"}
          </button>
          <button type="button" className="btn btn--sm btn--ghost"
                  onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </>
      ) : (
        <button type="button" className="btn btn--sm btn--danger"
                disabled={!reason.trim()} onClick={() => setConfirming(true)}>
          Revert to before
        </button>
      )}
    </>
  );
}
