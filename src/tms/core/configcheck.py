"""Validate a TMS deployment's configuration before restarting anything.

The alternative to this is finding out from a service that will not start, or
worse from a service that starts and is quietly wrong. Both happened during the
first production deploy: a typo in `trino.user` left every JMX-backed health
test stuck on 401, and the console looked fine while reporting UNKNOWN.

This is also the answer to "can TMS manage the cluster list?" (DECISIONS.md
D-008): the pain there was human error editing config.yaml, and catching the
error before the restart addresses that without giving TMS a second source of
truth.

Checks are grouped:

  STATIC  - config parses, required values present, internally consistent.
            No network. Always runs.
  LIVE    - each coordinator answers, credentials work, the grants TMS needs
            are actually granted. Needs the Trino password; skipped with a
            notice when it is not available rather than failing.

Exit codes: 0 = usable, 1 = problems found, 2 = could not load config at all.

Python 3.9 compatible.
"""

import argparse
import os
import stat
import sys
from typing import Any, Callable, List, Optional, Tuple

from tms.core.config import ConfigError, load_config

DEFAULT_CONFIG_PATH = "config/config.yaml"

OK = "ok"
WARN = "warn"
FAIL = "fail"

_MARK = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}


class Report:
    def __init__(self, verbose: bool = False) -> None:
        self.rows: List[Tuple[str, str, str]] = []
        self.verbose = verbose

    def add(self, level: str, check: str, detail: str = "") -> None:
        self.rows.append((level, check, detail))
        if level != OK or self.verbose:
            line = "[{}] {}".format(_MARK[level], check)
            if detail:
                line += "\n         " + detail.replace("\n", "\n         ")
            print(line)

    def count(self, level: str) -> int:
        return sum(1 for row in self.rows if row[0] == level)

    def summary(self) -> int:
        print("\n{} checks — {} ok, {} warning(s), {} failure(s)".format(
            len(self.rows), self.count(OK), self.count(WARN), self.count(FAIL)))
        if self.count(FAIL):
            print("설정을 고치기 전에는 서비스를 재시작하지 마라.")
            return 1
        if self.count(WARN):
            print("치명적이지는 않지만 확인할 항목이 있다.")
        else:
            print("이상 없음.")
        return 0


# ------------------------------------------------------------------- static

def check_secret_file(report: Report, config_path: str) -> None:
    """A 0600 file owned by someone else is the classic first-deploy failure."""
    secret_path = os.path.join(os.path.dirname(config_path) or ".", "config.secret.yaml")
    if not os.path.exists(secret_path):
        report.add(OK, "config.secret.yaml", "없음 - 시크릿을 환경변수로 공급 중인 것으로 본다")
        return
    if not os.access(secret_path, os.R_OK):
        report.add(FAIL, "config.secret.yaml 읽기 권한",
                   "{} 를 읽을 수 없다. 서비스 계정 소유인지 확인하라.".format(secret_path))
        return
    mode = stat.S_IMODE(os.stat(secret_path).st_mode)
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        report.add(WARN, "config.secret.yaml 권한",
                   "mode {:o} — 자격증명 파일이다. chmod 600 을 권한다.".format(mode))
    else:
        report.add(OK, "config.secret.yaml 권한", "mode {:o}".format(mode))


def check_clusters(report: Report, config) -> None:
    if not config.clusters:
        report.add(FAIL, "클러스터", "정의된 클러스터가 없다")
        return
    report.add(OK, "클러스터", "{}개: {}".format(
        len(config.clusters), ", ".join(c.name for c in config.clusters)))

    for cluster in config.clusters:
        if not cluster.coordinator_url.startswith("https://"):
            report.add(
                FAIL, "{}: coordinator_url".format(cluster.name),
                "{} — basic auth 는 HTTPS 에서만 동작한다. HTTP 로는 Trino 가 "
                "401 'Password not allowed for insecure authentication' 을 낸다."
                .format(cluster.coordinator_url))
        else:
            report.add(OK, "{}: coordinator_url".format(cluster.name),
                       cluster.coordinator_url)

        if cluster.expected_workers <= 0:
            report.add(WARN, "{}: expected_workers".format(cluster.name),
                       "{} — H-03(워커 등록 수)이 의미 있는 판정을 못 한다"
                       .format(cluster.expected_workers))


