# ARCHITECTURE — R1

> **Bolt 1 산출물 (U1, U2, U4, U6, U7)** · 작성 2026-08-06 · 상태: **인간 승인 대기**
> 모든 Trino/Gateway 연동점은 `TRINO_VERIFIED.md` 항목을 인용한다. 인용 없는 API는 쓰지 않는다.

---

## 1. 설계 원칙 (R1 한정, 위반 시 반려)

| # | 원칙 | 근거 |
|---|---|---|
| **A1** | **TMS는 Trino에 SQL 쿼리를 제출하지 않는다.** REST(`/v1/*`)와 JMX-over-HTTP(`/v1/jmx/*`)만 쓴다 | 아래 §1-1 |
| **A2** | **EventListener를 만들지 않는다** | 기존 히스토리 프로젝트가 이미 코디네이터에서 돌고 있다 (D-001). 두 개면 코디네이터 부하가 두 배 |
| **A3** | 코디네이터를 폴링하는 주체는 **프로세스 전체에 단 하나**다 | NFR-PERF-03. API를 스케일아웃해도 Trino 부하가 늘지 않아야 한다 |
| **A4** | 외부 의존성은 **전부 선택적**이다. 죽으면 해당 영역만 `unknown`, 나머지는 동작 | NFR-DEGRADE |
| **A5** | 쓰기 API는 예외 없이 `reason` 필수 + 감사 기록 | CLAUDE.md 절대 규칙 3 |

### 1-1. A1을 강하게 못 박는 이유

`system.runtime.queries` / `system.runtime.nodes` 는 조회에 **SQL 쿼리 제출이 필요하다.** 5초 주기로 폴링하면:

1. **코디네이터에 쿼리 슬롯·리소스 그룹 슬롯을 소비한다** — 관리 도구가 관리 대상의 용량을 먹는다
2. **하루 약 17,000건의 TMS 쿼리가 기존 히스토리 시스템에 쌓인다** — 남의 데이터를 오염시킨다. 워크로드 분석(`WORKLOAD_PROFILE.md`)의 모수가 망가진다
3. OPA 인가 호출도 그만큼 발생한다

→ **R1에서 필요한 모든 데이터는 REST/JMX로 얻을 수 있다** (§3 표 참조). SQL 경로는 쓰지 않는다.
→ `system.runtime.*` 이 꼭 필요해지는 시점은 R3 FR-FLEET이며, 그때 **저빈도 조회**로 다시 판단한다.

---

## 2. 컴포넌트

```
                     [ 운영자 브라우저 ]
                             │ HTTPS
                             ▼
                    ┌─────────────────┐
                    │  tms-api        │  FastAPI / systemd
                    │  (stateless,    │  인증·인가·감사·조회 API
                    │   N개 확장 가능) │  쓰기 액션 실행
                    └────┬───────┬────┘
                         │       │
              읽기       │       │ 쓰기(kill 등) — 직접 호출
                         ▼       │
                  ┌────────────┐ │
                  │ PostgreSQL │ │   감사로그, 헬스 이벤트,
                  │  (TMS 전용)│ │   최신 스냅샷
                  └────────────┘ │
                         ▲       │
                   스냅샷 기록    │
                         │       │
                  ┌──────┴─────┐ │
                  │tms-collector│ │  ★ 단일 인스턴스
                  │ (polling)   │ │
                  └──────┬──────┘ │
                         │        │
        ┌────────────────┼────────┘
        ▼                ▼
  [ Trino 코디네이터 ]  [ Gateway ]      [ Grafana / Loki / 기존 히스토리 ]
   REST + JMX-over-HTTP   선택적 어댑터        링크만 (호출 없음)
```

### 2-1. 왜 `tms-collector`를 별도 systemd 유닛으로 분리하는가

