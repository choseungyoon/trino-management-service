"""Editing `config.properties` from the console: what a change may contain,
and when it may leave for a cluster.

⛔ A change is a **set of edits, never a file.** The scan that feeds this
screen redacts credential-shaped values (`http-server.https.keystore.key` and
friends), so TMS's copy of a node's `config.properties` has `[REDACTED]` where
the real secrets are. Writing that copy back would replace working passwords
with the literal string. So a change says "set this key, remove that key" and
the playbook merges; every line TMS never saw stays exactly as it was.

⛔ An unknown property name stops the server from booting - not "that setting
ignored", the whole process refuses to start (TRINO_VERIFIED T1-8-1). One typo
deployed to every node is every node down at once. TMS holds no table of valid
names; the cluster reported its own at startup and the scan collected them, and
a name that is not in that list is refused here.

⛔ Which role a setting belongs on is a **warning, not a barrier**. A
coordinator-only value on a worker starts fine (T1-8-2). The warning is derived
from what the cluster is doing now, not from a list somebody wrote.

Python 3.9 compatible.
"""

import re
from typing import Any, Dict, List, Optional, Sequence

#: Trino property names. Conservative on purpose: this string becomes the left
#: side of a line in a file that decides whether the server starts.
NAME = re.compile(r"^[a-zA-Z][A-Za-z0-9_.\-]*$")

#: Same rule as catalogs. A credential belongs in the node's environment, which
#: Trino resolves through `${ENV:VAR}` (T1-9-2), never in TMS.
SECRET_KEY = re.compile(
    r"(password|secret|credential|access[-_.]?key|private[-_.]?key"
    r"|\.key$|keystore|truststore)",
    re.IGNORECASE)
ENV_REFERENCE = re.compile(r"^\$\{ENV:[A-Za-z_][A-Za-z0-9_]*\}$")

SET = "set"
UNSET = "unset"
ACTIONS = (SET, UNSET)

ROLE_ALL = "all"
ROLE_COORDINATOR = "coordinator"
ROLE_WORKER = "worker"
#: ⛔ A closed vocabulary of three words, and the playbook asserts it again.
#: This is what reaches Ansible as the host pattern - never a host name, which
#: is the property that makes mis-targeting impossible rather than unlikely.
ROLES = (ROLE_ALL, ROLE_COORDINATOR, ROLE_WORKER)

MAX_ENTRIES = 50
MAX_VALUE_CHARS = 2000


class ConfigEditError(ValueError):
    """A change that cannot be stored, with a sentence saying why."""


