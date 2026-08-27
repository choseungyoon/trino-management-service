"""What is actually on each node, and where the nodes disagree.

Reads nothing itself. A playbook the operator installs prints one tagged JSON
line per host; this parses those lines, removes anything that looks like a
credential, and answers the one question the screen exists for: **are these
nodes configured the same way, and if not, where?**

⛔ Compared within a role, never across. A coordinator and a worker are
*supposed* to differ - `coordinator=true`, a different heap, a different port.
Comparing them against each other would report drift on every healthy cluster,
and a drift screen that is always red is a drift screen nobody reads.

⛔ Content is collected for an allowlist of files only. `etc/catalog/*` holds
`connection-password`, and copying those into TMS's database would move the
credentials with them - so catalogs are compared by checksum. "Do these nodes
have the same catalog?" is answerable without "what is in it?".

⛔ TMS does not decide what a valid property name is. Trino prints every one it
accepts at startup (TRINO_VERIFIED T1-8-3), and an unknown name stops the
server from booting at all (T1-8-1). `valid_names` is that list, collected per
node, and it is what a later deploy checks a typo against. A table maintained
here would be a second opinion about a build TMS has never seen.

Python 3.9 compatible.
"""

import base64
import hashlib
import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

#: The playbook prefixes its one JSON line per host with this. Anything else on
#: stdout - Ansible's own chatter, a task banner, a warning - is ignored.
MARKER = "TMS-CONFIG-SCAN "

#: Files whose *content* is collected. Everything else is checksum-only.
#: ⛔ `etc/catalog/*` is deliberately absent: those files hold credentials.
CONTENT_FILES = ("etc/config.properties", "etc/jvm.config", "etc/log.properties")

#: A value is dropped when its key matches. Trino redacts the same way in its
#: own startup dump, which is where the idea comes from.
#
# ⛔ Errs toward redacting. `http-server.https.keystore.key` is a *password*
# despite reading like a filename, and anything ending in `.key` in Trino's
# configuration has been one so far. A value wrongly hidden costs a screen a
# cell; a value wrongly shown costs a credential.
SECRET_KEY = re.compile(
    r"(password|secret|credential|token|access[-_.]?key|private[-_.]?key"
    r"|\.key$|keystore|truststore)",
    re.IGNORECASE)
REDACTED = "[REDACTED]"

#: ⛔ Never deployed as one file. `node.id` must differ per node, so there is
#: no such thing as "the right content" for it - only "the right content for
#: this host". Reported for drift, never offered for editing.
PER_NODE_FILES = ("etc/node.properties",)


def redact(properties: Dict[str, str]) -> Dict[str, str]:
    """Drop anything whose key reads like a credential."""
    return {k: (REDACTED if SECRET_KEY.search(k) else v)
            for k, v in (properties or {}).items()}