**대안**: `tms-api` 안에서 asyncio 백그라운드 태스크로 폴링.
**문제**: uvicorn 워커를 N개로 늘리면 **폴링도 N배가 된다.** 관리 도구를 스케일아웃했더니 관리 대상이 죽는 구조다. NFR-PERF-03 위반이 조용히 발생한다.

**채택**: `tms-collector.service` 단일 인스턴스가 유일한 폴링 주체(A3). `tms-api`는 PostgreSQL의 스냅샷만 읽으므로 자유롭게 확장된다.

| 유닛 | 인스턴스 | 역할 |
|---|---|---|
| `tms-api.service` | N (확장 가능) | HTTP API, 인증/인가, 감사 기록, **쓰기 액션 직접 실행** |
| `tms-collector.service` | **1 (고정)** | Trino 폴링 → PostgreSQL 스냅샷 기록, 헬스 판정, 상태 전이 이벤트 기록 |

> **쓰기(kill 등)는 왜 collector를 거치지 않는가**: 사용자 액션은 즉시성이 필요하고 폴링 주기에 묶이면 안 된다. 또한 감사 기록은 요청 컨텍스트(누가/왜)를 아는 `tms-api`에서만 정확히 남길 수 있다.
> **collector 단일 인스턴스의 SPOF 성격**: collector가 죽으면 스냅샷이 낡는다. → **스냅샷에 `collected_at`을 반드시 포함하고, UI는 임계(기본 30초) 초과 시 데이터를 "stale"로 표시한다.** 낡은 데이터를 현재 상태로 보여주지 않는 것이 NFR-DEGRADE의 요구다.

---

## 3. 데이터 소스 (전부 Bolt 0 검증 완료)

| FR | 필요 데이터 | 경로 | 검증 |
|---|---|---|---|
| FR-QUERY-LIVE | 실행 중 쿼리 목록 | `GET /v1/query?state=…` → `List<BasicQueryInfo>` | §T1-5 |
| FR-QUERY-LIVE | 쿼리 kill | `PUT /v1/query/{queryId}/killed` (본문=사유) | §T1-5 |
| FR-CLUSTER-HEALTH | 코디네이터 생존/기동 | `GET /v1/info` (PUBLIC) | §T1-2 |
| FR-CLUSTER-HEALTH | 노드 상태 | `GET /v1/info/state` (PUBLIC) | §T1-2 |
| FR-CLUSTER-HEALTH | 활성 노드 수, 힙, 쿼리 지표 | `GET /v1/jmx/mbean/{objectName}` | §T1-7 |
| FR-AUDIT-ACTION | — | TMS PostgreSQL | — |
| FR-LOG-DEEPLINK | — | URL 생성만 (외부 호출 없음) | — |
| (선택) 클러스터 목록 | 백엔드 목록 | Gateway `GET /gateway/backend/all` | §T2-3 |

### 3-1. `GET /v1/query` 사용 규약

**검증된 사실** (`TRINO_VERIFIED.md` §T1-5 + 477 소스)

- 시그니처: `@QueryParam("state") Set<String> stateFilters` — **복수 지정 가능**
- 유효한 `state` 값 = `QueryState` enum (477 소스 확인):
  `QUEUED`, `WAITING_FOR_RESOURCES`, `DISPATCHING`, `PLANNING`, `STARTING`, `RUNNING`, `FINISHING`, `FINISHED`, `FAILED`
- 응답 `BasicQueryInfo` 필드: `queryId`, `session`, **`resourceGroupId`**, `state`, `scheduled`, `self`, `query`, `updateType`, `preparedQuery`, `queryStats`, `errorType`, `errorCode`, `queryType`, `retryPolicy`
- `BasicQueryStats` 주요 필드: `createTime`, `endTime`, `queuedTime`, **`elapsedTime`**, `executionTime`, **`totalCpuTime`**, **`peakUserMemoryReservation`**, `userMemoryReservation`, `totalDrivers`/`runningDrivers`/`queuedDrivers`, `physicalInputDataSize`, **`progressPercentage`**, `runningPercentage`, `fullyBlocked`, `blockedReasons`

