import { useState } from "react";

import { ApiError, api } from "../api";
import { ClusterTabs, useCluster } from "../components/ClusterTabs";
import { Icon } from "../components/Icon";
import { relativeTime } from "../format";
import { useApi } from "../useApi";

interface FileEntry {
  present: boolean;
  sha256: string | null;
  content_collected: boolean;
  properties?: Record<string, string>;
}

interface Node {
  host: string;
  role: string;
  reachable: boolean;
  error: string | null;
  files: Record<string, FileEntry>;
  properties: Record<string, string>;
  valid_names: string[];
}

interface Finding {
  kind: string;
  role: string;
  subject: string;
  detail: string;
  hosts: Record<string, string>;
  expected: boolean;
}

interface Page {
  cluster: string;
  scanned: boolean;
  scanning: boolean;
  development: boolean;
  can_scan: boolean;
  nodes: Node[];
  findings: Finding[];
  agree: boolean;
  valid_names: string[];
  roles: string[];
  collected_at?: string;
  error?: string | null;
}

const KIND_LABEL: Record<string, string> = {
  value_differs: "Different value",
  file_differs: "Different file",
  missing_file: "File missing",
  unreachable: "Not read",
};

export function ClusterConfig() {
  const [cluster, selectCluster, names] = useCluster();
  // Polls while a scan runs; the scan itself is a fleet-wide SSH fan-out and
  // takes longer than a request should.
  const { data, error, reload } = useApi<Page>(
    cluster ? `/clusters/${encodeURIComponent(cluster)}/config` : null,
    5_000);
  const [failure, setFailure] = useState<string | null>(null);
  const [openNode, setOpenNode] = useState<string | null>(null);

  async function scan() {
    setFailure(null);
    try {
      await api.post(`/clusters/${encodeURIComponent(cluster)}/config/scan`);
      reload();
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : String(caught));
    }
  }

  const real = (data?.findings ?? []).filter((f) => !f.expected);
  const expected = (data?.findings ?? []).filter((f) => f.expected);

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Configuration</span>
        <ClusterTabs selected={cluster} names={names} onSelect={selectCluster} />
        <span className="spacer" />
        {data?.collected_at ? (
          <span className="dim">read {relativeTime(data.collected_at)}</span>
        ) : null}
        {data?.can_scan ? (
          <button className="btn btn--sm" type="button" disabled={data.scanning}
                  onClick={scan}>
            {data.scanning ? "Reading…" : "Read the nodes"}
          </button>
        ) : null}
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

        {/* ⛔ Said on the screen, because it is the property that makes this
            safe to press. */}
        <div className="banner" role="note">
          <Icon name="lock" size={15} stroke={2} />
          <div>
            <strong>This screen only reads.</strong> "Read the nodes" runs a
            playbook that opens an SSH connection to every node and copies
            nothing back except what is listed below.{" "}
            <b>Catalog files are compared by checksum, never by content</b> —
            they hold connection passwords. Values whose name reads like a
            credential are dropped before they reach TMS.
          </div>
        </div>

        {data?.development ? (
          <div className="banner" role="status">
            <Icon name="info" size={15} stroke={2} />
            <div>
              <b>{data.cluster} is marked as a development cluster.</b> A node
              that does not answer is not reported as drift here — its worker
              count changes with whatever is being tested.
            </div>
          </div>
        ) : null}

        {data?.error ? (
          <div className="banner banner--concerning" role="alert">
            <Icon name="concerning" size={15} stroke={2} />
            <div>
              <b>The last scan did not finish cleanly.</b> {data.error} What is
              below is what it managed to read.
            </div>
          </div>
        ) : null}

        {data && !data.scanned ? (
          <section className="panel">
            <div className="empty">
              <Icon name="info" size={20} stroke={1.6} />
              <div className="empty__title">Nothing read yet</div>
              <div className="empty__desc">
                TMS does not poll this — a scan connects to every node, and the
                answer only changes when somebody changes it. Press{" "}
                <b>Read the nodes</b> when you want to know.
              </div>
            </div>
          </section>
        ) : null}

        {data?.scanned ? (
          <>
            <section className="panel">
              <div className="panel__head">
                <div className="panel__title">
                  {real.length ? `${real.length} disagreement${
                    real.length === 1 ? "" : "s"}` : "The nodes agree"}
                </div>
                <div className="panel__sub">
                  {/* ⛔ The judgement worth stating: within a role, never
                      across. A coordinator and a worker are supposed to
                      differ. */}
                  Compared within each role — a coordinator and a worker are
                  supposed to differ
                </div>
              </div>
              {real.length ? (
                <Findings rows={real} />
              ) : (
                <p className="panel__note">
                  Every node of the same role is running the same files and the
                  same values.
                </p>
              )}
            </section>

            {expected.length ? (
              <section className="panel">
                <div className="panel__head">
                  <div className="panel__title">Expected differences</div>
                  <div className="panel__sub">
                    <code className="mono">node.properties</code> holds{" "}
                    <code className="mono">node.id</code>, which must be unique.
                    Listed so you can see which nodes have it, not because
                    anything is wrong.
                  </div>
                </div>
                <Findings rows={expected} />
              </section>
            ) : null}

            <section className="panel">
              <div className="panel__head">
                <div className="panel__title">Nodes</div>
                <div className="panel__sub">
                  {data.nodes.length} scanned · roles {data.roles.join(", ")}
                </div>
              </div>
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Node</th>
                      <th scope="col">Role</th>
                      <th scope="col" className="num">Files</th>
                      <th scope="col" className="num">Settings</th>
                      <th scope="col" className="num">Known properties</th>
                      <th scope="col" />
                    </tr>
                  </thead>
                  <tbody>
                    {data.nodes.map((node) => (
                      <NodeRows key={node.host} node={node}
                                open={openNode === node.host}
                                onToggle={() => setOpenNode(
                                  openNode === node.host ? null : node.host)} />
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="panel__note">
                {/* ⛔ Where the deploy step's typo check will come from. */}
                <strong>Known properties</strong> is what that node's Trino
                accepts, taken from its own startup log. TMS keeps no list of
                its own: an unrecognised name stops a Trino server booting, and
                a hand-written table would be a second opinion about a build TMS
                has never seen.{" "}
                {data.valid_names.length
                  ? `${data.valid_names.length} names are known to every node scanned.`
                  : "No names were collected — check that the playbook can read the startup log."}
              </p>
            </section>
          </>
        ) : null}
      </main>
    </>
  );
}

function Findings({ rows }: { rows: Finding[] }) {
  return (
    <div className="table-scroll">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">What</th>
            <th scope="col">Role</th>
            <th scope="col">Subject</th>
            <th scope="col">Per node</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((finding) => (
            <tr key={`${finding.role}-${finding.kind}-${finding.subject}`}>
              <td>
                <span className={`test-chip test-chip--${
                  finding.expected ? "good" : "concerning"}`}>
                  {KIND_LABEL[finding.kind] ?? finding.kind}
                </span>
              </td>
              <td>{finding.role}</td>
              <td className="mono">{finding.subject}</td>
              <td className="wrap">
                <div className="dim">{finding.detail}</div>
                {Object.entries(finding.hosts).map(([host, value]) => (
                  <div key={host}>
                    <span className="mono">{host}</span>{" "}
                    <span className="dim mono">{value}</span>
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NodeRows({ node, open, onToggle }: {
  node: Node;
  open: boolean;
  onToggle: () => void;
}) {
  const files = Object.entries(node.files);
  return (
    <>
      <tr>
        <td className="mono">{node.host}</td>
        <td><span className="tag">{node.role}</span></td>
        <td className="num">{files.filter(([, f]) => f.present).length}</td>
        <td className="num">{Object.keys(node.properties).length}</td>
        <td className="num">{node.valid_names.length || "—"}</td>
        <td className="row-actions">
          {node.reachable && !node.error ? (
            <button className="btn btn--sm btn--ghost" type="button"
                    onClick={onToggle}>
              {open ? "Hide" : "Show"}
            </button>
          ) : (
            <span className="status status--unknown" title={node.error ?? undefined}>
              <Icon name="unknown" size={12} stroke={2} />not read
            </span>
          )}
        </td>
      </tr>
      {open ? (
        <tr className="row-editing">
          <td colSpan={6}>
            <div className="stack">
              <div>
                <b>Files</b>
                {files.map(([path, entry]) => (
                  <div key={path}>
                    <code className="mono">{path}</code>{" "}
                    {entry.present ? (
                      <span className="dim mono">
                        {entry.sha256 ?? "—"}
                        {entry.content_collected ? "" : " · checksum only"}
                      </span>
                    ) : (
                      <span className="status status--unknown">absent</span>
                    )}
                  </div>
                ))}
              </div>
              <div>
                <b>etc/config.properties</b>
                <div className="table-scroll">
                  <table className="table">
                    <tbody>
                      {Object.entries(node.properties).map(([name, value]) => (
                        <tr key={name}>
                          <td className="mono">{name}</td>
                          <td className="mono">{value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}
