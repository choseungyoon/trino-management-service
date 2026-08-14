"""What may be saved into Trino's resource group tables (DESIGN_WL07.md 4).

Almost nothing here is enforced by the database. Trino's schema has no unique
constraint on (name, parent, environment), no check on the limit formats, and
no idea which selector targets which group - and the meaning of every value
lives in Trino, not in PostgreSQL. So this module is the only thing between an
operator and a configuration that stops the cluster admitting queries ten
seconds later.

Findings come in two levels, and the distinction matters:

* ``ERROR``   - refuse the save. The configuration would be broken or
  undefined.
* ``WARNING`` - save it, but say so. The configuration is legal and may well be
  intended; it just has a consequence people repeatedly fail to predict.

Column limits below are not guesses: they are the live schema measured on
Trino 477 (TRINO_VERIFIED.md T1-4-1).

Python 3.9 compatible.
"""

import re
from typing import Any, Dict, List, Optional

ERROR = "error"
WARNING = "warning"

# Measured, not assumed - see TRINO_VERIFIED.md T1-4-1.
MAX_NAME = 250
MAX_REGEX = 512
MAX_USER_GROUP_REGEX = 2048

SCHEDULING_POLICIES = ("fair", "weighted_fair", "weighted", "query_priority")

# Airlift data sizes and durations, as used by every size/time limit in the
# resource group spec. Deliberately permissive about case: rejecting a value
# Trino would have accepted is worse than passing one it rejects, because the
# operator can see Trino's own error but cannot argue with ours.
_DATA_SIZE = re.compile(r"^\s*\d+(\.\d+)?\s*(B|kB|MB|GB|TB|PB)\s*$", re.IGNORECASE)
_PERCENTAGE = re.compile(r"^\s*\d+(\.\d+)?\s*%\s*$")
_DURATION = re.compile(r"^\s*\d+(\.\d+)?\s*(ns|us|ms|s|m|h|d)\s*$")

_REGEX_COLUMNS = {
    "user_regex": MAX_REGEX,
    "source_regex": MAX_REGEX,
    "query_type": MAX_REGEX,
    "client_tags": MAX_REGEX,
    "original_user_regex": MAX_REGEX,
    "authenticated_user_regex": MAX_REGEX,
    "user_group_regex": MAX_USER_GROUP_REGEX,
}

# Columns that are Java regular expressions. `query_type` and `client_tags` are
# exact-match and JSON respectively, so compiling them would reject valid input.
_REGEX_SYNTAX_COLUMNS = (
    "user_regex", "source_regex", "original_user_regex",
    "authenticated_user_regex", "user_group_regex",
)


class Finding:
    __slots__ = ("level", "code", "target", "message")

    def __init__(self, level: str, code: str, target: str, message: str) -> None:
        self.level = level
        self.code = code
        self.target = target
        self.message = message

    @property
    def blocking(self) -> bool:
        return self.level == ERROR

    def as_dict(self) -> Dict[str, str]:
        return {"level": self.level, "code": self.code,
                "target": self.target, "message": self.message}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Finding({} {} {!r})".format(self.level, self.code, self.message)


def percentage_of(value: Optional[str]) -> Optional[float]:
    """'80%' -> 80.0, '1GB' -> None.

    ⚠️ A percentage here is a share of the *cluster's* memory, not the parent
    group's (Trino 477 docs). Sibling percentages summing past their parent is
    therefore a real smell rather than an arithmetic identity.
    """
    if not value or not _PERCENTAGE.match(str(value)):
        return None
    return float(str(value).strip().rstrip("%").strip())


def _is_size(value: str) -> bool:
    return bool(_DATA_SIZE.match(value) or _PERCENTAGE.match(value))


def _parent_id(group: Dict[str, Any]) -> str:
    return ".".join((group.get("path") or [])[:-1])