**collector가 보내는 요청 (확정)**

```
GET /v1/query?state=QUEUED&state=WAITING_FOR_RESOURCES&state=DISPATCHING
             &state=PLANNING&state=STARTING&state=RUNNING&state=FINISHING
```

> 종료 상태(`FINISHED`, `FAILED`)는 **요청하지 않는다.** 완료 쿼리는 기존 히스토리 프로젝트 소관이며(D-001), 코디네이터 메모리에 남아 있는 완료 쿼리까지 받으면 응답이 불필요하게 커진다.
> **⚠️ 응답 크기 주의**: `BasicQueryInfo.query` 는 SQL 전문이다. 동시 실행 쿼리가 많으면 응답이 수 MB가 될 수 있다. **collector는 SQL 텍스트를 목록 스냅샷에 저장할 때 상한(기본 4 KB)으로 자른다.** 전문이 필요하면 상세 조회 시점에 `GET /v1/query/{queryId}` 로 다시 가져온다.

### 3-2. FR-CLUSTER-HEALTH가 읽는 MBean (문서 확인된 이름만)

| 지표 | ObjectName:Attribute |
|---|---|
| **노드 수 (활성/비활성/drain/종료중)** | **`trino.node:name=CoordinatorNodeManager`** 의 `ActiveNodeCount`, `InactiveNodeCount`, `DrainingNodeCount`, `DrainedNodeCount`, `ShuttingDownNodeCount` ✅ 실환경 확인. **`ActiveNodeCount` 는 코디네이터를 포함한다(12워커→13)** |
| 힙 사용 | `java.lang:type=Memory:HeapMemoryUsage.used` |
| 실행 중 쿼리 | `trino.execution:name=QueryManager:RunningQueries` |
| 실패 쿼리(5분) | `trino.execution:name=QueryManager:FailedQueries.FiveMinute.Count` |
| 시작 쿼리(5분) | `trino.execution:name=QueryManager:StartedQueries.FiveMinute.Count` |
| 내부 실패(5분) | `trino.execution:name=QueryManager:InternalFailures.FiveMinute.Count` |
| OOM kill 누적 | `trino.memory:name=ClusterMemoryManager:QueriesKilledDueToOutOfMemory` |

> **워커 노드 개별 상태는 R1 범위 밖이다.** `ActiveCount` 와 설정상 기대치를 비교해 "워커 N대 중 M대 미조인"까지만 판정한다. 노드 단위 인벤토리는 R3 FR-FLEET 소관.
> **미해소 G-5/G-6 영향 없음** — 위 목록은 전부 477 공식 문서에 명시된 이름이다.

---

## 4. 외부 의존성과 다운 시 동작 (NFR-DEGRADE)

| 의존성 | 다운 시 TMS 동작 | 영향받는 FR | 영향 없는 FR |
|---|---|---|---|
| Trino 코디네이터 A | 클러스터 A만 `unknown`. B는 정상 표시 | FR-QUERY-LIVE(A), FR-CLUSTER-HEALTH(A) | 나머지 전부 |
| **OPA** (접근제어로 도입한 경우) | `MANAGEMENT_READ` 호출이 전부 실패 → JMX·쿼리 목록 `unknown`. **단 `/v1/info`는 PUBLIC이라 계속 동작** → **H-01/H-02(코디네이터 생존·기동)는 살아남는다** | FR-QUERY-LIVE, H-03~H-07 | **H-01, H-02**, FR-PORTAL, FR-AUDIT-ACTION |
| **모든** 코디네이터 | 쿼리/헬스 화면 전체 `unknown` + stale 배지 | FR-QUERY-LIVE, FR-CLUSTER-HEALTH | FR-PORTAL, FR-AUDIT-ACTION(조회), FR-LOG-DEEPLINK |
| Gateway | Gateway 헬스 테스트만 `unknown`. 클러스터 목록은 정적 설정 fallback | FR-CH(gateway test) | 나머지 전부 |
| **TMS PostgreSQL** | **쓰기 액션 전면 거부**(감사 불가 → 액션 금지). 조회는 collector 캐시 없이 불가 | 거의 전부 | — |
| LDAP/AD | 신규 로그인 불가. 기존 세션은 만료까지 유지 | FR-PORTAL(신규) | 진행 중 세션 |
| Loki/Grafana | 딥링크가 죽은 링크가 됨. TMS는 정상 | FR-LOG-DEEPLINK(클릭 후) | 나머지 전부 |
| 기존 히스토리 시스템 | 링크가 죽은 링크가 됨 | FR-PT-02 링크 | 나머지 전부 |

