#!/usr/bin/env python3
"""Bolt 2 / V1 - environment connectivity verification for TMS.

Read-only. Submits no SQL and performs no write action against Trino.
Run this before writing any collector code: the design assumes facts that were
verified against Trino 477 documentation and source, but this script confirms
they hold in *our* environment (basic auth, TLS, rules.json).

Covers V1-1..V1-5 and V1-7 from docs/BOLTS.md.
V1-6 (kill) and V1-8 (CPU budget) are deliberately excluded - see docs/BOLTS.md.

Usage:
    python3 scripts/verify_connectivity.py \\
        --coordinator https://trino-a-coord.example.internal:8443 \\
        --user tms-svc \\
        --expected-workers 12

Password is read from the TMS_TRINO_PASSWORD environment variable so it never
appears in shell history or process listings:

    read -rs TMS_TRINO_PASSWORD && export TMS_TRINO_PASSWORD

Python 3.9+ compatible. Standard library only - no Artifactory round trip needed.
"""

import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# Query states to request. Terminal states (FINISHED, FAILED) are excluded on
# purpose: completed queries belong to the separate query-history project
# (D-001), and including them inflates the response.
# Verified against io/trino/execution/QueryState.java @477.
LIVE_STATES = [
    "QUEUED",
    "WAITING_FOR_RESOURCES",
    "DISPATCHING",
    "PLANNING",
    "STARTING",
    "RUNNING",
    "FINISHING",
]

# MBeans used by health tests H-03..H-07.
#
# WARNING: do NOT trust the Trino docs for these names. The 477 docs page
# admin/jmx still lists `trino.failuredetector:name=HeartbeatFailureDetector`,
# but FailureDetectorModule is no longer installed in 477 - the coordinator now
# uses io.trino.node.CoordinatorNodeManager. Requesting the stale name returns
# HTTP 500 (MBeanResource declares `throws JMException` and does not map
# InstanceNotFoundException to 404).
#
# The script therefore enumerates GET /v1/jmx/mbean first and verifies every
# name below actually exists before fetching it. See docs/TRINO_VERIFIED.md T1-7.
HEALTH_MBEANS = [
    "trino.node:name=CoordinatorNodeManager",
    "java.lang:type=Memory",
    "trino.execution:name=QueryManager",
    "trino.memory:name=ClusterMemoryManager",
]

# Substrings used to surface candidate MBeans when an expected name is missing,
# so a stale name is diagnosed in one run instead of guessed at.
DISCOVERY_HINTS = ["node", "failuredetector", "memory", "querymanager", "execution"]

TIMEOUT_SECONDS = 10


class Result:
    """Collects check outcomes so the script can exit with a meaningful code."""

    def __init__(self) -> None:
        self.passed: List[str] = []
        self.failed: List[str] = []
        self.warned: List[str] = []
        self.facts: Dict[str, Any] = {}

    def ok(self, check: str, detail: str = "") -> None:
        self.passed.append(check)
        print("  [ OK ] {}{}".format(check, " - " + detail if detail else ""))

    def warn(self, check: str, detail: str) -> None:
        self.warned.append(check)
        print("  [WARN] {} - {}".format(check, detail))

    def fail(self, check: str, detail: str) -> None:
        self.failed.append(check)
        print("  [FAIL] {} - {}".format(check, detail))

    def fact(self, key: str, value: Any) -> None:
        self.facts[key] = value


def build_opener(verify_tls: bool) -> urllib.request.OpenerDirector:
    if verify_tls:
        context = ssl.create_default_context()
    else:
        # Only for environments with an internal CA that is not on this host.
        # Never use this against anything you do not control.
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))


