# API_R1 — R1 엔드포인트 명세

> **Bolt 1 산출물 (U8)** · 작성 2026-08-06 · 상태: **인간 승인 대기**
> 기준: `ARCHITECTURE.md`, `HEALTH_TESTS.md`, `AUDIT_MODEL.md`

---

## 0. 공통 규약

| 항목 | 값 |
|---|---|
| 베이스 | `/api/v1` |
| 인증 | 세션 쿠키 (FR-PT-01/03). 미인증 → `401` |
| 권한 부족 | `403` + 감사 기록(쓰기 라우트에 한함) |
| 콘텐츠 | `application/json` |
| 시각 | 전부 **ISO 8601 UTC** (`2026-08-06T04:12:00Z`) |

### 0-1. 오류 응답 (전 엔드포인트 공통)

```json
{
  "error": {
    "code": "REASON_REQUIRED",
    "message": "reason is required for write actions",
    "request_id": "0f2c…"
  }
}
```

| code | HTTP | 의미 |
|---|---|---|
| `UNAUTHENTICATED` | 401 | 세션 없음/만료 |
| `FORBIDDEN` | 403 | 역할 부족 |
| `REASON_REQUIRED` | 400 | `reason` 누락 또는 공백 (`AU2`) |
| `NOT_FOUND` | 404 | 대상 없음 |
| `UPSTREAM_UNAVAILABLE` | 503 | 코디네이터/Gateway 도달 불가 |
| `AUDIT_UNAVAILABLE` | 503 | **감사 저장소 불가 → 쓰기 거부** (`AU1`) |

### 0-2. Stale 표기 (전 조회 응답 공통)

collector 스냅샷을 읽는 모든 응답은 아래를 포함한다.

```json
{
  "collected_at": "2026-08-06T04:12:00Z",
  "stale": false,
  "data": { }
}
```

> `stale: true` 이면 UI는 반드시 "N초 전 데이터" 배지를 표시한다. 서버가 판단하고 클라이언트가 재계산하지 않는다.

---

## 1. FR-PORTAL

### `GET /api/v1/me`
현재 사용자와 역할. 프런트가 화면 노출을 결정하는 근거(FR-PT-04).

```json
{ "user": "syhcho", "roles": ["operator"], "session_expires_at": "…" }
```

### `GET /api/v1/links`
링크 허브(FR-PT-02). **설정 파일에서 읽는다.** 값이 비어 있는 항목은 응답에서 제외한다 → UI에 죽은 링크가 뜨지 않는다.

```json
{
  "links": [
    { "id": "grafana",       "label": "Grafana",        "url": "https://…" },
    { "id": "trino_ui_a",    "label": "Trino UI (A)",   "url": "https://…" },
    { "id": "gateway_ui",    "label": "Trino Gateway",  "url": "https://…" },
    { "id": "superset",      "label": "Superset",       "url": "https://…" },
    { "id": "query_history", "label": "쿼리 히스토리",   "url": "https://…" }
  ]
}
```

> `query_history` 는 D-001로 분리된 기존 프로젝트다. **완료 쿼리 동선의 유일한 R1 진입점**이므로 우선 노출한다.

---

## 2. FR-QUERY-LIVE

### `GET /api/v1/clusters/{cluster}/queries`
실행 중 쿼리 목록 (FR-QL-01/02/03). collector 스냅샷에서 읽는다 — **요청마다 코디네이터를 때리지 않는다**(A3).

**쿼리 파라미터**: `state`(복수), `user`, `min_elapsed_seconds`, `resource_group`, `limit`(기본 100), `cursor`

```json
{
  "collected_at": "…", "stale": false,
  "data": {
    "summary": { "running": 42, "queued": 7, "long_running": 3 },
    "queries": [
      {
        "query_id": "20260806_041200_00042_abcde",
        "state": "RUNNING",
        "user": "analyst01",
        "source": "superset",
        "resource_group_id": ["global", "bi"],
        "elapsed_ms": 184000,
        "queued_ms": 1200,
        "total_cpu_ms": 940000,
        "peak_user_memory_bytes": 8589934592,
        "progress_percentage": 62.5,
        "running_drivers": 120,
        "queued_drivers": 8,
        "fully_blocked": false,
        "query_preview": "SELECT … (4KB로 절단)",
        "query_truncated": true,
        "long_running": true,
        "links": {
          "logs": "https://loki…",
          "history": "https://<기존-시스템>/query/20260806_041200_00042_abcde"
        }
      }
    ],
    "next_cursor": null
  }
}
```

