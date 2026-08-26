import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api";
import { dataSize, duration } from "../format";
import { Icon } from "./Icon";

interface Killable {
  query_id: string;
  user: string | null;
  source: string | null;
  elapsed_ms: number | null;
  peak_user_memory_bytes: number | null;
}

/**
 * Killing a query is a ceremony, not a button.
 *
 * ⛔ The reason is required and it is not paperwork: it is delivered to the
 * person whose query is being killed, inside the error they see. The dialog
 * says so, because an operator typing "test" needs to know who reads it.
 *
 * Native <dialog>, matching the rest of the console: focus trapping, Esc, and
 * an inert background come free and are easy to get wrong by hand.
 */
export function KillDialog({ query, cluster, onClose, onKilled }: {
  query: Killable;
  cluster: string;
  onClose: () => void;
  onKilled: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(
        `/clusters/${encodeURIComponent(cluster)}/queries/${encodeURIComponent(query.query_id)}/kill`,
        { reason });
      onKilled();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
      setBusy(false);
    }
  }

  return (
    <dialog className="modal" ref={ref} onClose={onClose} onCancel={onClose}>
      <>
        <div className="modal__head">
          <Icon name="bad" size={18} stroke={2} />
          <h2 className="modal__title">Kill query</h2>
        </div>

        <form className="modal__body" onSubmit={submit}>
          {/* Repeats what is about to be destroyed. You always see it. */}
          <div className="target-card">
            <div className="target-card__row">
              <span className="target-card__key">Query</span>
              <span className="target-card__val">{query.query_id}</span>
            </div>
            <div className="target-card__row">
              <span className="target-card__key">User</span>
              <span className="target-card__val">
                {query.user || "—"}
                {query.source ? ` · ${query.source}` : ""}
              </span>
            </div>
            <div className="target-card__row">
              <span className="target-card__key">Cluster</span>
              <span className="target-card__val">
                {cluster} · running {duration(query.elapsed_ms)} ·{" "}
                {dataSize(query.peak_user_memory_bytes)} peak
              </span>
            </div>
          </div>

          {error ? (
            <div className="field__error" role="alert">
              {error}
            </div>
          ) : null}

          <div className="field">
            <label htmlFor="kill-reason">
              Reason <span className="req" aria-hidden="true">*</span>
            </label>
            <textarea id="kill-reason" required autoFocus value={reason}
                      placeholder="Why this query is being killed"
                      onChange={(e) => setReason(e.target.value)} />
            <div className="field__hint">
              <Icon name="concerning" size={12} stroke={2} />
              <span>
                This reason is delivered to <b>{query.user || "the query owner"}</b>{" "}
                in their query's error message, and recorded in the audit log.
              </span>
            </div>
          </div>

          <div className="modal__foot" style={{ padding: 0 }}>
            <button className="btn btn--ghost" type="button" onClick={onClose}>
              Cancel
            </button>
            <button className="btn btn--danger" type="submit"
                    disabled={busy || !reason.trim()}>
              <Icon name="bad" size={12} stroke={2} />
              {busy ? "Killing…" : "Kill query"}
            </button>
          </div>
        </form>
      </>
    </dialog>
  );
}