def check_portal(report: Report, config) -> None:
    users = config.portal.local_users
    if not users:
        report.add(WARN, "portal.local_users",
                   "비어 있다 — 웹 UI 가 마운트되지 않고 /api/v1/login 도 거부된다. "
                   "화면이 필요하면 계정을 최소 1개 만들어라.")
        return
    report.add(OK, "portal.local_users", "{}개 계정: {}".format(
        len(users), ", ".join(sorted(users))))

    if not config.portal.session_secret.reveal():
        report.add(FAIL, "portal.session_secret",
                   "로컬 계정이 있는데 세션 비밀키가 없다 — tms-api 가 기동에 실패한다. "
                   "TMS_SESSION_SECRET 또는 portal.session_secret 을 설정하라.")
    else:
        report.add(OK, "portal.session_secret", "설정됨 (전 인스턴스 동일해야 한다)")

    temporary = [name for name, spec in users.items()
                 if isinstance(spec, dict) and spec.get("must_change_password")]
    if temporary:
        report.add(WARN, "임시 비밀번호 계정",
                   "{} — 최초 로그인 후 변경하고, 응답의 새 해시를 "
                   "config.secret.yaml 에 반영해야 재시작 후에도 유지된다."
                   .format(", ".join(sorted(temporary))))


def check_deeplinks(report: Report, config) -> None:
    links = config.deeplinks
    missing = []
    for label, value in (
        ("log.template", links.log_template),
        ("query_history.query_url_template", links.query_history_url_template),
        ("grafana.cluster_dashboard", links.grafana_cluster_dashboard),
        ("superset_url", links.superset_url),
    ):
        if not value:
            missing.append(label)
    if missing:
        report.add(WARN, "딥링크",
                   "미설정: {} — 해당 링크는 렌더링되지 않는다(의도된 동작)."
                   .format(", ".join(missing)))
    else:
        report.add(OK, "딥링크", "전부 설정됨")

    for label, template, needed in (
        ("log.template", links.log_template, ("{query}", "{from_ms}", "{to_ms}")),
        ("query_history.query_url_template", links.query_history_url_template,
         ("{query_id}",)),
        ("grafana.cluster_dashboard", links.grafana_cluster_dashboard, ("{cluster}",)),
    ):
        if not template:
            continue
        absent = [token for token in needed if token not in template]
        if absent:
            report.add(WARN, label,
                       "치환자 {} 가 없다 — 링크가 항상 같은 곳을 가리킨다"
                       .format(", ".join(absent)))


def check_intervals(report: Report, config) -> None:
    """Report the cadence. Do not re-validate it.

    `build_config` already rejects stale_threshold <= query_poll_interval, so a
    check for it here could never fire - and a check that cannot fire is worse
    than no check, because the list makes it look covered. It is covered, just
    earlier: load_config raises before this runs.
    """
    collector = config.collector
    report.add(OK, "collector 주기",
               "query {}s / jmx {}s / info {}s, stale {}s".format(
                   collector.query_poll_interval_seconds,
                   collector.jmx_poll_interval_seconds,
                   collector.info_poll_interval_seconds,
                   collector.stale_threshold_seconds))
    if collector.query_poll_interval_seconds < 5:
        report.add(WARN, "collector.query_poll_interval_seconds",
                   "{}s — 기본 5s 보다 짧다. NFR-PERF-03 예산을 다시 재지 않았다면 "
                   "코디네이터 부하가 예산을 넘을 수 있다."
                   .format(collector.query_poll_interval_seconds))


def check_gateway(report: Report, config) -> None:
    if not config.gateway.enabled:
        report.add(OK, "gateway", "비활성 — 헬스 테스트 H-08 은 카탈로그에서 제외된다")
    elif not config.gateway.base_url:
        report.add(FAIL, "gateway.base_url", "gateway.enabled 인데 base_url 이 비었다")
    else:
        report.add(OK, "gateway", config.gateway.base_url)


# --------------------------------------------------------------------- live