> **"PostgreSQL 다운 시 쓰기 거부"는 의도된 설계다.** 감사 기록을 남길 수 없으면 액션을 실행하지 않는다 (FR-AA-01/04). 감사 없는 쓰기를 허용하는 우회로는 만들지 않는다.
> **TMS가 전부 죽어도 Trino 쿼리는 정상 실행된다** — TMS는 쿼리 경로에 없다 (NFR-ISOLATION). 이것이 유지되는지 `reviewer` 체크리스트에서 매번 확인한다.

### 4-1. 외부 호출 공통 규약

모든 외부 HTTP 호출은 아래를 **예외 없이** 지킨다.

| 항목 | 값 | 근거 |
|---|---|---|
| connect timeout | 2s | 빠른 실패 |
| read timeout | 5s (kill은 10s) | 폴링 주기(5s)를 넘기지 않는다 |
| 재시도 | 조회 2회(지수 백오프), **쓰기 0회** | 쓰기 재시도는 중복 kill을 부른다 |
| 서킷브레이커 | 연속 5회 실패 → 30초 open | 죽은 대상을 계속 두드리지 않는다 |
| 실패 시 | 예외를 삼키지 않고 **해당 영역만 `unknown`으로 표기** | NFR-DEGRADE |

---

## 5. 코디네이터 부하 예산 (NFR-PERF-03)

> **⚠️ 예산은 TMS 단독이 아니라 기존 히스토리 프로젝트의 EventListener와 합산해 평가한다.** 코디네이터 CPU 1% 미만은 **합계** 기준이다 (D-001).

| 호출 | 주기 | 코디네이터당 req/s |
|---|---|---|
| `GET /v1/query?state=…` | 5s | 0.20 |
| JMX MBean 7종 | 15s | 0.47 |
| `GET /v1/info` | 30s | 0.03 |
| **합계** | | **≈ 0.70 req/s** |

전부 인메모리 상태를 읽는 GET이며 쿼리 실행을 유발하지 않는다(A1).

**Bolt 2에서 반드시 실측할 것**
- [ ] 폴링 on/off 상태에서 코디네이터 CPU 차이 측정 → 1% 미만 확인
- [ ] `GET /v1/query` 응답 크기 실측 (피크 동시 실행 기준). 수 MB면 주기를 10s로 늘린다
- [ ] 기존 EventListener 부하와 합산해 재평가

---

## 6. 인증·인가 (FR-PORTAL)

### 6-1. 역할 → 권한 매트릭스

| 기능 | 조회자 (viewer) | 운영자 (operator) | 관리자 (admin) |
|---|---|---|---|
| 포털 / 링크 허브 | ✅ | ✅ | ✅ |
| 실행 중 쿼리 조회 | ✅ | ✅ | ✅ |
| **쿼리 kill** | ❌ | ✅ | ✅ |
| 클러스터 헬스 조회 | ✅ | ✅ | ✅ |
| 헬스 임계값 변경 (FR-CH-05) | ❌ | ❌ | ✅ |
| 헬스 테스트 활성/비활성 (FR-CH-03/04) | ❌ | ❌ | ✅ |
| 감사 로그 조회 | ❌ | ✅ | ✅ |
| 감사 로그 내보내기 | ❌ | ❌ | ✅ |

