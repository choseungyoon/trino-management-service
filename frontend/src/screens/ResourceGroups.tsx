import { useState } from "react";
import { Link } from "react-router";

import { ApiError, api } from "../api";
import { ClusterTabs, useCluster } from "../components/ClusterTabs";
import { Icon } from "../components/Icon";
import { MANAGE_HEALTH, useCapability } from "../useCapability";
import { useApi } from "../useApi";

interface Group {
  row_id: string;
  id: string;
  name: string;
  depth: number;
  hard_concurrency_limit: number | null;
  max_queued: number | null;
  soft_memory_limit: string | null;
  scheduling_policy: string | null;
  jmx_export: boolean;
  status: string;
  running: number | null;
  queued: number | null;
}

interface Selector {
  id: string;
  priority: number;
  target: string | null;
  catch_all: boolean;
  matchers: Record<string, string>;
}

interface Tree {
  enabled: boolean;
  environment?: string;
  rows: Group[];
  unmanaged: { id: string }[];
  selectors: Selector[];
  has_catch_all?: boolean;
  live_available?: boolean;
  live_reason?: string | null;
  unavailable_reason?: string | null;
  advice?: string | null;
}

interface Impact {
  group: { row_id: string; id: string };
  groups: { id: string }[];
  selectors: { target: string; catch_all: boolean; matchers: Record<string, string> }[];
}

const POLICIES = ["fair", "weighted_fair", "weighted", "query_priority"];

const MATCHERS = ["user_regex", "source_regex", "query_type", "client_tags",
                  "original_user_regex", "authenticated_user_regex",
                  "user_group_regex"];

const STATUS: Record<string, { text: string; klass: string; title?: string }> = {
  running: { text: "running", klass: "status status--good" },
  idle: { text: "no traffic yet", klass: "status" },
  // Not a fault. Per-user groups deliberately skip jmxExport: one MBean per
  // user is 50 today and 50,000 at the target scale.
  hidden: { text: "not exported", klass: "status",
            title: "jmxExport is off, so TMS cannot see whether this group is running" },
};

