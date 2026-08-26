import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { ApiError, api } from "../api";

/**
 * Sign in.
 *
 * Outside the Shell: there is no navigation to offer someone who is not
 * signed in, and every screen inside it would 401 on its first read.
 */
export function Login() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await api.post<{ must_change_password: boolean }>(
        "/login", { username, password });
      // ⛔ A temporary password must be replaced before it can be used to do
      // anything, otherwise "temporary" means "permanent in practice". The
      // server enforces it; this only saves the operator a 403.
      navigate(session.must_change_password ? "/account" : (params.get("next") || "/"),
               { replace: true });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
      setBusy(false);
    }
  }

  return (
    <main className="auth-screen">
      <div className="auth-card">
        <div className="brand">
          <div className="brand__glyph" aria-hidden="true">T</div>
          <div className="brand__name">TMS</div>
        </div>

        <h1>Sign in</h1>
        <p className="auth-card__sub">
          Trino Management Service — platform operators only.
        </p>

        <form onSubmit={submit}>
          {error ? <div className="field__error" role="alert">{error}</div> : null}

          <div className="field">
            <label htmlFor="username">Username</label>
            <input id="username" name="username" autoComplete="username" required autoFocus
                   value={username} onChange={(e) => setUsername(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input id="password" name="password" type="password" autoComplete="current-password"
                   required value={password}
                   onChange={(e) => setPassword(e.target.value)} />
          </div>
          <button className="btn btn--primary btn--block" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="auth-card__note">
          Temporary passwords must be changed at first sign-in before any other
          action.
        </p>
      </div>
    </main>
  );
}