**규칙**: 권한 밖 화면은 **노출하지 않는다**(FR-PT-04). 노출 후 클릭 시 403은 나쁜 설계다. API는 별개로 항상 서버측 검사를 수행한다.

### 6-2. Trino 호출 주체 — **basic auth 서비스 계정. `X-Trino-User` 미전송**

근거: `TRINO_VERIFIED.md` §T3-5

| 항목 | 결정 |
|---|---|
| 인증 | **HTTP Basic auth, TMS 전용 서비스 계정 1개.** 자격증명은 `config.secret.yaml` |
| `X-Trino-User` | **보내지 않는다** |
| `management.user` | **사용하지 않는다** (인증만 우회하고 인가는 그대로. 보안상 불리) |

**`X-Trino-User`를 보내지 않는 이유 (소스 확인)**

`HttpRequestSessionContextFactory` 는 **인증된 사용자와 세션 사용자가 다를 때만** `checkCanImpersonateUser` 를 호출한다. TMS가 헤더를 보내지 않으면 둘이 같아져 **impersonation 경로를 아예 타지 않는다.** 불필요한 인가 호출과 정책 의존을 하나 줄인다.

> **TMS는 최종 사용자를 가장할 이유가 없다.** "이 사용자가 kill해도 되는가"는 §6-1 매트릭스로 **TMS가 먼저 판정**하고, 통과한 요청만 서비스 계정으로 Trino에 보낸다. 감사 로그에는 **실제 요청자**를 기록한다(서비스 계정이 아니라).
> TMS는 테이블 데이터를 읽지 않으므로 **테이블/컬럼/행 마스킹 권한과 무관하다.**

### 6-3. 접근제어 설정별 TMS 동작 (⚠️ R1 전제)

**Trino의 system access control은 하나뿐이며, 데이터 권한과 관리 권한을 함께 관장한다.** "데이터 전용 OPA"라는 분리는 존재하지 않는다.

| 현재 `access-control.name` | TMS R1 동작 | 필요 조치 |
|---|---|---|
| `default` (또는 미설정) | ✅ basic auth만으로 전부 동작 | 없음 |
| `allow-all` | ✅ 동작 | 없음 |
| `read-only` | ⚠️ 조회 동작. kill 가능 여부 미검증 | 확인 필요 |
| **`file`** ← **우리 환경 (2026-08-06 확인)** | ⚠️ **분할 결과 — §6-3-1** | **`rules.json` 에 `system_information` 규칙 추가** |
| `opa` | ❌ Rego 규칙 없으면 403 | §6-4 |

### 6-3-1. 우리 환경 = `file` + `rules.json` (확정 조건)

`TRINO_VERIFIED.md` §T3-6. **규칙 섹션이 없을 때의 기본값이 섹션마다 정반대라 결과가 갈린다.**

| TMS 호출 | 등급 | `rules.json` 에 해당 섹션 없을 때 | 영향 |
|---|---|---|---|
| `GET /v1/info`, `/v1/info/state` | `PUBLIC` | ✅ **항상 허용** (규칙 무관, 문서 명시) | **H-01, H-02** |
| `GET /v1/query` 목록·상세 | `AUTHENTICATED_USER` | ✅ **허용** — `queries` 규칙 없으면 기본 허용 | **FR-QUERY-LIVE** |
| `PUT /v1/query/{id}/killed` | `AUTHENTICATED_USER` | ✅ **허용** — 기본값에 `kill` 포함 | **FR-QL-04** |
| **`GET /v1/jmx/mbean/…`** | **`MANAGEMENT_READ`** | ❌ **거부** — `system_information` 규칙 없으면 **기본 전부 거부** | **H-03 ~ H-07 전부** |
| `PUT /v1/info/state` (R3) | `MANAGEMENT_WRITE` | ❌ 거부 | FR-FL-03 (R3) |