def request(
    opener: urllib.request.OpenerDirector,
    url: str,
    user: Optional[str],
    password: Optional[str],
) -> Tuple[int, str, float]:
    """Return (status, body, elapsed_seconds). Never raises on HTTP errors."""
    req = urllib.request.Request(url, method="GET")
    if user is not None:
        token = base64.b64encode(
            "{}:{}".format(user, password or "").encode("utf-8")
        ).decode("ascii")
        req.add_header("Authorization", "Basic " + token)
    # Deliberately NOT sending X-Trino-User: keeping the authenticated user equal
    # to the session user avoids the impersonation check entirely
    # (HttpRequestSessionContextFactory @477). See ARCHITECTURE.md 6-2.
    started = time.time()
    try:
        with opener.open(req, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body, time.time() - started
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body, time.time() - started
    except Exception as exc:  # noqa: BLE001 - report, do not crash the run
        return 0, "{}: {}".format(type(exc).__name__, exc), time.time() - started


def check_v1_1_info(opener, base: str, result: Result) -> None:
    """V1-1: /v1/info is PUBLIC. Also pin down the startup-status field name."""
    print("\nV1-1  GET /v1/info  (expected: 200 WITHOUT credentials)")
    status, body, _ = request(opener, base + "/v1/info", None, None)
    if status != 200:
        result.fail("V1-1", "expected 200 without auth, got {}".format(status))
        return
    try:
        info = json.loads(body)
    except ValueError:
        result.fail("V1-1", "response is not JSON: {}".format(body[:200]))
        return

    result.ok("V1-1", "PUBLIC access confirmed")
    result.fact("v1_info_keys", sorted(info.keys()))
    print("       fields: {}".format(", ".join(sorted(info.keys()))))

    # H-02 needs a startup-completion signal. Find whatever field carries it
    # instead of guessing a name.
    candidates = [k for k in info if "start" in k.lower() or "coordinator" in k.lower()]
    if candidates:
        for key in candidates:
            print("       -> {} = {!r}".format(key, info[key]))
        result.fact("h02_candidate_fields", {k: info[k] for k in candidates})
        result.ok("V1-1/H-02", "startup-status candidates found: {}".format(candidates))
    else:
        result.warn("V1-1/H-02", "no obvious startup field; H-02 stays unimplemented")

    status_state, body_state, _ = request(opener, base + "/v1/info/state", None, None)
    if status_state == 200:
        result.ok("V1-1b", "/v1/info/state PUBLIC, value={}".format(body_state.strip()))
    else:
        result.fail("V1-1b", "/v1/info/state returned {}".format(status_state))


def check_v1_2_3_jmx(
    opener, base: str, user: str, password: str, expected_workers: int, result: Result
) -> None:
    """V1-2/V1-3: JMX over HTTP requires system_information:read in rules.json.

    Enumerates the MBean registry first so a name that no longer exists is
    reported as "not registered" with candidates, rather than as a bare 500.
    """
    print("\nV1-2  GET /v1/jmx/mbean  (expected: 200 WITH tms-svc basic auth)")
    status, body, _ = request(opener, base + "/v1/jmx/mbean", user, password)
    if status == 403:
        result.fail(
            "V1-2",
            "403 Forbidden - tms-svc lacks system_information:read in rules.json. "
            "H-03..H-07 cannot work. See ARCHITECTURE.md 6-3-2.",
        )
        return
    if status != 200:
        result.fail("V1-2", "expected 200, got {} - {}".format(status, body[:200]))
        return

    registered: List[str] = []
    try:
        for entry in json.loads(body):
            if isinstance(entry, dict) and entry.get("objectName"):
                registered.append(str(entry["objectName"]))
    except ValueError:
        result.warn("V1-2", "MBean list is not JSON; skipping existence pre-check")
    result.fact("registered_mbean_count", len(registered))
    result.ok("V1-2", "JMX reachable, {} MBeans registered".format(len(registered)))

    print("\nV1-3  MBeans used by H-03..H-07")
    for object_name in HEALTH_MBEANS:
        if registered and object_name not in registered:
            candidates = [
                name
                for name in registered
                if any(hint in name.lower() for hint in DISCOVERY_HINTS)
            ]
            result.fail(
                "V1-3:" + object_name,
                "NOT REGISTERED on this server - the name is stale or the module "
                "is not installed. Candidates printed below.",
            )
            result.fact("candidates_for:" + object_name, sorted(candidates)[:40])
            for name in sorted(candidates)[:25]:
                print("         candidate: {}".format(name))
            continue

        url = base + "/v1/jmx/mbean/" + urllib.parse.quote(object_name, safe="")
        status, body, _ = request(opener, url, user, password)
        if status != 200:
            result.fail(
                "V1-3:" + object_name,
                "status {} (500 usually means the ObjectName does not exist)".format(
                    status
                ),
            )
            continue
        try:
            mbean = json.loads(body)
        except ValueError:
            result.fail("V1-3:" + object_name, "non-JSON response")
            continue
        attributes = {
            a.get("name"): a.get("value")
            for a in mbean.get("attributes", [])
            if isinstance(a, dict)
        }
        result.ok("V1-3:" + object_name, "{} attributes".format(len(attributes)))
        result.fact("mbean:" + object_name, sorted(attributes.keys()))

        # H-03 thresholds depend on whether the coordinator is counted as a node.
        if "CoordinatorNodeManager" in object_name:
            node_counts = {
                key: value
                for key, value in attributes.items()
                if key.endswith("NodeCount")
            }
            result.fact("node_counts", node_counts)
            for key in sorted(node_counts):
                print("       {} = {}".format(key, node_counts[key]))
            active = node_counts.get("ActiveNodeCount")
            if active is None:
                result.warn("V1-3/H-03", "ActiveNodeCount not present")
            else:
                if active == expected_workers:
                    note = "== expected_workers -> coordinator NOT counted"
                elif active == expected_workers + 1:
                    note = "== expected_workers+1 -> coordinator IS counted"
                else:
                    note = "neither {} nor {} - investigate before setting thresholds".format(
                        expected_workers, expected_workers + 1
                    )
                print("       -> ActiveNodeCount={} : {}".format(active, note))
                result.ok("V1-3/H-03", "ActiveNodeCount={}".format(active))


def check_v1_4_5_queries(
    opener, base: str, user: str, password: str, result: Result
) -> None:
    """V1-4/V1-5: query list must not be silently filtered to empty.

    With file access control a queries-rule denial shows up as an EMPTY LIST,
    not a 403. That is indistinguishable from an idle cluster, so cross-check
    against the JMX RunningQueries counter (health test H-09).
    """
    print("\nV1-4  GET /v1/query  (watch for SILENT permission filtering)")
    query = "&".join("state=" + s for s in LIVE_STATES)
    url = base + "/v1/query?" + query
    status, body, elapsed = request(opener, url, user, password)
    if status != 200:
        result.fail("V1-4", "expected 200, got {} - {}".format(status, body[:200]))
        return
    try:
        queries = json.loads(body)
    except ValueError:
        result.fail("V1-4", "non-JSON response")
        return

    size_bytes = len(body.encode("utf-8"))
    result.fact("query_count", len(queries))
    result.fact("query_response_bytes", size_bytes)
    result.ok("V1-4", "{} live queries returned".format(len(queries)))

    # Cross-check with JMX so we do not mistake a permission denial for idleness.
    jmx_url = base + "/v1/jmx/mbean/" + urllib.parse.quote(
        "trino.execution:name=QueryManager", safe=""
    )
    jmx_status, jmx_body, _ = request(opener, jmx_url, user, password)
    running = None
    if jmx_status == 200:
        try:
            attributes = {
                a.get("name"): a.get("value")
                for a in json.loads(jmx_body).get("attributes", [])
                if isinstance(a, dict)
            }
            running = attributes.get("RunningQueries")
        except ValueError:
            pass

    if running is not None:
        result.fact("jmx_running_queries", running)
        if len(queries) == 0 and isinstance(running, (int, float)) and running > 0:
            result.fail(
                "V1-4/H-09",
                "SILENT FILTERING: /v1/query returned 0 but JMX RunningQueries={}. "
                "tms-svc likely lacks queries:view in rules.json.".format(running),
            )
        elif len(queries) == 0:
            # Both sources agree the cluster is idle. That is self-consistent, but
            # it proves nothing about queries:view - an idle cluster and a fully
            # filtered response look identical. Do not let this pass as verified.
            result.warn(
                "V1-4",
                "INCONCLUSIVE: cluster is idle (0 queries, RunningQueries=0). "
                "queries:view permission is NOT verified by this run - an idle "
                "cluster and a silently filtered response are indistinguishable. "
                "Re-run while at least one query is executing.",
            )
        else:
            result.ok(
                "V1-4/H-09",
                "consistent (list={}, JMX RunningQueries={})".format(
                    len(queries), running
                ),
            )
    else:
        result.warn("V1-4/H-09", "could not read RunningQueries for cross-check")

    print("\nV1-5  Response size / latency")
    print(
        "       {} bytes for {} queries in {:.2f}s".format(
            size_bytes, len(queries), elapsed
        )
    )
    if len(queries) == 0:
        result.warn(
            "V1-5",
            "INCONCLUSIVE: {} bytes for an empty list says nothing about peak "
            "size. Re-run at peak concurrency before fixing the poll interval.".format(
                size_bytes
            ),
        )
    elif size_bytes > 2_000_000:
        result.warn(
            "V1-5",
            "{} bytes is large; raise poll interval or lower query_text_max_bytes".format(
                size_bytes
            ),
        )
    else:
        per_query = size_bytes // max(1, len(queries))
        result.ok(
            "V1-5",
            "{} bytes / {} queries (~{} B per query)".format(
                size_bytes, len(queries), per_query
            ),
        )
        print(
            "       projection: 200 concurrent queries ~= {:.1f} MB per poll".format(
                per_query * 200 / 1_000_000
            )
        )

    if queries:
        sample = queries[0]
        result.fact("basic_query_info_keys", sorted(sample.keys()))
        stats = sample.get("queryStats", {})
        if isinstance(stats, dict):
            result.fact("basic_query_stats_keys", sorted(stats.keys()))
        print("       BasicQueryInfo fields: {}".format(", ".join(sorted(sample.keys()))))


def check_v1_7_metrics(
    opener, base: str, user: str, password: str, result: Result
) -> None:
    """V1-7: can /metrics replace 7 individual MBean fetches with one call?"""
    print("\nV1-7  GET /metrics  (OpenMetrics; optimisation candidate)")
    status, body, _ = request(opener, base + "/metrics", user, password)
    if status == 403:
        result.warn("V1-7", "403 - same permission as /v1/jmx; skipping optimisation")
        return
    if status != 200:
        result.warn("V1-7", "status {} - skipping optimisation".format(status))
        return
    names = sorted(
        {
            line.split("{")[0].split(" ")[0]
            for line in body.splitlines()
            if line and not line.startswith("#")
        }
    )
    result.fact("openmetrics_sample_names", names[:80])
    result.ok("V1-7", "{} metric names exposed".format(len(names)))
    print("       first 15: {}".format(", ".join(names[:15])))
    print("       -> map these to the H-03..H-07 MBeans to collapse 7 calls into 1")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coordinator", required=True, help="https://host:port")
    parser.add_argument("--user", default="tms-svc")
    parser.add_argument("--expected-workers", type=int, default=12)
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="skip TLS verification (internal CA not trusted on this host)",
    )
    parser.add_argument("--json-out", help="write collected facts to this path")
    args = parser.parse_args()

    password = os.environ.get("TMS_TRINO_PASSWORD")
    if not password:
        print(
            "ERROR: set TMS_TRINO_PASSWORD first, e.g.\n"
            "  read -rs TMS_TRINO_PASSWORD && export TMS_TRINO_PASSWORD",
            file=sys.stderr,
        )
        return 2

    base = args.coordinator.rstrip("/")
    opener = build_opener(verify_tls=not args.insecure)
    result = Result()

    print("=" * 72)
    print("TMS V1 connectivity verification")
    print("  coordinator : {}".format(base))
    print("  user        : {}".format(args.user))
    print("  TLS verify  : {}".format(not args.insecure))
    print("=" * 72)

    check_v1_1_info(opener, base, result)
    check_v1_2_3_jmx(opener, base, args.user, password, args.expected_workers, result)
    check_v1_4_5_queries(opener, base, args.user, password, result)
    check_v1_7_metrics(opener, base, args.user, password, result)

    print("\n" + "=" * 72)
    print(
        "PASS {}   WARN {}   FAIL {}".format(
            len(result.passed), len(result.warned), len(result.failed)
        )
    )
    if result.failed:
        print("\nFailed checks:")
        for check in result.failed:
            print("  - {}".format(check))
    print("=" * 72)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(result.facts, handle, indent=2, sort_keys=True, default=str)
        print("facts written to {}".format(args.json_out))

    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