def parse_properties(text: Optional[str]) -> Dict[str, str]:
    """A Java properties file, as far as Trino uses them.

    No line continuations and no `:` separator - Trino's own files use
    `key=value` and a parser that accepted more would report differences that
    the server does not see.
    """
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def checksum(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def parse_scan(lines: Iterable[str]) -> List[Dict[str, Any]]:
    """Pull the tagged JSON lines out of a playbook's output.

    A host whose line will not parse is reported as an error rather than
    dropped: a node missing from the comparison would silently read as
    "everything agrees".
    """
    nodes = []
    for line in lines or []:
        index = line.find(MARKER)
        if index < 0:
            continue
        payload = line[index + len(MARKER):].strip()
        try:
            nodes.append(_node(json.loads(payload)))
        except (ValueError, TypeError) as exc:
            log.warning("unreadable config scan line: %s", exc)
            nodes.append({"host": "?", "role": "unknown", "reachable": False,
                          "error": "TMS could not read this node's scan output: "
                                   "{}".format(exc),
                          "files": {}, "properties": {}, "valid_names": []})
    return nodes


def _decode(entry: Dict[str, Any]) -> Optional[str]:
    """The playbook sends `slurp`'s base64 rather than raw text.

    A configuration file is several lines and a one-line JSON message is the
    transport; base64 removes every question about what happens to a newline
    or a quote on the way. Undecodable content is treated as absent rather
    than guessed at.
    """
    if entry.get("content") is not None:
        return entry["content"]
    blob = entry.get("content_b64")
    if not blob:
        return None
    try:
        return base64.b64decode(blob).decode("utf-8", "replace")
    except (ValueError, TypeError):
        log.warning("could not decode collected file content")
        return None


def _node(raw: Dict[str, Any]) -> Dict[str, Any]:
    files = {}
    for path, entry in (raw.get("files") or {}).items():
        content = _decode(entry) if isinstance(entry, dict) else None
        present = bool(entry.get("present")) if isinstance(entry, dict) else False
        record = {
            "present": present,
            # The playbook may send a checksum instead of content for files
            # that are not on the allowlist. Either way there is a checksum.
            "sha256": (entry.get("sha256") if isinstance(entry, dict) else None)
                      or checksum(content),
            "content_collected": path in CONTENT_FILES and content is not None,
        }
        if record["content_collected"]:
            record["properties"] = redact(parse_properties(content))
        files[path] = record

    # ⛔ What the node is actually running, taken from its own
    # `etc/config.properties`. The startup dump would be closer to "in force",
    # but it is 447 lines per node and most of it is a default nobody set - the
    # file is what an operator edits and what a deploy would overwrite.
    effective = dict(files.get("etc/config.properties", {}).get("properties") or {})
    effective.update(redact(raw.get("properties") or {}))

    return {
        "host": raw.get("host") or "?",
        "role": raw.get("role") or "unknown",
        "reachable": bool(raw.get("reachable", True)),
        "error": raw.get("error"),
        "files": files,
        "properties": effective,
        "valid_names": sorted(raw.get("valid_names") or []),
    }


def compare(nodes: List[Dict[str, Any]],
            ignore_missing_nodes: bool = False) -> Dict[str, Any]:
    """Where do nodes of the same role disagree?

    `ignore_missing_nodes` is for a development cluster, whose worker count
    changes with whatever is being tested. A node that is not there is not
    drift there; on a production cluster it is the first thing to say.
    """
    reachable = [n for n in nodes if n["reachable"] and not n.get("error")]
    unreachable = [n for n in nodes if not n["reachable"] or n.get("error")]

    findings: List[Dict[str, Any]] = []
    for role in sorted({n["role"] for n in reachable}):
        peers = [n for n in reachable if n["role"] == role]
        if len(peers) < 2:
            # Nothing to compare against. One coordinator is the normal case,
            # and a lone worker is normal on a development cluster.
            continue
        findings.extend(_file_findings(role, peers))
        findings.extend(_property_findings(role, peers))

    if unreachable and not ignore_missing_nodes:
        findings.append({
            "kind": "unreachable",
            "role": "-",
            "subject": ", ".join(n["host"] for n in unreachable),
            "detail": "TMS could not read the configuration of {} node(s). "
                      "What the others agree on says nothing about "
                      "these.".format(len(unreachable)),
            "hosts": {n["host"]: (n.get("error") or "unreachable")
                      for n in unreachable},
        })

    return {
        "nodes": nodes,
        "findings": findings,
        "agree": not findings,
        # Every property name this cluster's build accepts, taken from the
        # nodes themselves. The intersection, not the union: a name only one
        # node knows is a name a deploy to all of them would break on.
        "valid_names": _shared_names(reachable),
        "roles": sorted({n["role"] for n in nodes}),
    }


def _file_findings(role: str, peers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings = []
    paths = sorted({p for node in peers for p in node["files"]})
    for path in paths:
        by_host = {}
        for node in peers:
            entry = node["files"].get(path)
            by_host[node["host"]] = (entry or {}).get("sha256") if (
                entry and entry.get("present")) else None
        distinct = {v for v in by_host.values()}
        if len(distinct) < 2:
            continue
        missing = [h for h, v in by_host.items() if v is None]
        findings.append({
            "kind": "missing_file" if missing else "file_differs",
            "role": role,
            "subject": path,
            "detail": ("{} does not have this file.".format(", ".join(sorted(missing)))
                       if missing else
                       "The file differs between nodes of the same role."),
            "hosts": {h: (v or "absent") for h, v in by_host.items()},
            # ⛔ node.properties is *expected* to differ - node.id is unique.
            # Reported anyway, because "which nodes have it" is still worth
            # seeing, but marked so the screen does not shout.
            "expected": path in PER_NODE_FILES,
        })
    return findings


def _property_findings(role: str, peers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Effective values that differ between same-role nodes.

    Uses what the server reported, not what the file says: a value can be set
    in a file and overridden, and the operator cares about what is in force.
    """
    findings = []
    names = sorted({name for node in peers for name in node["properties"]})
    for name in names:
        by_host = {node["host"]: node["properties"].get(name) for node in peers}
        if len({json.dumps(v, sort_keys=True) for v in by_host.values()}) < 2:
            continue
        findings.append({
            "kind": "value_differs",
            "role": role,
            "subject": name,
            "detail": "Nodes of the same role are running different values.",
            "hosts": {h: (v if v is not None else "not set")
                      for h, v in by_host.items()},
            "expected": False,
        })
    return findings


def _shared_names(nodes: List[Dict[str, Any]]) -> List[str]:
    """Property names every scanned node accepts.

    ⛔ The intersection. A deploy goes to several nodes at once and an unknown
    name stops a server booting (T1-8-1), so a name only one node knows is a
    name that would break the others.
    """
    known = [set(n["valid_names"]) for n in nodes if n["valid_names"]]
    if not known:
        return []
    shared = known[0]
    for other in known[1:]:
        shared &= other
    return sorted(shared)