### 6-3-2. 실제 `rules.json` 조건 (2026-08-06 확인) — **B7 해소**

**확인된 현재 설정**

```jsonc
"system_information": [
  { "user": "prometheus_scraper", "allow": ["read", "write"] }   // 그 외 항목은 미확인
],
"queries": [
  { "user": "prometheus_scraper", "allow": [] },                 // ← 이 계정은 쿼리 권한 전무
  { "allow": ["execute", "view", "kill"] }                       // ← 그 외 모든 사용자 전부 허용
]
```

**부가 사실**: `prometheus_scraper` 는 **아직 사용되지 않는다.** Prometheus 연동(SETUP S6) 전에 미리 만들어 둔 계정이다.

#### 결론 1 — TMS는 `prometheus_scraper` 를 재사용하면 안 된다

`queries` 규칙은 **위에서 아래로 첫 매칭이 승리**한다. 첫 규칙이 `prometheus_scraper` 에 `allow: []` 이므로 이 계정은 `execute`·`view`·`kill` 전부 거부된다.

| TMS 호출 | `prometheus_scraper` 로 실행 시 |
|---|---|
| `GET /v1/jmx/mbean` | ✅ 동작 (`system_information: read` 보유) |
| **`GET /v1/query` 목록** | ⚠️ **403이 아니라 빈 목록.** `filterViewQueryOwnedBy` 가 전부 걸러낸다 |
| `GET /v1/query/{id}` | ❌ 403 |
| `PUT /v1/query/{id}/killed` | ❌ 403 |

> **⚠️ 목록 조회의 실패 방식이 위험하다.** 권한 거부인데 **오류가 아니라 빈 배열**이 온다. UI에는 "실행 중 쿼리 0건"으로 보이고, 이는 **한가한 정상 클러스터와 구별되지 않는다.** 조용한 오작동이다.
> **구현 요구사항**: collector가 `/v1/query` 에서 빈 목록을 받았는데 JMX의 `trino.execution:name=QueryManager:RunningQueries` 는 0보다 크면, **권한 문제로 판정하고 `UNKNOWN` + 경고를 띄운다.** 두 소스의 교차 검증으로 조용한 실패를 잡는다.

#### 결론 2 — 전용 계정 `tms-svc` 를 만든다 (D-005)

`queries` 의 catch-all 규칙이 `tms-svc` 에도 매칭되므로 **`queries` 섹션은 손대지 않아도 view·kill이 이미 허용된다.** 다만 최소권한을 위해 명시 규칙을 catch-all **위에** 둔다 — TMS는 원칙 A1에 따라 SQL을 제출하지 않으므로 **`execute` 를 갖지 않는다.**

```jsonc
{
  "system_information": [
    { "user": "tms-svc",            "allow": ["read"] },          // ★ 추가 — R1. write는 R3까지 보류
    { "user": "prometheus_scraper", "allow": ["read"] }           // ★ write 제거 권고 (아래)
  ],
  "queries": [
    { "user": "tms-svc",            "allow": ["view", "kill"] },  // ★ 추가 — execute 없음(A1)
    { "user": "prometheus_scraper", "allow": [] },
    { "allow": ["execute", "view", "kill"] }
  ]
}
```

#### 결론 3 — `prometheus_scraper` 의 `write` 는 과다 권한 (권고)

`system_information: write` 는 **graceful shutdown 트리거 권한**이다 (`PUT /v1/info/state`, §T1-2).
Prometheus 스크레이핑에 필요한 것은 `read` 뿐이다 — `/metrics` 와 `/v1/jmx/mbean` 은 둘 다 `MANAGEMENT_READ` 이므로 `read` 로 충분하다 (§T1-7).

> **아직 사용 전인 계정이므로 지금 줄이는 것이 비용이 0이다.** 나중에 Prometheus를 붙인 뒤 줄이면 회귀 위험을 따져야 한다.

