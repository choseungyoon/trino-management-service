"""End-to-end smoke test: real FastAPI app + real PostgreSQL, stub Trino."""
import sys, os
sys.path.insert(0, "/Users/seungyuncho/Product/trino-management-service/src")

import asyncio, httpx
from tms.core.config import build_config
from tms.core.passwords import hash_password
from tms.core.audit import AuditGuard
from tms.core.audit_postgres import PostgresAuditRepository
from tms.collector.postgres import PostgresSnapshotRepository
from tms.collector.snapshot import Snapshot, KIND_QUERIES, KIND_HEALTH, utcnow
from tms.api.services import TmsService
from tms.api.main import create_app

DSN = "postgresql://tms_app:app_pw@127.0.0.1:5433/tms"
TEMP_PW = "Temp-Pass-2026!"

class StubTrino:
    def __init__(self): self.killed = []
    def kill_query(self, qid, msg): self.killed.append((qid, msg))
    def get_query(self, qid): return {"queryId": qid, "query": "SELECT 1"}

raw = {
    "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid:8443",
                  "expected_workers": 12}],
    "trino": {"user": "tms-svc", "password": "pw"},
    "database": {"url": DSN},
    "deeplinks": {"log": {"template": "https://loki.invalid/?q={query}&from={from_ms}&to={to_ms}"},
                  "query_history": {"query_url_template": "https://hist.invalid/query/{query_id}",
                                    "home_url": "https://hist.invalid/"}},
    "portal": {
        "session_secret": "test-session-secret-value",
        "local_users": {"syhcho": {"password_hash": hash_password(TEMP_PW, iterations=1000),
                                   "roles": ["admin"], "must_change_password": True}},
    },
}
config = build_config(raw)

snapshots = PostgresSnapshotRepository(DSN)
audit_repo = PostgresAuditRepository(DSN)
stub = StubTrino()
service = TmsService(config=config, repository=snapshots,
                     audit_guard=AuditGuard(audit_repo), audit_repository=audit_repo,
                     trino_clients={"prod-a": stub})

snapshots.save(Snapshot("prod-a", KIND_QUERIES, utcnow(), payload={
    "queries": [{"query_id": "20260806_1_abc", "state": "RUNNING", "user": "analyst",
                 "elapsed_ms": 400000, "resource_group_id": ["global", "bi"],
                 "query_preview": "SELECT 1", "long_running": True}],
    "summary": {"running": 1, "queued": 0, "total": 1}}))
snapshots.save(Snapshot("prod-a", KIND_HEALTH, utcnow(), payload={
    "rollup_state": "CONCERNING", "rollup_enabled": True,
    "tests": [{"id": "H-03", "state": "CONCERNING", "advice": "워커 2대 미조인"}]}))

app = create_app(config=config, service=service)

def show(label, r):
    body = r.text[:220].replace("\n", " ")
    print("  {:<44} {} {}".format(label, r.status_code, body))

fails = []
def check(label, cond):
    print("  {} {}".format("PASS" if cond else "FAIL", label))
    if not cond: fails.append(label)

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://tms.test") as c:
      print("\n=== 인증 ===")
      r = await c.get("/api/v1/me");                       show("GET /me (미인증)", r)
      check("미인증은 401", r.status_code == 401)

      r = await c.post("/api/v1/login", json={"username": "syhcho", "password": "wrong"})
      show("login (오답)", r); check("오답은 401", r.status_code == 401)

      r = await c.post("/api/v1/login", json={"username": "syhcho", "password": TEMP_PW})
      show("login (정답, 임시PW)", r)
      check("로그인 성공", r.status_code == 200 and r.json().get("must_change_password") is True)
      check("세션 쿠키 발급", "tms_session" in c.cookies)

      print("\n=== 임시 비밀번호 게이트 ===")
      r = await c.get("/api/v1/clusters/prod-a/queries"); show("queries (변경 전)", r)
      check("임시PW로 다른 API 403", r.status_code == 403)

      r = await c.put("/api/v1/password", json={"current_password": TEMP_PW, "new_password": "weak"})
      show("password 변경 (약한 PW)", r); check("약한 PW 거부 400", r.status_code == 400)

      r = await c.put("/api/v1/password", json={"current_password": TEMP_PW,
                                          "new_password": "Brand-New-Pass-9!"})
      show("password 변경", r)
      check("변경 성공", r.status_code == 200 and r.json().get("changed") is True)
      check("새 해시 반환", r.json().get("password_hash", "").startswith("pbkdf2_sha256$"))

      print("\n=== 조회 ===")
      r = await c.get("/api/v1/me"); show("GET /me", r)
      check("capabilities 포함", "kill_query" in r.json().get("capabilities", []))
      r = await c.get("/api/v1/links"); show("GET /links", r)
      check("query_history 링크", any(l["id"] == "query_history" for l in r.json()["links"]))

      r = await c.get("/api/v1/clusters"); show("GET /clusters", r)
      check("rollup 반환", r.json()["data"][0]["rollup_state"] == "CONCERNING")

      r = await c.get("/api/v1/clusters/prod-a/health"); show("GET /health", r)
      check("advice 포함", bool(r.json()["data"]["tests"][0]["advice"]))

      r = await c.get("/api/v1/clusters/prod-a/queries"); show("GET /queries", r)
      data = r.json()
      check("stale=False", data["stale"] is False)
      check("딥링크 부착", "history" in data["data"]["queries"][0]["links"])

      r = await c.get("/api/v1/clusters/nope/queries"); show("GET /queries (없는 클러스터)", r)
      check("404", r.status_code == 404)

      print("\n=== 쓰기 ===")
      r = await c.post("/api/v1/clusters/prod-a/queries/20260806_1_abc/kill", json={"reason": "  "})
      show("kill (빈 reason)", r)
      check("빈 reason 400", r.status_code == 400 and r.json()["error"]["code"] == "REASON_REQUIRED")
      check("kill 미실행", stub.killed == [])

      r = await c.post("/api/v1/clusters/prod-a/queries/20260806_1_abc/kill",
                 json={"reason": "리소스 그룹 고갈 유발"})
      show("kill", r); check("kill 성공", r.status_code == 200)
      check("사유가 Trino 로 전달", stub.killed and "리소스 그룹 고갈 유발" in stub.killed[0][1])
      check("actor 포함", stub.killed and "actor=syhcho" in stub.killed[0][1])

      print("\n=== 감사 ===")
      r = await c.get("/api/v1/audit?limit=5"); show("GET /audit", r)
      recs = r.json()["records"]
      check("kill 이 감사에 기록", any(x["action_type"] == "QUERY_KILL" and x["actor"] == "syhcho"
                                       and x["outcome"] == "SUCCESS" for x in recs))
      r = await c.get("/api/v1/audit/export?reason=분기 감사 제출"); show("GET /audit/export", r)
      check("export 성공", r.status_code == 200)
      r = await c.get("/api/v1/audit?action_type=AUDIT_EXPORT")
      check("export 자체가 감사됨", r.json()["count"] >= 1)

      print("\n=== 로그아웃 ===")
      r = await c.post("/api/v1/logout"); show("logout", r)
      c.cookies.clear()
      r = await c.get("/api/v1/me"); check("로그아웃 후 401", r.status_code == 401)

asyncio.run(main())

print("\n" + "=" * 60)
print("실패 {}건".format(len(fails)))
for f in fails: print("  -", f)
sys.exit(1 if fails else 0)
