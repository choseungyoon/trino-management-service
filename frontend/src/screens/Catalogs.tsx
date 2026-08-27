import { useState } from "react";
import { Link } from "react-router";

import { ApiError, api } from "../api";
import { Icon } from "../components/Icon";
import { relativeTime } from "../format";
import { useApi } from "../useApi";

interface Target {
  cluster: string;
  development: boolean;
  refusal: string | null;
}

interface Catalog {
  id: number;
  name: string;
  connector: string;
  properties: Record<string, string>;
  notes: string | null;
  file: string;
  environment: string[];
  verified_on: string | null;
  verified_at: string | null;
  targets: Target[];
  created_by: string;
}

interface Deployment {
  id: number;
  catalog_name: string;
  cluster: string;
  action: string;
  state: string;
  detail: string | null;
  reason: string;
  actor: string;
  started_at: string;
}

interface Page {
  catalogs: Catalog[];
  deployments: Deployment[];
  clusters: { name: string; development: boolean }[];
  development_clusters: string[];
  can_edit: boolean;
  busy: Record<string, boolean>;
}

export function Catalogs() {
  const { data, error, reload } = useApi<Page>("/catalogs", 5_000);
  const [failure, setFailure] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

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
        <span className="topbar__title">Catalogs</span>
        <span className="spacer" />
        <Link className="btn btn--sm btn--ghost" to="/cluster-config">
          Configuration
        </Link>
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

        {/* ⛔ The two facts that shape everything on this screen, measured
            rather than assumed. */}
        <div className="banner" role="note">
          <Icon name="lock" size={15} stroke={2} />
          <div>
            <strong>
              A catalog Trino cannot load stops the whole server from starting
            </strong>{" "}
            — an unknown connector, an unknown property, a missing environment
            variable, all of them. TMS cannot check any of that in advance, so
            a catalog goes to{" "}
            <b>{data?.development_clusters.join(" or ") || "a development cluster"}</b>{" "}
            first and has to survive a restart there.{" "}
            <b>Deploying does not restart.</b> The file lands and nothing reads
            it until you run the safe restart sequence.
          </div>
        </div>

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">Catalogs</div>
            <div className="panel__sub">
              Drafts in TMS. What is on a cluster right now is on the{" "}
              <Link to="/cluster-config">Configuration</Link> screen.
            </div>
          </div>
          {data?.catalogs.length ? (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Connector</th>
                    <th scope="col">Proved on</th>
                    <th scope="col">Needs</th>
                    <th scope="col" />
                  </tr>
                </thead>
                <tbody>
                  {data.catalogs.map((entry) => (
                    <Row key={entry.id} entry={entry} page={data}
                         open={open === entry.id}
                         onToggle={() => setOpen(open === entry.id ? null : entry.id)}
                         guarded={guarded} />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <Icon name="queries" size={20} stroke={1.6} />
              <div className="empty__title">No catalogs yet</div>
              <div className="empty__desc">
                Nothing here is read from the clusters — these are drafts TMS
                deploys. A catalog already on a cluster is not listed until
                somebody writes it down here.
              </div>
            </div>
          )}
          {data?.can_edit ? <NewCatalog guarded={guarded} /> : null}
        </section>

        <section className="panel">
          <div className="panel__head">
            <div className="panel__title">Deployments</div>
            <div className="panel__sub">
              What was written where, and why. Kept even when it failed.
            </div>
          </div>
          {data?.deployments.length ? (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">When</th>
                    <th scope="col">Catalog</th>
                    <th scope="col">Cluster</th>
                    <th scope="col">Action</th>
                    <th scope="col">Result</th>
                    <th scope="col">Reason</th>
                    <th scope="col">By</th>
                  </tr>
                </thead>
                <tbody>
                  {data.deployments.map((row) => (
                    <tr key={row.id}>
                      <td className="num dim">{relativeTime(row.started_at)}</td>
                      <td className="mono">{row.catalog_name}</td>
                      <td className="mono">{row.cluster}</td>
                      <td>{row.action}</td>
                      <td>
                        {row.state === "SUCCEEDED" ? (
                          <span className="status status--good">
                            <Icon name="good" size={12} stroke={2} />written
                          </span>
                        ) : row.state === "RUNNING" ? (
                          <span className="status status--running">
                            <Icon name="clock" size={12} stroke={2} />writing
                          </span>
                        ) : (
                          <span className="status status--bad"
                                title={row.detail ?? undefined}>
                            <Icon name="bad" size={12} stroke={2} />failed
                          </span>
                        )}
                      </td>
                      <td className="wrap">{row.reason}</td>
                      <td>{row.actor}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="panel__note">Nothing has been deployed yet.</p>
          )}
        </section>
      </main>
    </>
  );
}

function Row({ entry, page, open, onToggle, guarded }: {
  entry: Catalog;
  page: Page;
  open: boolean;
  onToggle: () => void;
  guarded: (work: () => Promise<unknown>) => Promise<boolean>;
}) {
  const [target, setTarget] = useState<Target | null>(null);
  const [action, setAction] = useState<"deploy" | "remove">("deploy");
  const [reason, setReason] = useState("");

  return (
    <>
      <tr>
        <td className="mono">{entry.name}</td>
        <td className="mono">{entry.connector}</td>
        <td>
          {entry.verified_on ? (
            <span className="test-chip test-chip--good">
              {entry.verified_on}
              {entry.verified_at ? ` · ${relativeTime(entry.verified_at)}` : ""}
            </span>
          ) : (
            <span className="test-chip test-chip--concerning">not yet</span>
          )}
        </td>
        <td className="mono dim">
          {entry.environment.length ? entry.environment.join(", ") : "—"}
        </td>
        <td className="row-actions">
          <button className="btn btn--sm btn--ghost" type="button" onClick={onToggle}>
            {open ? "Hide" : "Show"}
          </button>
          {page.can_edit ? entry.targets.map((t) => (
            <button className={`btn btn--sm${t.refusal ? "" : " btn--primary"}`}
                    key={t.cluster} type="button"
                    disabled={!!t.refusal || page.busy[t.cluster]}
                    /* ⛔ The refusal is the tooltip. A greyed button that
                       cannot say why is a button people file tickets about. */
                    title={t.refusal ?? `Write ${entry.name} onto ${t.cluster}`}
                    onClick={() => { setTarget(t); setAction("deploy"); }}>
              → {t.cluster}
              {t.development ? " (dev)" : ""}
            </button>
          )) : null}
        </td>
      </tr>

      {open ? (
        <tr className="row-editing">
          <td colSpan={5}>
            <div className="stack">
              <div>
                <b>etc/catalog/{entry.name}.properties</b>
                <pre className="sql">{entry.file}</pre>
              </div>
              {entry.environment.length ? (
                <div className="field__hint">
                  <Icon name="concerning" size={12} stroke={2} />
                  <span>
                    ⛔ <b>{entry.environment.join(", ")}</b> must already exist
                    in the Trino process environment on every node this goes
                    to. A reference whose variable is missing stops the server
                    from starting, exactly like a bad connector name. TMS never
                    holds the value.
                  </span>
                </div>
              ) : null}
              {entry.notes ? <div className="dim">{entry.notes}</div> : null}
              {page.can_edit ? (
                <div className="row-actions">
                  <button className="btn btn--sm btn--danger" type="button"
                          onClick={() => {
                            setTarget(entry.targets[0] ?? null);
                            setAction("remove");
                          }}>
                    Remove from a cluster
                  </button>
                </div>
              ) : null}
            </div>
          </td>
        </tr>
      ) : null}

      {target ? (
        <tr className="row-editing">
          <td colSpan={5}>
            <div className="confirm">
              <div className="confirm__body">
                <b>
                  {action === "deploy" ? "Write" : "Remove"}{" "}
                  <code className="mono">
                    etc/catalog/{entry.name}.properties
                  </code>{" "}
                  {action === "deploy" ? "onto" : "from"}{" "}
                  <code className="mono">{target.cluster}</code>?
                </b>
                <div className="confirm__impact">
                  Every node in that cluster.{" "}
                  <b>Nothing restarts.</b> Trino reads catalogs only at
                  startup, so the file sits there unused until you run the safe
                  restart sequence — which is where it will fail, loudly, if it
                  is wrong.
                  {action === "remove" ? null : target.development ? (
                    <div>
                      This is the development cluster. A restart that comes
                      back healthy is what unlocks the other clusters.
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="confirm__actions">
                {action === "remove" ? (
                  <select className="input input--sm" value={target.cluster}
                          onChange={(e) => setTarget(
                            entry.targets.find((t) => t.cluster === e.target.value)
                            ?? target)}>
                    {entry.targets.map((t) => (
                      <option key={t.cluster} value={t.cluster}>{t.cluster}</option>
                    ))}
                  </select>
                ) : null}
                <input className="input input--sm" required aria-label="Reason"
                       placeholder="Why" value={reason}
                       onChange={(e) => setReason(e.target.value)} />
                <button className="btn btn--sm btn--danger" type="button"
                        disabled={!reason.trim()}
                        onClick={async () => {
                          const done = await guarded(() =>
                            api.post(`/catalogs/${entry.id}/deploy`, {
                              cluster: target.cluster, action, reason }));
                          if (done) { setTarget(null); setReason(""); }
                        }}>
                  {action === "deploy" ? "Write the file" : "Remove the file"}
                </button>
                <button className="btn btn--sm btn--ghost" type="button"
                        onClick={() => setTarget(null)}>Cancel</button>
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function NewCatalog({ guarded }: {
  guarded: (work: () => Promise<unknown>) => Promise<boolean>;
}) {
  const [form, setForm] = useState({
    name: "", connector: "", properties: "", notes: "", reason: "",
  });
  const [busy, setBusy] = useState(false);

  const edit = (field: keyof typeof form) =>
    (e: { target: { value: string } }) => setForm({ ...form, [field]: e.target.value });

  /** `key=value` per line — the same shape as the file it becomes. */
  function parsed(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const line of form.properties.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const at = trimmed.indexOf("=");
      if (at < 0) continue;
      out[trimmed.slice(0, at).trim()] = trimmed.slice(at + 1).trim();
    }
    return out;
  }

  return (
    <details className="rg-add">
      <summary>New catalog</summary>
      <div className="rg-add__grid">
        <label className="field">
          <span>
            Name <span className="dim">(becomes etc/catalog/&lt;name&gt;.properties)</span>
          </span>
          <input className="input mono" placeholder="pg_reporting" maxLength={60}
                 value={form.name} onChange={edit("name")} />
        </label>
        <label className="field">
          <span>
            Connector <span className="dim">(Trino writes these with underscores)</span>
          </span>
          <input className="input mono" placeholder="postgresql"
                 value={form.connector} onChange={edit("connector")} />
        </label>
        <label className="field">
          <span>
            Properties <span className="dim">(one <span className="mono">key=value</span> per line)</span>
          </span>
          <textarea className="input mono" rows={8}
                    placeholder={"connection-url=jdbc:postgresql://db:5432/reporting\n"
                                 + "connection-user=trino\n"
                                 + "connection-password=${ENV:PG_PASSWORD}"}
                    value={form.properties} onChange={edit("properties")} />
          <div className="field__hint">
            <Icon name="lock" size={12} stroke={2} />
            <span>
              ⛔ A password, secret or key must be written as{" "}
              <code className="mono">{"${ENV:VARIABLE}"}</code>. TMS refuses a
              literal — it does not store credentials, and Trino reads the
              variable from the node's own environment.
            </span>
          </div>
        </label>
        <label className="field">
          <span>Notes <span className="dim">(optional)</span></span>
          <input className="input" maxLength={500} value={form.notes}
                 onChange={edit("notes")} />
        </label>
        <label className="field">
          <span>Reason <span className="dim">(required)</span></span>
          <input className="input" required maxLength={500}
                 placeholder="Why this catalog is needed"
                 value={form.reason} onChange={edit("reason")} />
        </label>
        <button className="btn btn--primary" type="button"
                disabled={busy || !form.name.trim() || !form.connector.trim()
                          || !form.reason.trim()}
                onClick={async () => {
                  setBusy(true);
                  const made = await guarded(() => api.post("/catalogs", {
                    name: form.name, connector: form.connector,
                    properties: parsed(), notes: form.notes || null,
                    reason: form.reason,
                  }));
                  setBusy(false);
                  if (made) setForm({ name: "", connector: "", properties: "",
                                      notes: "", reason: "" });
                }}>
          {busy ? "Creating…" : "Create draft"}
        </button>
      </div>
    </details>
  );
}