export function ResourceGroups() {
  const [cluster, selectCluster, names] = useCluster();
  const canEdit = useCapability(MANAGE_HEALTH) === true;
  const { data, error, reload } = useApi<{ data: Tree }>(
    cluster ? `/clusters/${encodeURIComponent(cluster)}/resource-groups` : null);
  const [failure, setFailure] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  /** Every write lands here so refusals reach one place and the tree reloads. */
  async function write(work: () => Promise<unknown>): Promise<boolean> {
    setFailure(null);
    setWarnings([]);
    try {
      const result = await work() as { warnings?: { message: string }[] } | undefined;
      setWarnings((result?.warnings ?? []).map((w) => w.message));
      reload();
      return true;
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : String(caught));
      return false;
    }
  }

  const tree = data?.data;
  const base = `/clusters/${encodeURIComponent(cluster)}/resource-groups`;

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Resource Groups</span>
        <span className="spacer" />
        <ClusterTabs selected={cluster} names={names} onSelect={selectCluster} />
        <Link className="btn btn--sm btn--ghost"
              to={`/resource-groups/history?cluster=${encodeURIComponent(cluster)}`}>
          <Icon name="history" size={12} /> History
        </Link>
      </header>

      <main className="content" id="main">
        {error && !tree ? (
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

        {/* Accepted, with something worth knowing attached. Not an error - the
            change is already live. */}
        {warnings.map((warning) => (
          <div className="banner banner--concerning" role="status" key={warning}>
            <Icon name="concerning" size={15} stroke={2} />
            <div>{warning}</div>
          </div>
        ))}

        {tree && !tree.enabled ? (
          <div className="banner" role="status">
            <Icon name="info" size={15} stroke={2} />
            <div>
              <b>TMS is not reading Trino's resource group store.</b>{" "}
              {tree.unavailable_reason}
            </div>
          </div>
        ) : null}

        {tree?.enabled ? (
          <>
            {tree.unavailable_reason ? (
              <div className="banner banner--bad" role="alert">
                <Icon name="bad" size={15} stroke={2} />
                <div>
                  <b>{tree.unavailable_reason}</b>
                  {tree.advice ? <div>{tree.advice}</div> : null}
                </div>
              </div>
            ) : null}

            {/* The one invariant worth interrupting for. Trino 477 does not
                document what happens to a query matching no selector, so a
                tree without a catch-all is a configuration nobody has
                tested - including Trino's own authors. */}
            {tree.rows.length && !tree.has_catch_all ? (
              <div className="banner banner--bad" role="alert">
                <Icon name="bad" size={15} stroke={2} />
                <div>
                  <b>No catch-all selector.</b> Every selector here narrows what
                  it matches, so a query matching none of them has nowhere to
                  go — and Trino 477 does not document what it does in that
                  case. Add a selector with no conditions at the lowest
                  priority.
                </div>
              </div>
            ) : null}

            {!tree.live_available && tree.rows.length ? (
              <div className="banner" role="status">
                <Icon name="info" size={15} stroke={2} />
                <div>
                  {tree.live_reason} The <b>Running</b> column is blank for that
                  reason, not because the groups are idle.
                </div>
              </div>
            ) : null}

            {tree.unmanaged.length ? (
              /* Either someone edited the database by hand or node.environment
                 does not match this coordinator. The second is worse: it means
                 everything else on this page describes the wrong cluster. */
              <div className="banner banner--concerning" role="alert">
                <Icon name="concerning" size={15} stroke={2} />
                <div>
                  <b>
                    {tree.unmanaged.length} group(s) are running with no
                    configuration behind them:
                  </b>{" "}
                  {tree.unmanaged.map((row, index) => (
                    <span key={row.id}>
                      {index ? ", " : ""}
                      <code className="mono">{row.id}</code>
                    </span>
                  ))}
                  . Either the store was edited outside TMS, or this cluster's{" "}
                  <code className="mono">node.environment</code> is not{" "}
                  <code className="mono">{tree.environment}</code>.
                </div>
              </div>
            ) : null}

            <GroupsPanel tree={tree} base={base} canEdit={canEdit} write={write} />
            <SelectorsPanel tree={tree} base={base} canEdit={canEdit} write={write} />
          </>
        ) : null}
      </main>
    </>
  );
}

type Write = (work: () => Promise<unknown>) => Promise<boolean>;