def validate(groups: List[Dict[str, Any]], selectors: List[Dict[str, Any]],
             group_provider_configured: bool = False) -> List[Finding]:
    """Check a whole tree, not one row.

    Deliberately whole-tree: the rules that matter most are relational. A single
    group can be perfectly valid while the tree it now sits in has no catch-all
    selector, or two siblings with the same name. Callers validate the tree the
    change *would* produce, then commit or roll back.
    """
    findings: List[Finding] = []
    findings += _check_groups(groups)
    findings += _check_hierarchy(groups)
    findings += _check_selectors(groups, selectors, group_provider_configured)
    return findings


def blocking(findings: List[Finding]) -> List[Finding]:
    return [f for f in findings if f.blocking]


# ------------------------------------------------------------------ groups


def _check_groups(groups: List[Dict[str, Any]]) -> List[Finding]:
    findings: List[Finding] = []
    for group in groups:
        gid = group.get("id") or group.get("name") or "(unnamed)"
        name = (group.get("name") or "").strip()

        if not name:
            findings.append(Finding(ERROR, "V1", gid, "A group needs a name."))
        elif len(name) > MAX_NAME:
            findings.append(Finding(
                ERROR, "V1", gid,
                "Name is {} characters; the column holds {}.".format(len(name), MAX_NAME)))

        for column, label in (("max_queued", "Max queued"),
                              ("hard_concurrency_limit", "Concurrency limit")):
            value = group.get(column)
            if value is None:
                findings.append(Finding(
                    ERROR, "V2", gid, "{} is required.".format(label)))
            elif int(value) <= 0:
                # Trino accepts 0. It means this group runs nothing, or queues
                # nothing and rejects immediately - a way to switch a group off
                # that looks like a tuning value.
                findings.append(Finding(
                    ERROR, "V2", gid,
                    "{} is {}. That stops this group entirely; delete the group "
                    "if that is the intent.".format(label, value)))

        memory = group.get("soft_memory_limit")
        if memory and not _is_size(str(memory)):
            findings.append(Finding(
                ERROR, "V3", gid,
                "Memory limit {!r} is neither a size (100GB) nor a share "
                "(80%).".format(memory)))

        for column in ("soft_cpu_limit", "hard_cpu_limit"):
            value = group.get(column)
            if value and not _DURATION.match(str(value)):
                findings.append(Finding(
                    ERROR, "V3", gid,
                    "{} {!r} is not a duration (1h, 30m).".format(column, value)))

        if group.get("soft_cpu_limit") and not group.get("hard_cpu_limit"):
            findings.append(Finding(
                ERROR, "V4", gid,
                "A soft CPU limit needs a hard CPU limit alongside it."))

        policy = group.get("scheduling_policy")
        if policy and policy not in SCHEDULING_POLICIES:
            findings.append(Finding(
                ERROR, "V5", gid,
                "Scheduling policy {!r} is not one of {}.".format(
                    policy, ", ".join(SCHEDULING_POLICIES))))

        scan = group.get("hard_physical_data_scan_limit")
        if scan and not _is_size(str(scan)):
            findings.append(Finding(
                ERROR, "V3", gid,
                "Scan limit {!r} is not a size (10GB).".format(scan)))
        elif scan:
            findings.append(Finding(
                WARNING, "W3", gid,
                "A scan quota does not fail a query when it runs out - new "
                "queries queue until the quota period rolls, which can be an "
                "hour of this group appearing simply slow."))

        if group.get("hard_cpu_limit"):
            findings.append(Finding(
                WARNING, "W3", gid,
                "A CPU quota does not fail a query when it runs out - new "
                "queries queue until the quota period rolls."))

        if not group.get("jmx_export"):
            findings.append(Finding(
                WARNING, "W2", gid,
                "jmxExport is off, so this group never appears on the Workload "
                "screen. Intended for per-user groups; a mistake on the rest."))

    return findings