#### 결론 4 — catch-all이 전 사용자에게 `kill` 을 준다 (감사 설계에 영향)

`{ "allow": ["execute", "view", "kill"] }` 는 **모든 인증 사용자가 타인의 쿼리를 kill할 수 있음**을 뜻한다.

> **FR-AUDIT-ACTION의 한계를 명시해야 한다**: TMS 감사 로그는 **TMS를 거친 액션만** 남긴다. 사용자가 Trino Web UI나 CLI로 직접 kill하면 TMS에 기록되지 않는다. "누가 이 쿼리를 죽였나"에 TMS가 항상 답할 수 있는 것은 아니다.
> **권고 (TMS 범위 밖, 운영 정책)**: 5만 사용자 규모에서 `kill` 을 전체 허용으로 두는 것이 의도인지 확인하고, 필요하면 `role`/`group` 으로 좁힌다. 좁히면 TMS 감사의 포괄성이 함께 올라간다.

#### 단계적 적용

`system_information` 에 `tms-svc` 를 추가하기 전에도 **H-01/H-02와 FR-QUERY-LIVE는 동작한다** (PUBLIC + catch-all). 막히는 것은 H-03~H-07뿐이다.
→ **Bolt 2 구현을 `rules.json` 승인과 병렬로 진행할 수 있다.**

> **⚠️ R3 예고**: graceful shutdown 사용 시 `tms-svc` 에 `write` 추가 + `rules.json` 을 **전 워커에 배포** 필요 (문서 명시).

### 6-4. OPA 접근제어 도입 시 필요한 것 (R1 착수 시점엔 불필요할 수 있음)

`access-control.name=opa` 로 전환하면 TMS 호출도 전부 OPA 인가를 거친다. **데이터 규칙이 아니라 시스템 정보 규칙이 필요하다.**

| TMS 호출 | OPA `action.operation` | 필요 시점 |
|---|---|---|
| `/v1/jmx/mbean`, `/v1/query` 목록 | `ReadSystemInformation` | R1 |
| `/v1/query` 목록 필터링 | `FilterViewQueryOwnedBy` | R1 |
| `/v1/query/{id}` 상세 | `ViewQueryOwnedBy` | R1 |
| `PUT /v1/query/{id}/killed` | `KillQueryOwnedBy` | R1 |
| `PUT /v1/info/state` (graceful shutdown) | `WriteSystemInformation` | R3 |

**⚠️ 성능**: `filterViewQueryOwnedBy` 는 **실행 중 쿼리의 distinct 소유자 수만큼** OPA 요청을 보낸다(비배치 시). TMS가 5초 주기로 폴링하므로 동시 사용자 50명이면 클러스터당 **초당 약 10건**의 추가 OPA 부하가 생긴다.
→ **`opa.policy.batched-uri` 설정을 사실상 필수로 본다** (`TRINO_VERIFIED.md` §T3-2, §T3-5).

**⚠️ 별개 사안 — 데이터 권한 계획**: "고정 basic auth 계정 + 사용자별 `X-Trino-User`" 는 정의상 **impersonation**이며, **`default` 접근제어에서는 금지되어 동작하지 않는다.** OPA(또는 `file`) 도입 + Rego의 `ImpersonateUser` 허용 규칙이 있어야 성립한다. TMS 범위는 아니지만 **동일한 접근제어 설정을 공유하므로 함께 계획해야 한다.**

---

## 7. 로그 딥링크 생성기 (FR-LOG-DEEPLINK)

**설계 방침**: 데이터 소스를 모르는 **순수 함수**로 만든다. 통합 시점에 기존 히스토리 시스템 화면에서도 그대로 재사용하기 위함이다.

```
build_log_url(target: LogTarget) -> str
  LogTarget = { query_id?, node_host?, cluster?, time_from, time_to }
```

