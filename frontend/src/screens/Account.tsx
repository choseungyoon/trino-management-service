import { useState } from "react";

import { ApiError, api } from "../api";
import { Icon } from "../components/Icon";
import { useApi } from "../useApi";

interface Me {
  user: string;
  roles: string[];
}

export function Account() {
  const { data: me } = useApi<Me>("/me");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ password_hash: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api.put<{ password_hash: string }>("/password", {
        current_password: current, new_password: next }));
      setCurrent("");
      setNext("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Account</span>
      </header>

      <main className="content" id="main">
        <section className="panel" style={{ maxWidth: 560 }}>
          <div className="panel__head">
            <span className="panel__title">Change password</span>
            <span className="panel__sub">
              {me?.user} · {me?.roles?.join(", ")}
            </span>
          </div>

          <form className="modal__body" onSubmit={submit}>
            {error ? <div className="field__error" role="alert">{error}</div> : null}

            <div className="field">
              <label htmlFor="current_password">Current password</label>
              <input id="current_password" type="password" required autoFocus
                     autoComplete="current-password" value={current}
                     onChange={(e) => setCurrent(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="new_password">New password</label>
              <input id="new_password" type="password" required
                     autoComplete="new-password" value={next}
                     onChange={(e) => setNext(e.target.value)} />
              <div className="field__hint">
                <Icon name="concerning" size={12} stroke={2} />
                <span>
                  At least 12 characters, mixing 3 of: lowercase, uppercase,
                  digits, symbols. This account can kill production queries.
                </span>
              </div>
            </div>

            <div className="modal__foot" style={{ padding: 0 }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                {busy ? "Changing…" : "Change password"}
              </button>
            </div>
          </form>
        </section>

        {result ? (
          /* The process cannot rewrite a gitignored config file it does not
             own, so the operator persists the hash. Saying so beats pretending
             otherwise. */
          <section className="panel" style={{ maxWidth: 760 }}>
            <div className="panel__head">
              <span className="panel__title">Persist this hash</span>
              <span className="panel__sub">
                the change is lost on restart until you do
              </span>
            </div>
            <div className="modal__body">
              <p className="muted">
                Replace{" "}
                <code className="mono">
                  portal.local_users.{me?.user}.password_hash
                </code>{" "}
                in <code className="mono">config/config.secret.yaml</code> with
                the value below and remove{" "}
                <code className="mono">must_change_password</code>.
              </p>
              <pre className="sql">{result.password_hash}</pre>
            </div>
          </section>
        ) : null}
      </main>
    </>
  );
}