**필드 출처**: 전부 `BasicQueryInfo` / `BasicQueryStats` (477 소스 확인, `ARCHITECTURE.md` §3-1)
**`long_running`**: `elapsed_ms > 설정 임계값`(FR-QL-03). 임계값은 클러스터별 설정
**`links.history`**: `query_history.query_url_template` 이 설정된 경우에만 포함 (§7 `ARCHITECTURE.md`)

### `GET /api/v1/clusters/{cluster}/queries/{query_id}`
상세. **SQL 전문이 필요하므로 이 요청만 코디네이터를 직접 조회한다** (`GET /v1/query/{queryId}`, §T1-5). 사용자가 명시적으로 연 경우에만 발생하므로 폴링 부하와 무관하다.

### `POST /api/v1/clusters/{cluster}/queries/{query_id}/kill`
쿼리 kill (FR-QL-04). **operator 이상.**

```json
{ "reason": "리소스 그룹 고갈 유발. 사용자와 협의 완료." }
```

| 조건 | 응답 |
|---|---|
| `reason` 누락/공백 | `400 REASON_REQUIRED` |
| 역할 부족 | `403` + 감사 기록(FAILURE) |
| 감사 저장소 불가 | `503 AUDIT_UNAVAILABLE` — **kill을 시도조차 하지 않는다** |
| 성공 | `200` + `{ "killed": true, "request_id": "…" }` |

**동작**: `PUT /v1/query/{query_id}/killed` 호출. 본문 = `"Killed by TMS. actor={actor}, reason={reason}, request_id={request_id}"`
**⚠️ UI 필수 고지**: "이 사유는 쿼리를 실행한 사용자에게 오류 메시지로 표시됩니다." (`AUDIT_MODEL.md` §4-2)
**재시도 금지**: 쓰기는 재시도하지 않는다 (`ARCHITECTURE.md` §4-1)

---

## 3. FR-CLUSTER-HEALTH

### `GET /api/v1/clusters`
클러스터 목록 + roll-up 상태.

```json
{
  "collected_at": "…", "stale": false,
  "data": [
    { "name": "prod-a", "rollup_state": "GOOD",       "bad": 0, "concerning": 0, "unknown": 0 },
    { "name": "prod-b", "rollup_state": "CONCERNING", "bad": 0, "concerning": 1, "unknown": 0 }
  ]
}
```

### `GET /api/v1/clusters/{cluster}/health`
개별 테스트 결과 (FR-CH-01/02).

```json
{
  "collected_at": "…", "stale": false,
  "data": {
    "rollup_state": "CONCERNING",
    "rollup_enabled": true,
    "tests": [
      {
        "id": "H-03", "name": "워커 등록 수",
        "enabled": true, "state": "CONCERNING",
        "observed_value": 10, "threshold": 12,
        "advice": "워커 12대 중 10대만 등록되어 있다. 미조인 워커의 systemd 상태와 discovery 설정을 확인하라. 클러스터 용량이 83%로 떨어져 있다.",
        "links": { "logs": "https://loki…" }
      }
    ]
  }
}
```

> **불변조건**: `state ∈ {BAD, CONCERNING}` 이면 `advice` 는 비어 있을 수 없다 (`HEALTH_TESTS.md` §7).

### `GET /api/v1/clusters/{cluster}/health/events`
상태 전이 이력 (FR-CH-07). 파라미터: `from`, `to`, `test_id`, `limit`, `cursor`

### `PATCH /api/v1/clusters/{cluster}/health/tests/{test_id}` — **admin**
활성/비활성(FR-CH-03), 임계값 변경(FR-CH-05).