function GroupsPanel({ tree, base, canEdit, write }: {
  tree: Tree; base: string; canEdit: boolean; write: Write;
}) {
  // ⛔ One row open at a time, and edit and delete share the slot. Two open
  // editors on one tree are two people about to overwrite each other.
  const [open, setOpen] = useState<{ id: string; mode: "edit" | "delete" } | null>(null);

  return (
    <section className="panel">
      <div className="panel__head">
        <span className="panel__title">Configured groups</span>
        <span className="spacer" />
        <span className="panel__sub">
          node.environment <code className="mono">{tree.environment}</code>
        </span>
      </div>

      {tree.rows.length ? (
        <>
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col" className="num">Concurrency</th>
                  <th scope="col" className="num">Max queued</th>
                  <th scope="col">Memory</th>
                  <th scope="col">Policy</th>
                  <th scope="col" className="num">Running</th>
                  <th scope="col" className="num">Queued</th>
                  <th scope="col">State</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {tree.rows.map((row) =>
                  open?.id === row.row_id && open.mode === "edit" ? (
                    <EditRow key={row.row_id} row={row} base={base} write={write}
                             onDone={() => setOpen(null)} />
                  ) : open?.id === row.row_id && open.mode === "delete" ? (
                    <DeleteRow key={row.row_id} row={row} base={base} write={write}
                               onDone={() => setOpen(null)} />
                  ) : (
                    <tr key={row.row_id} id={`rg-${row.row_id}`}>
                      <td style={{ paddingLeft: 12 + row.depth * 18 }}>
                        <code className="mono">{row.name}</code>
                      </td>
                      <td className="num">{row.hard_concurrency_limit ?? "—"}</td>
                      <td className="num">{row.max_queued ?? "—"}</td>
                      <td>{row.soft_memory_limit || "—"}</td>
                      <td>{row.scheduling_policy || "fair"}</td>
                      <td className="num">{row.running ?? "—"}</td>
                      <td className="num">{row.queued ?? "—"}</td>
                      <td>
                        <span className={STATUS[row.status]?.klass ?? "status"}
                              title={STATUS[row.status]?.title}>
                          {STATUS[row.status]?.text ?? "unknown"}
                        </span>
                      </td>
                      <td className="row-actions">
                        {canEdit ? (
                          <>
                            <button type="button" className="btn btn--sm btn--ghost"
                                    onClick={() => setOpen({ id: row.row_id, mode: "edit" })}>
                              Edit
                            </button>
                            <button type="button" className="btn btn--sm btn--ghost"
                                    onClick={() => setOpen({ id: row.row_id, mode: "delete" })}>
                              Delete
                            </button>
                          </>
                        ) : null}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          <p className="panel__note">
            This is the <b>configured</b> set, read from the store — unlike the
            Workload screen, a group appears here whether or not it has ever run
            a query. Changes made in the database reach the coordinators within{" "}
            <code className="mono">resource-groups.refresh-interval</code>, with
            no restart.
          </p>
        </>
      ) : (
        <div className="empty">
          <Icon name="info" size={22} stroke={1.6} />
          <div className="empty__title">No groups configured</div>
          <div className="empty__desc">
            The store holds no rows for{" "}
            <code className="mono">{tree.environment || "this cluster"}</code>.
          </div>
        </div>
      )}

      {canEdit ? <AddGroup tree={tree} base={base} write={write} /> : null}
    </section>
  );
}

function EditRow({ row, base, write, onDone }: {
  row: Group; base: string; write: Write; onDone: () => void;
}) {
  /* Not a modal: the surrounding tree is the context that makes a number mean
     something, and a dialog would cover it. */
  const [draft, setDraft] = useState({
    name: row.name,
    hard_concurrency_limit: row.hard_concurrency_limit?.toString() ?? "",
    max_queued: row.max_queued?.toString() ?? "",
    soft_memory_limit: row.soft_memory_limit ?? "",
    scheduling_policy: row.scheduling_policy || "fair",
    jmx_export: row.jmx_export,
  });
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    const saved = await write(() => api.patch(`${base}/${row.row_id}`, {
      reason,
      changes: {
        name: draft.name,
        // Blank means "no limit", which the store keeps as null. Sending ""
        // would make it a value.
        hard_concurrency_limit: draft.hard_concurrency_limit === ""
          ? null : Number(draft.hard_concurrency_limit),
        max_queued: draft.max_queued === "" ? null : Number(draft.max_queued),
        soft_memory_limit: draft.soft_memory_limit || null,
        scheduling_policy: draft.scheduling_policy,
        jmx_export: draft.jmx_export,
      },
    }));
    setBusy(false);
    if (saved) onDone();
  }

  return (
    <tr className="row-editing" id={`rg-${row.row_id}`}>
      <td style={{ paddingLeft: 12 + row.depth * 18 }}>
        <input className="input input--sm mono" maxLength={250} aria-label="Group name"
               value={draft.name}
               onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
      </td>
      <td>
        <input className="input input--sm num" type="number" min={1}
               aria-label="Concurrency limit" value={draft.hard_concurrency_limit}
               onChange={(e) => setDraft({ ...draft, hard_concurrency_limit: e.target.value })} />
      </td>
      <td>
        <input className="input input--sm num" type="number" min={1}
               aria-label="Max queued" value={draft.max_queued}
               onChange={(e) => setDraft({ ...draft, max_queued: e.target.value })} />
      </td>
      <td>
        <input className="input input--sm" maxLength={128} placeholder="80% or 100GB"
               aria-label="Memory limit" value={draft.soft_memory_limit}
               onChange={(e) => setDraft({ ...draft, soft_memory_limit: e.target.value })} />
      </td>
      <td>
        <select className="input input--sm" aria-label="Scheduling policy"
                value={draft.scheduling_policy}
                onChange={(e) => setDraft({ ...draft, scheduling_policy: e.target.value })}>
          {POLICIES.map((policy) => <option key={policy}>{policy}</option>)}
        </select>
      </td>
      <td colSpan={3}>
        <label className="check">
          <input type="checkbox" checked={draft.jmx_export}
                 onChange={(e) => setDraft({ ...draft, jmx_export: e.target.checked })} />
          visible on Workload
        </label>
      </td>
      <td className="row-actions">
        {/* Reason is required by the server too - this only saves a round trip. */}
        <input className="input input--sm" required aria-label="Reason"
               placeholder="Why is this changing?" value={reason}
               onChange={(e) => setReason(e.target.value)} />
        <button type="button" className="btn btn--sm btn--primary"
                disabled={busy || !reason.trim()} onClick={save}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" className="btn btn--sm btn--ghost" onClick={onDone}>
          Cancel
        </button>
      </td>
    </tr>
  );
}

function DeleteRow({ row, base, write, onDone }: {
  row: Group; base: string; write: Write; onDone: () => void;
}) {
  /* Both foreign keys in Trino's schema are ON DELETE CASCADE, so removing a
     group takes its whole subtree and every selector pointing into it. This
     lists what goes rather than counting it: a count is something people
     accept, a list is something they read. */
  const { data: impact } = useApi<Impact>(`${base}/${row.row_id}/impact`);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const others = impact?.groups.filter((g) => g.id !== row.id) ?? [];
  const alsoGoes = others.length > 0 || (impact?.selectors.length ?? 0) > 0;

  async function remove() {
    setBusy(true);
    const gone = await write(() =>
      api.del(`${base}/${row.row_id}?reason=${encodeURIComponent(reason)}`));
    setBusy(false);
    if (gone) onDone();
  }

  return (
    <tr className="row-deleting" id={`rg-${row.row_id}`}>
      <td colSpan={9}>
        <div className="confirm">
          <div className="confirm__body">
            <b>Delete <code className="mono">{row.id}</code>?</b>
            {alsoGoes ? (
              <div className="confirm__impact">
                This also deletes:
                <ul>
                  {others.map((group) => (
                    <li key={group.id}>
                      group <code className="mono">{group.id}</code>
                    </li>
                  ))}
                  {impact?.selectors.map((selector, index) => (
                    <li key={index}>
                      the selector sending{" "}
                      {selector.catch_all
                        ? <b>everything else</b>
                        : <Matchers matchers={selector.matchers} />}{" "}
                      to <code className="mono">{selector.target}</code>
                    </li>
                  ))}
                </ul>
                Ten seconds after this, the coordinators are running without them.
              </div>
            ) : null}
          </div>
          <div className="confirm__actions">
            <input className="input input--sm" required aria-label="Reason"
                   placeholder="Why is this being deleted?" value={reason}
                   onChange={(e) => setReason(e.target.value)} />
            <button type="button" className="btn btn--sm btn--danger"
                    disabled={busy || !reason.trim()} onClick={remove}>
              {busy ? "Deleting…" : "Delete"}
            </button>
            <button type="button" className="btn btn--sm btn--ghost" onClick={onDone}>
              Cancel
            </button>
          </div>
        </div>
      </td>
    </tr>
  );
}

function Matchers({ matchers }: { matchers: Record<string, string> }) {
  const entries = Object.entries(matchers);
  return (
    <>
      {entries.map(([key, value], index) => (
        <span key={key}>
          {index ? " & " : ""}
          <code className="mono">{key}={value}</code>
        </span>
      ))}
    </>
  );
}

function AddGroup({ tree, base, write }: { tree: Tree; base: string; write: Write }) {
  const [form, setForm] = useState({
    name: "", parent_row_id: "", hard_concurrency_limit: "10",
    max_queued: "100", soft_memory_limit: "", reason: "", jmx_export: true,
  });
  const [busy, setBusy] = useState(false);

  async function add() {
    setBusy(true);
    const added = await write(() => api.post(base, {
      name: form.name,
      parent_row_id: form.parent_row_id || null,
      reason: form.reason,
      values: {
        hard_concurrency_limit: Number(form.hard_concurrency_limit),
        max_queued: Number(form.max_queued),
        soft_memory_limit: form.soft_memory_limit || null,
        jmx_export: form.jmx_export,
      },
    }));
    setBusy(false);
    if (added) setForm({ ...form, name: "", reason: "" });
  }

  const edit = (field: keyof typeof form) =>
    (e: { target: { value: string } }) => setForm({ ...form, [field]: e.target.value });

  return (
    /* Outside the table on purpose. An add form squeezed into the tree's
       columns has to share widths with data it has nothing to do with, and the
       first attempt clipped a checkbox label down to a single letter. */
    <details className="rg-add">
      <summary>Add a group</summary>
      <div className="rg-add__grid">
        <label className="field">Name
          <input className="input mono" placeholder="reporting" maxLength={250}
                 value={form.name} onChange={edit("name")} />
        </label>
        <label className="field">Parent
          <select className="input" value={form.parent_row_id}
                  onChange={edit("parent_row_id")}>
            {/* Trino allows several roots; the shipped template has two. */}
            <option value="">(root group)</option>
            {tree.rows.map((row) => (
              <option key={row.row_id} value={row.row_id}>under {row.id}</option>
            ))}
          </select>
        </label>
        <label className="field">Concurrency
          <input className="input num" type="number" min={1}
                 value={form.hard_concurrency_limit}
                 onChange={edit("hard_concurrency_limit")} />
        </label>
        <label className="field">Max queued
          <input className="input num" type="number" min={1} value={form.max_queued}
                 onChange={edit("max_queued")} />
        </label>
        <label className="field">Memory
          <input className="input" placeholder="80% or 100GB" maxLength={128}
                 value={form.soft_memory_limit} onChange={edit("soft_memory_limit")} />
        </label>
        <label className="field">Reason
          <input className="input" required placeholder="Why is this being added?"
                 value={form.reason} onChange={edit("reason")} />
        </label>
        <label className="check">
          <input type="checkbox" checked={form.jmx_export}
                 onChange={(e) => setForm({ ...form, jmx_export: e.target.checked })} />
          visible on the Workload screen
        </label>
        <button type="button" className="btn btn--primary" onClick={add}
                disabled={busy || !form.name.trim() || !form.reason.trim()}>
          {busy ? "Adding…" : "Add group"}
        </button>
      </div>
    </details>
  );
}

function SelectorsPanel({ tree, base, canEdit, write }: {
  tree: Tree; base: string; canEdit: boolean; write: Write;
}) {
  const [confirming, setConfirming] = useState<string | null>(null);
  const catchAlls = tree.selectors.filter((s) => s.catch_all).length;

  return (
    <section className="panel">
      <div className="panel__head">
        <span className="panel__title">Selectors</span>
        <span className="spacer" />
        <span className="panel__sub">evaluated top to bottom</span>
      </div>

      {tree.selectors.length || canEdit ? (
        <>
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col" className="num">Priority</th>
                  <th scope="col">Matches</th>
                  <th scope="col">Sends to</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {tree.selectors.map((selector) =>
                  confirming === selector.id ? (
                    <DeleteSelector key={selector.id} selector={selector} base={base}
                                    write={write} onDone={() => setConfirming(null)} />
                  ) : (
                    <tr key={selector.id}>
                      <td className="num">{selector.priority}</td>
                      <td>
                        {selector.catch_all
                          ? <b>everything else</b>
                          : <Matchers matchers={selector.matchers} />}
                      </td>
                      <td>
                        <code className="mono">
                          {selector.target || "(unknown group)"}
                        </code>
                      </td>
                      <td className="row-actions">
                        {canEdit ? (
                          selector.catch_all && catchAlls === 1 ? (
                            /* No delete button at all on the last catch-all.
                               Trino 477 does not document what happens to a
                               query matching no selector, so the state is
                               unreachable rather than merely discouraged. The
                               server refuses it too; this stops the offer
                               being made. */
                            <span className="hint"
                                  title="Removing the last catch-all would leave unmatched queries with nowhere to go">
                              required
                            </span>
                          ) : (
                            <button type="button" className="btn btn--sm btn--ghost"
                                    onClick={() => setConfirming(selector.id)}>
                              Delete
                            </button>
                          )
                        ) : null}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          <p className="panel__note">
            Order is meaning: Trino takes the first selector that matches, in
            descending priority. <code className="mono">user_group_regex</code>{" "}
            only ever matches when a group provider is configured (
            <code className="mono">etc/group-provider.properties</code>) —
            without one it is a dead rule.
          </p>
          {canEdit ? <AddSelector tree={tree} base={base} write={write} /> : null}
        </>
      ) : (
        <div className="empty">
          <Icon name="info" size={22} stroke={1.6} />
          <div className="empty__title">No selectors</div>
          <div className="empty__desc">Nothing routes queries into these groups.</div>
        </div>
      )}
    </section>
  );
}

function DeleteSelector({ selector, base, write, onDone }: {
  selector: Selector; base: string; write: Write; onDone: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <tr className="row-deleting">
      <td colSpan={4}>
        <div className="confirm">
          <div className="confirm__body">
            <b>Delete this selector?</b> Queries it was placing go to whichever
            rule matches next — for most, that is the catch-all.
          </div>
          <div className="confirm__actions">
            <input className="input input--sm" required aria-label="Reason"
                   placeholder="Why is this being deleted?" value={reason}
                   onChange={(e) => setReason(e.target.value)} />
            <button type="button" className="btn btn--sm btn--danger"
                    disabled={busy || !reason.trim()}
                    onClick={async () => {
                      setBusy(true);
                      const gone = await write(() => api.del(
                        `${base}/selectors/${selector.id}?reason=${encodeURIComponent(reason)}`));
                      setBusy(false);
                      if (gone) onDone();
                    }}>
              {busy ? "Deleting…" : "Delete"}
            </button>
            <button type="button" className="btn btn--sm btn--ghost" onClick={onDone}>
              Cancel
            </button>
          </div>
        </div>
      </td>
    </tr>
  );
}

function AddSelector({ tree, base, write }: { tree: Tree; base: string; write: Write }) {
  const [form, setForm] = useState({
    priority: "15", matcher: MATCHERS[0], pattern: "",
    target_row_id: "", reason: "",
  });
  const [busy, setBusy] = useState(false);

  const edit = (field: keyof typeof form) =>
    (e: { target: { value: string } }) => setForm({ ...form, [field]: e.target.value });

  return (
    <details className="rg-add">
      <summary>Add a selector</summary>
      <div className="rg-add__grid">
        <label className="field">Priority
          <input className="input num" type="number" value={form.priority}
                 onChange={edit("priority")} />
        </label>
        <label className="field">Match on
          <select className="input" value={form.matcher} onChange={edit("matcher")}>
            {MATCHERS.map((name) => <option key={name}>{name}</option>)}
          </select>
        </label>
        <label className="field">Pattern
          <input className="input mono" placeholder="^datalake\.admin$"
                 value={form.pattern} onChange={edit("pattern")} />
        </label>
        <label className="field">Sends to
          <select className="input" value={form.target_row_id}
                  onChange={edit("target_row_id")}>
            <option value="">choose a group…</option>
            {tree.rows.map((row) => (
              <option key={row.row_id} value={row.row_id}>{row.id}</option>
            ))}
          </select>
        </label>
        <label className="field">Reason
          <input className="input" required placeholder="Why is this being added?"
                 value={form.reason} onChange={edit("reason")} />
        </label>
        <button type="button" className="btn btn--primary"
                disabled={busy || !form.target_row_id || !form.reason.trim()}
                onClick={async () => {
                  setBusy(true);
                  const added = await write(() => api.post(`${base}/selectors`, {
                    target_row_id: form.target_row_id,
                    priority: Number(form.priority),
                    // Empty pattern means no condition, which is what makes a
                    // selector the catch-all.
                    matchers: form.pattern ? { [form.matcher]: form.pattern } : {},
                    reason: form.reason,
                  }));
                  setBusy(false);
                  if (added) setForm({ ...form, pattern: "", reason: "" });
                }}>
          {busy ? "Adding…" : "Add selector"}
        </button>
      </div>
    </details>
  );
}