- 출력 URL은 **설정 파일의 템플릿**으로 만든다. Loki/OpenSearch/기타 어느 쪽이든 코드 변경 없이 대응한다 (S7 선택이 아직 안 끝났다).
- 시간 범위는 항상 앞뒤 여유(기본 ±5분)를 준다. 정확히 쿼리 구간만 자르면 원인 로그를 놓친다.

```yaml
# config/config.yaml
deeplinks:
  log:
    template: "https://loki.example.internal/explore?q={query}&from={from_ms}&to={to_ms}"
    padding_seconds: 300
  query_history:            # 기존 프로젝트 연결 (D-001)
    query_url_template: "https://<기존-시스템>/query/{query_id}"
    home_url: "https://<기존-시스템>/"
  grafana:
    cluster_dashboard: "https://grafana.example.internal/d/<uid>?var-cluster={cluster}"
```

> **`query_history.query_url_template` 은 R1에서 반드시 채운다.** FR-QUERY-LIVE 화면의 각 쿼리 행에 "완료 후 상세 보기" 링크를 걸어, 완료 쿼리 동선 단절(리스크 R1-5)을 메운다.
> **⚠️ 실제 URL 패턴 미확인** — 플랫폼팀 확인 필요. 확인 전까지 설정값은 비워 두고, 비어 있으면 링크를 렌더링하지 않는다.

---

## 8. 설정 파일 구조

```
config/
├── config.yaml           # 일반 설정 (git 추적)
└── config.secret.yaml    # 자격증명 (gitignore) — NFR-NO-SECRET / NFR-SEC-02
```

```yaml
# config/config.yaml (발췌)
clusters:                       # Gateway 미가용 시 이 목록이 진실의 원천 (리스크 R1-2)
  - name: prod-a
    coordinator_url: https://trino-a-coord.example.internal:8443
    expected_workers: 12
  - name: prod-b
    coordinator_url: https://trino-b-coord.example.internal:8443
    expected_workers: 12

gateway:
  enabled: false                # B6 확인 후 true. 선택적 어댑터
  base_url: ""

collector:
  query_poll_interval_seconds: 5
  jmx_poll_interval_seconds: 15
  info_poll_interval_seconds: 30
  stale_threshold_seconds: 30   # 초과 시 UI가 stale 표시
  query_text_max_bytes: 4096
```

---

## 9. 디렉토리 구조 (R1 실제 생성분)

```
src/tms/
├── api/            # FastAPI 라우트 — 조회 + 쓰기 액션
├── clients/        # trino.py (REST+JMX), gateway.py (선택적 어댑터)
├── core/           # 인증·인가·감사 미들웨어, 설정 로더, 서킷브레이커
├── collector/      # ★ 신규: 폴링 루프, 헬스 판정 엔진
├── health/         # health test 카탈로그 (HEALTH_TESTS.md 구현)
├── deeplink/       # URL 생성기 (순수 함수)
└── web/            # UI
```

**R1에서 생성하지 않는 것**: `src/event-listener/` (A2), `src/tms/ingest/` (D-001), `src/routing-service/` (R4)

---

## 10. 미해소 사항

| # | 내용 | 영향 | 처리 |
|---|---|---|---|
| **G-7** | 우리 인증(OPA+TLS) 조합에서 `/v1/jmx/mbean` 실제 접근 가능 여부 | **높음.** FR-CLUSTER-HEALTH 주 수집 경로 | **Bolt 2 착수 전 `trino-expert` 실환경 확인 필수.** 불가 시 JMX RMI 또는 JMX connector로 대체 설계 |
| **B6** | Gateway 버전·설정 | 중간 | `gateway.enabled: false` 기본. 정적 클러스터 목록으로 진행 |
| **D-004** | 감사·헬스 이벤트 저장소 | 중간 | **PostgreSQL 권고.** 인간 승인 대기 |
| — | 기존 히스토리 시스템의 queryId URL 패턴 | 낮음 | 설정값. 미확인 시 링크 미렌더링 |