```json
{ "enabled": false, "reason": "H-05 오탐 조사 중. 2026-08-13 재활성화 예정." }
```

감사: `HEALTH_TEST_TOGGLE` / `HEALTH_THRESHOLD_CHANGE`

### `PATCH /api/v1/clusters/{cluster}/health/rollup` — **admin**
roll-up 별도 비활성화(FR-CH-04). 감사: `HEALTH_ROLLUP_TOGGLE`

---

## 4. FR-AUDIT-ACTION

### `GET /api/v1/audit` — **operator 이상**
파라미터: `from`, `to`, `actor`, `action_type`, `target_kind`, `target_id`, `outcome`, `limit`, `cursor` (keyset 페이징)

### `GET /api/v1/audit/export` — **admin**
CSV 반환 (FR-AA-05). **이 호출 자체가 `AUDIT_EXPORT` 로 감사된다.**
`reason` 을 쿼리 파라미터로 필수 요구한다 — 쓰기가 아니지만 감사 대상 액션이기 때문이다.

---

## 5. FR-LOG-DEEPLINK

**독립 엔드포인트를 만들지 않는다.** 딥링크는 위 응답들의 `links` 필드로 함께 내려간다 — 별도 왕복이 생기지 않고, 링크 생성 규칙이 서버 한 곳에만 존재한다.

| 컨텍스트 | 필드 | FR |
|---|---|---|
| 실행 중 쿼리 | `queries[].links.logs` | FR-LD-01 |
| 헬스 테스트 이상 | `tests[].links.logs` | FR-LD-03 |
| 노드 (R3) | — | FR-LD-02 (R3 FR-FLEET에서) |

> **FR-LD-02(노드 상세 → 로그)는 R1에 노드 상세 화면이 없어 진입점이 없다.** R3 FR-FLEET로 이월한다. **요구사항 축소이므로 인간 승인 대상이다.**

---

## 6. 운영 엔드포인트

| 경로 | 용도 |
|---|---|
| `GET /health` | TMS 자체 헬스 (NFR-OPS-01). 인증 불요 |
| `GET /ready` | DB 연결 + 설정 로드 완료 여부 |
| `GET /metrics` | Prometheus. TMS 자체 지표 + **collector 폴링 성공률/지연** |

> `/metrics` 에 **collector가 각 코디네이터를 마지막으로 성공 수집한 시각**을 노출한다. `sre-agent`가 "TMS가 눈이 멀었다"를 알림으로 잡을 수 있게 하기 위함이다 (Datadog "No Data" 패턴, `TEAMS.md` §2-6).

---

## 7. R1 API 요약

| 메서드 | 경로 | 역할 | FR |
|---|---|---|---|
| GET | `/api/v1/me` | 인증됨 | FR-PT-01/04 |
| GET | `/api/v1/links` | 인증됨 | FR-PT-02 |
| GET | `/api/v1/clusters` | viewer | FR-CH-01 |
| GET | `/api/v1/clusters/{c}/health` | viewer | FR-CH-01/02 |
| GET | `/api/v1/clusters/{c}/health/events` | viewer | FR-CH-07 |
| **PATCH** | `/api/v1/clusters/{c}/health/tests/{id}` | **admin** | FR-CH-03/05 |
| **PATCH** | `/api/v1/clusters/{c}/health/rollup` | **admin** | FR-CH-04 |
| GET | `/api/v1/clusters/{c}/queries` | viewer | FR-QL-01/02/03 |
| GET | `/api/v1/clusters/{c}/queries/{id}` | viewer | FR-QL-01 |
| **POST** | `/api/v1/clusters/{c}/queries/{id}/kill` | **operator** | FR-QL-04 |
| GET | `/api/v1/audit` | operator | FR-AA-05 |
| GET | `/api/v1/audit/export` | **admin** | FR-AA-05 |

**쓰기 라우트는 4개뿐이다** (PATCH 2 + POST 1 + export 1). 전부 `reason` 필수 + 감사 대상이다. **이 표에 없는 쓰기 API를 R1에 추가하지 않는다.**