def _check_hierarchy(groups: List[Dict[str, Any]]) -> List[Finding]:
    findings: List[Finding] = []

    seen: Dict[str, int] = {}
    for group in groups:
        gid = group.get("id") or ""
        seen[gid] = seen.get(gid, 0) + 1
    for gid, count in sorted(seen.items()):
        if count > 1:
            findings.append(Finding(
                ERROR, "V9", gid,
                "{} groups share this path. Trino's schema has no unique "
                "constraint, so duplicates are accepted silently and only one "
                "of them is ever used.".format(count)))

    by_id = {group.get("id"): group for group in groups}

    # query_priority is only meaningful if the whole subtree agrees; Trino's
    # documentation states the sub-groups must be configured with it too.
    for group in groups:
        if group.get("scheduling_policy") != "query_priority":
            continue
        prefix = (group.get("id") or "") + "."
        for other in groups:
            other_id = other.get("id") or ""
            if other_id.startswith(prefix) and other.get("scheduling_policy") != "query_priority":
                findings.append(Finding(
                    ERROR, "V6", other_id,
                    "{} uses query_priority, so every group beneath it must "
                    "too.".format(group.get("id"))))

    # Sibling shares are of cluster memory, not of the parent, so they can be
    # set to sum past it without any error - and then the parent's own limit is
    # what actually binds, which is rarely what someone meant to configure.
    by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for group in groups:
        by_parent.setdefault(_parent_id(group), []).append(group)
    for parent_id, children in sorted(by_parent.items()):
        if not parent_id:
            continue
        parent = by_id.get(parent_id)
        parent_share = percentage_of((parent or {}).get("soft_memory_limit"))
        if parent_share is None:
            continue
        shares = [percentage_of(child.get("soft_memory_limit")) for child in children]
        total = sum(share for share in shares if share is not None)
        if total > parent_share:
            findings.append(Finding(
                WARNING, "W1", parent_id,
                "Children add up to {:g}% of cluster memory but {} allows "
                "{:g}%. Shares are of the cluster, not of the parent.".format(
                    total, parent_id, parent_share)))

    return findings


# --------------------------------------------------------------- selectors


def _check_selectors(groups, selectors, group_provider_configured) -> List[Finding]:
    findings: List[Finding] = []
    known = {group.get("id") for group in groups}

    for selector in selectors:
        sid = "selector {}".format(selector.get("id"))
        target = selector.get("target")
        if not target or target not in known:
            findings.append(Finding(
                ERROR, "V8", sid,
                "This selector points at a group that does not exist in this "
                "environment, so it can never place a query."))

        matchers = selector.get("matchers") or {}
        for column, limit in _REGEX_COLUMNS.items():
            value = matchers.get(column)
            if value is not None and len(str(value)) > limit:
                findings.append(Finding(
                    ERROR, "V7", sid,
                    "{} is {} characters; the column holds {}.".format(
                        column, len(str(value)), limit)))

        for column in _REGEX_SYNTAX_COLUMNS:
            value = matchers.get(column)
            if not value:
                continue
            try:
                re.compile(str(value))
            except re.error as exc:
                findings.append(Finding(
                    ERROR, "V7", sid,
                    "{} is not a valid regular expression: {}.".format(column, exc)))

        if matchers.get("user_group_regex") and not group_provider_configured:
            findings.append(Finding(
                WARNING, "W4", sid,
                "user_group_regex only matches when Trino has a group provider "
                "(etc/group-provider.properties). Without one the groups it "
                "compares against are always empty, so this rule is dead."))

    # Keyed on `groups`, not on `selectors`. Guarding on the selector list let
    # the empty case through - deleting the last selector left zero of them,
    # which is precisely the state this rule exists to prevent, and validation
    # waved it past. An environment with no groups at all is a different thing:
    # nothing is configured, so nothing is misconfigured, and refusing it would
    # make the very first group impossible to create.
    if groups and not any(s.get("catch_all") for s in selectors):
        findings.append(Finding(
            ERROR, "V10", "selectors",
            "No catch-all selector. Every rule here narrows what it matches, "
            "so a query matching none of them has nowhere to go - and Trino "
            "477 does not document what it does then."))

    targeted = {s.get("target") for s in selectors}
    for group in groups:
        gid = group.get("id") or ""
        has_children = any((other.get("id") or "").startswith(gid + ".") for other in groups)
        if gid not in targeted and not has_children:
            findings.append(Finding(
                WARNING, "W5", gid,
                "No selector sends anything here, and it has no sub-groups. "
                "Nothing will ever run in this group."))

    return findings