def validate(title: str, target_role: str,
             entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Refuse a change that cannot be written. Raises ConfigEditError."""
    title = (title or "").strip()
    if not title:
        raise ConfigEditError(
            "Give this change a title. It is what the deployment history shows "
            "six months from now.")

    target_role = (target_role or "").strip().lower()
    if target_role not in ROLES:
        raise ConfigEditError(
            "Deploy to all nodes, the coordinator, or the workers - not {!r}."
            .format(target_role))

    if not entries:
        raise ConfigEditError("A change needs at least one property.")
    if len(entries) > MAX_ENTRIES:
        raise ConfigEditError(
            "At most {} properties in one change.".format(MAX_ENTRIES))

    cleaned, seen = [], set()
    for entry in entries:
        key = str(entry.get("key") or "").strip()
        action = str(entry.get("action") or SET).strip().lower()
        value = entry.get("value")
        value = "" if value is None else str(value).strip()

        if not NAME.match(key):
            raise ConfigEditError(
                "{!r} is not a Trino property name. Letters, digits, dot, dash "
                "and underscore, starting with a letter.".format(key))
        if key in seen:
            raise ConfigEditError(
                "{} appears twice. A file cannot hold two values for it."
                .format(key))
        seen.add(key)

        if action not in ACTIONS:
            raise ConfigEditError(
                "{}: action must be 'set' or 'unset'.".format(key))

        if action == UNSET:
            # ⛔ Removing a property is not a null change: Trino falls back to
            # its default, which may differ from what the node runs today.
            cleaned.append({"key": key, "action": UNSET, "value": None})
            continue

        if not value:
            raise ConfigEditError(
                "{} has no value. To take it out of the file, choose Remove - "
                "an empty value is a value.".format(key))
        if len(value) > MAX_VALUE_CHARS:
            raise ConfigEditError("{}: value is too long.".format(key))
        if "\n" in value or "\r" in value:
            raise ConfigEditError(
                "{}: a value is one line. A newline would split it into two "
                "properties.".format(key))
        if SECRET_KEY.search(key) and not ENV_REFERENCE.match(value):
            raise ConfigEditError(
                "{} looks like a credential, so it must be written as "
                "${{ENV:VARIABLE_NAME}}. Trino reads that from the node's own "
                "environment; a literal here would put the secret in TMS's "
                "database, its API and its audit log.".format(key))
        cleaned.append({"key": key, "action": SET, "value": value})

    return {"title": title, "target_role": target_role,
            "entries": sorted(cleaned, key=lambda e: e["key"])}


def unknown_names(entries: Sequence[Dict[str, Any]],
                  valid_names: Sequence[str]) -> List[str]:
    """Property names this cluster's build does not accept.

    ⛔ Only the names being *set*. Removing a property Trino does not know is
    harmless - the line is not there, or it is there and is already stopping
    the server, and taking it out is the fix.
    """
    known = set(valid_names or [])
    return sorted({e["key"] for e in entries or []
                   if e.get("action") != UNSET and e["key"] not in known})


def refuse_deploy(change: Dict[str, Any], cluster: str,
                  development_clusters: Sequence[str],
                  valid_names: Sequence[str],
                  scanned: bool) -> Optional[str]:
    """Why this change may not go to this cluster, or None.

    Two gates, and they answer different questions:

    * the **name check** catches a typo before it reaches any node, including
      the development one. It is the cheap gate and it runs everywhere.
    * the **development cluster** catches everything a name check cannot -
      a valid name with a value that stops the server. That cost is paid by
      one cluster instead of all of them (D-018).
    """
    development = list(development_clusters or [])
    entries = change.get("entries") or []

    # ⛔ No list means TMS cannot tell a typo from a real property, and a typo
    # here takes down every node it reaches. Refusing is the only honest
    # answer; skipping the check silently would be the dangerous one.
    if not scanned:
        return ("TMS has not read this cluster's configuration yet, so it does "
                "not know which property names this build accepts. Run the scan "
                "on this screen first.")
    if not valid_names:
        return ("The scan of this cluster returned no property names, so the "
                "typo check has nothing to check against. Trino prints them at "
                "startup - if the list is empty, the scan is not reading the "
                "right log. Nothing will be deployed until it is.")

    unknown = unknown_names(entries, valid_names)
    if unknown:
        return ("This cluster does not accept {}. An unknown property stops "
                "Trino from starting, so nothing is deployed. Check the "
                "spelling against the Known properties list."
                .format(", ".join(unknown)))

    if cluster in development:
        return None
    if not development:
        return ("No development cluster is configured, so there is nowhere to "
                "prove this before it reaches production. Set "
                "cluster_ops.config_scan.development_clusters.")

    verified = change.get("verified_on")
    if not verified:
        return ("This change has not been proved on a development cluster yet. "
                "Deploy it there, restart, and confirm health is GOOD first.")
    if verified not in development:
        return ("This change was last proved on {}, which is not a development "
                "cluster.".format(verified))
    return None


def role_advice(entries: Sequence[Dict[str, Any]], target_role: str,
                nodes: Sequence[Dict[str, Any]]) -> List[str]:
    """Where a change is about to land somewhere the setting is not used today.

    ⛔ Advice, never a refusal. A coordinator-only value on a worker starts
    fine (T1-8-2), so blocking would cost more than it saves.

    Derived from the scan rather than from a table of which property belongs to
    which role. TMS holds no such table on purpose - the cluster is the
    authority on its own build (D-018), and a list maintained here would be a
    second opinion about a version nobody checked.
    """
    if target_role == ROLE_COORDINATOR:
        return []

    by_role: Dict[str, List[Dict[str, Any]]] = {}
    for node in nodes or []:
        if node.get("reachable") and not node.get("error"):
            by_role.setdefault(node.get("role"), []).append(node)
    workers = by_role.get(ROLE_WORKER) or []
    coordinators = by_role.get(ROLE_COORDINATOR) or []
    if not workers or not coordinators:
        return []

    advice = []
    for entry in entries or []:
        if entry.get("action") == UNSET:
            continue
        key = entry["key"]
        on_coordinator = any(key in (n.get("properties") or {}) for n in coordinators)
        on_worker = any(key in (n.get("properties") or {}) for n in workers)
        if on_coordinator and not on_worker:
            advice.append(
                "{} is set on the coordinator today and on none of the {} "
                "workers. This change writes it to them too. That starts fine; "
                "it is worth knowing it is new there."
                .format(key, len(workers)))
    return advice


def summarise(entries: Sequence[Dict[str, Any]]) -> str:
    """One line for a history row, so the table is readable without expanding."""
    sets = [e["key"] for e in entries or [] if e.get("action") != UNSET]
    unsets = [e["key"] for e in entries or [] if e.get("action") == UNSET]
    parts = []
    if sets:
        parts.append("set " + ", ".join(sets))
    if unsets:
        parts.append("remove " + ", ".join(unsets))
    return " · ".join(parts)