def check_live(report: Report, config, insecure: bool) -> None:
    password = (os.environ.get("TMS_TRINO_PASSWORD")
                or config.trino.password.reveal())
    if not password:
        report.add(WARN, "실접속 검사",
                   "Trino 비밀번호가 없어 건너뛴다. TMS_TRINO_PASSWORD 를 설정하면 "
                   "계정·권한까지 확인한다 — 오타는 여기서만 잡힌다.")
        return
    try:
        import httpx
    except ImportError:
        report.add(WARN, "실접속 검사", "httpx 미설치로 건너뛴다")
        return

    verify = not (insecure or not config.trino.verify_tls)
    auth = (config.trino.user, password)
    with httpx.Client(verify=verify, timeout=10.0) as client:
        for cluster in config.clusters:
            base = cluster.coordinator_url.rstrip("/")
            label = cluster.name

            # /v1/info is PUBLIC - separates "unreachable" from "unauthorised".
            try:
                info = client.get(base + "/v1/info")
            except Exception as exc:  # noqa: BLE001
                report.add(FAIL, "{}: 접속".format(label),
                           "{} — 주소·방화벽·인증서를 확인하라 (내부 CA 는 "
                           "SSL_CERT_FILE 로 지정)".format(exc))
                continue
            if info.status_code != 200:
                report.add(FAIL, "{}: /v1/info".format(label),
                           "HTTP {}".format(info.status_code))
                continue
            report.add(OK, "{}: 접속".format(label), "/v1/info 200")

            # JMX needs system_information:read.
            jmx = client.get(base + "/v1/jmx/mbean/java.lang:type=Memory", auth=auth)
            if jmx.status_code == 401:
                report.add(FAIL, "{}: 인증".format(label),
                           "401 — trino.user('{}') 또는 비밀번호가 틀렸다. "
                           "계정명 오타를 먼저 의심하라.".format(config.trino.user))
                continue
            if jmx.status_code == 403:
                report.add(FAIL, "{}: JMX 권한".format(label),
                           "403 — rules.json 에 system_information: read 가 없다. "
                           "H-03~H-07 이 전부 UNKNOWN 이 된다.")
            elif jmx.status_code != 200:
                report.add(FAIL, "{}: JMX".format(label),
                           "HTTP {}".format(jmx.status_code))
            else:
                report.add(OK, "{}: 인증 + JMX 권한".format(label), "200")

            # queries:view. A denial here is silent - an empty list, not a 403.
            queries = client.get(base + "/v1/query", auth=auth)
            if queries.status_code == 403:
                report.add(FAIL, "{}: 쿼리 조회 권한".format(label),
                           "403 — rules.json 에 queries: view 가 없다")
            elif queries.status_code != 200:
                report.add(WARN, "{}: 쿼리 조회".format(label),
                           "HTTP {}".format(queries.status_code))
            else:
                report.add(OK, "{}: 쿼리 조회".format(label),
                           "{}건".format(len(queries.json())))


def check_database(report: Report, config) -> None:
    url = config.database_url.reveal()
    if not url:
        report.add(FAIL, "database.url", "설정되지 않았다")
        return
    try:
        import psycopg
    except ImportError:
        report.add(WARN, "DB 접속", "psycopg 미설치로 건너뛴다")
        return
    try:
        with psycopg.connect(url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name IN ('audit_action','collector_snapshot',"
                    "'health_event','health_test_override')")
                found = cur.fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        report.add(FAIL, "DB 접속", str(exc).strip().splitlines()[0])
        return
    if found < 4:
        report.add(FAIL, "DB 스키마",
                   "테이블 {}/4 개만 있다 — migrations/001_init.sql 을 적용하라"
                   .format(found))
    else:
        report.add(OK, "DB", "접속 및 테이블 4개 확인")


# --------------------------------------------------------------------- main

def run(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="재시작 전에 TMS 설정을 검증한다.")
    parser.add_argument("--config", default=os.environ.get("TMS_CONFIG",
                                                           DEFAULT_CONFIG_PATH))
    parser.add_argument("--offline", action="store_true",
                        help="네트워크 검사(Trino·DB)를 건너뛴다")
    parser.add_argument("--insecure", action="store_true",
                        help="TLS 검증 생략 (내부 CA 미신뢰 시)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="통과한 검사도 전부 출력")
    args = parser.parse_args(argv)

    print("TMS 설정 검증 — {}\n".format(args.config))
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print("[{}] 설정 로드\n         {}".format(_MARK[FAIL], exc))
        return 2
    except FileNotFoundError:
        print("[{}] 설정 로드\n         {} 를 찾을 수 없다".format(_MARK[FAIL], args.config))
        return 2
    except Exception as exc:  # noqa: BLE001
        print("[{}] 설정 로드\n         {}".format(_MARK[FAIL], exc))
        return 2

    report = Report(verbose=args.verbose)
    report.add(OK, "설정 로드", args.config)

    static_checks: List[Callable[..., Any]] = [
        lambda: check_secret_file(report, args.config),
        lambda: check_clusters(report, config),
        lambda: check_portal(report, config),
        lambda: check_intervals(report, config),
        lambda: check_gateway(report, config),
        lambda: check_deeplinks(report, config),
    ]
    for check in static_checks:
        check()

    if args.offline:
        report.add(WARN, "실접속 검사", "--offline 로 건너뛰었다")
    else:
        check_database(report, config)
        check_live(report, config, args.insecure)

    return report.summary()


if __name__ == "__main__":
    sys.exit(run())
