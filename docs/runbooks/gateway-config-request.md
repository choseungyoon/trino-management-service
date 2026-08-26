# Gateway 설정 요청서 (운영팀 전달용)

> **작성 2026-08-08** · 근거: 프로덕션과 **같은 버전 19** 를 로컬에 설치해 전량 실측
> 상세 실측 결과는 `TRINO_VERIFIED.md` §T2-3-1
>
> 아직 운영 서비스가 아니고 사용자가 약 50명인 지금이 **바꾸기 가장 싼 시기**다. 5만 규모에서는 같은 변경이 무중단 작업이 된다.

---

## 0. 한 장 요약

| # | 항목 | 상태 | 급함 |
|---|---|---|---|
| **1** | **Gateway API 인증** | 설정 안 되어 있으면 **누구나 라우팅을 끊을 수 있다** | 🔴 **최우선** |
| 2 | TMS 용 `API` 역할 계정 | 필요 | 🟡 |
| 3 | `databaseCache.expireAfterWrite` | 10m → 상향 검토 | 🟡 |
| 4 | S1 least-loaded 라우팅 + **CA 신뢰** | 미적용 | 🟡 |
| 5 | Gateway DB 를 VM1 에서 분리 | SPOF | 🔴 (별건, `TODO.md` W-5) |

---

## 1. 🔴 먼저 확인해 주세요 — API 인증이 켜져 있습니까

**로컬 실측에서 `authentication` 설정 없이 기동하면 아래가 전부 무인증으로 통과했습니다.**

| 요청 | 결과 |
|---|---|
| `GET /gateway/backend/all` | 200 — 백엔드 목록 노출 |
| `POST /gateway/backend/modify/add` | 200 — 백엔드 추가됨 |
| `POST /gateway/backend/deactivate/{name}` | 200 — **해당 클러스터로 쿼리 유입 중단** |
| `GET /webapp/getRoutingRules` | 200 — 라우팅 규칙 노출 |

즉 **Gateway 포트에 도달할 수 있는 누구나 전 사용자의 쿼리 라우팅을 끊거나 백엔드를 바꿔치기할 수 있습니다.** 인증이 아니라 네트워크 접근 통제에만 의존하고 있다면, 사내망 어디서든 가능하다는 뜻입니다.

**확인 방법** — Gateway 호스트가 아닌 곳에서:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://<gateway>:<port>/gateway/backend/all
```

**200 이 나오면 인증이 없는 것입니다.** 401/403 이면 정상입니다.

### 요청

`authentication` 을 설정해 주세요. 역할 모델은 셋뿐입니다 (`ADMIN` / `USER` / `API`).

> ⚠️ **전제**: Gateway 문서 원문 — *"All authentication and authorization mechanisms require configuring TLS as the foundational layer."* **Gateway 에 TLS 가 먼저 켜져 있어야 인증이 동작합니다.**

---

## 2. TMS 용 계정 — `API` 역할

TMS 가 백엔드 목록 조회와(R2) 활성/비활성 토글에(R3 안전 시퀀스) 사용합니다.

```
계정명: tms-gateway  (예시, 사내 규칙에 맞춰 주세요)
역할:   API
```

> ⚠️ **"읽기 전용" 역할은 존재하지 않습니다.** 목록 조회에 필요한 `API` 역할은 문서상 *"Allows access to rest apis to configure the clusters"* 로, **같은 자격증명으로 백엔드 변경도 가능합니다.** TMS 는 이 계정을 `tms-svc` 와 동급으로 보호합니다(`/etc/tms/tms.env`, 0600).

---

## 3. `databaseCache.expireAfterWrite` 상향 검토

현재 `10m` 입니다. 이 값은 캐시 신선도가 아니라 **Gateway DB 장애 시 라우팅 생존 시간**입니다.

| 설정 | DB 장애 시 |
|---|---|
| `10m` (현재) | **10분 뒤 신규 쿼리 라우팅 실패** |
| `1h` (기본값) | 1시간 버팀 |
| `null` | 무기한 버팀 |

평상시 갱신은 `refreshAfterWrite: 5s` 가 담당하므로 목록 신선도는 영향받지 않습니다. `expireAfterWrite` 는 **"DB 가 안 보일 때 언제 포기할 것인가"만** 정합니다. 짧게 둬서 얻는 것이 없습니다.

Gateway DB 가 VM1 에 co-located 된 SPOF 인 점(§5)과 겹치므로 **`null` 또는 `1h` 를 권합니다.**

---

## 4. S1 — least-loaded 라우팅 (+ ⛔ CA 신뢰가 함께 필요합니다)

현재 기본 라우터는 소스상 문자 그대로 `RANDOM.nextInt() % backends.size()` 입니다. **부하를 전혀 보지 않습니다** — 한 클러스터가 느려져도 절반이 그쪽으로 갑니다.

> **⚠️ 2026-08-10 정정.** 이 절은 원래 `monitorType: UI_API` 를 요청했습니다. **틀렸습니다.** Gateway 19 jar 와 Trino 477 실측으로 아래와 같이 바로잡습니다. 근거는 `TRINO_VERIFIED.md` §T2-8.

```yaml
clusterStatsConfiguration:
  monitorType: METRICS         # UI_API 아님 (아래 §4-1 참조)

backendState:                  # monitorType 이 NOOP/INFO_API 가 아니면 필수입니다
  username: tms-svc            # ⭐ TMS 서비스 계정 재사용 가능 — 실측 확인
  password: <비밀번호>            #    /metrics 는 MANAGEMENT_READ 면 됩니다
  ssl: true

modules:
  - io.trino.gateway.ha.module.QueryCountBasedRouterProvider

monitor:
  taskDelay: 1m
  # ⛔ Trino 477 은 노드 수 MBean 이름을 바꿨습니다. Gateway 19 의 기본값
  # (trino_metadata_name_DiscoveryNodeManager_ActiveNodeCount)은 477 에
  # 존재하지 않습니다. 여기에 적어야 Gateway 가 올바른 이름으로 요청합니다.
  metricMinimumValues:
    trino_node_name_CoordinatorNodeManager_ActiveNodeCount: 1
```

### 4-1. 왜 UI_API 가 아니라 METRICS 인가 — 전부 실측

| monitorType | Gateway 19 가 부르는 것 | Trino 477 · `tms-svc` 실측 |
|---|---|---|
| `INFO_API` | `GET /v1/info` | 200. **단 up/down 만. 쿼리 수가 없어 least-loaded 라우터가 무력화됩니다** |
| `UI_API` | `GET /ui/api/stats`, `/ui/api/query` | **401.** Web UI 전용 인증(폼 로그인)이라 basic auth 가 통하지 않습니다. 로컬 Gateway 로그: `login request failed` |
| `JMX` | `GET /v1/jmx/mbean/trino.metadata:name=DiscoveryNodeManager` | **500 `InstanceNotFoundException`.** 477 에는 이 MBean 이 없습니다 (`trino.node:name=CoordinatorNodeManager` 로 개명) |
| `JDBC` | SQL 실행 | `ExecuteQuery` 권한 필요 — TMS 계정에 주지 않기로 한 권한입니다 |
| **`METRICS`** | `GET /metrics?name[]=...` | **200 ✅** |

METRICS 로 Gateway 가 실제로 보내는 요청과, `tms-svc` 로 받은 응답입니다.

```
GET /metrics?name[]=trino_execution_name_QueryManager_RunningQueries
            &name[]=trino_execution_name_QueryManager_QueuedQueries
            &name[]=trino_node_name_CoordinatorNodeManager_ActiveNodeCount
→ 200
  trino_execution_name_QueryManager_RunningQueries 0.0
  trino_execution_name_QueryManager_QueuedQueries 0.0
  trino_node_name_CoordinatorNodeManager_ActiveNodeCount 1.0
```

앞의 두 지표명은 **Gateway 19 의 기본값 그대로 477 에서 동작합니다.** 세 번째만 이름이 달라졌고, `metricMinimumValues` 에 적으면 Gateway 가 그 이름으로 요청합니다(위 URL 이 그 증거입니다).

### 4-2. `backendState` 계정 — `tms-svc` 를 그대로 써도 됩니다

`/metrics` 는 `MANAGEMENT_READ` 이고 TMS 는 이미 그 권한을 갖고 있습니다. 실측에서 위 URL 이 `tms-svc` 로 200 을 반환했습니다.

> **다만 한 가지 알고 결정하실 것**: `tms-svc` 는 **쿼리 kill 권한(`KillQueryOwnedBy`)도 가진 계정**입니다. 재사용하면 Gateway 설정 파일이 그 비밀번호를 들게 되고, **Gateway 가 침해되면 쿼리 kill 까지 가능해집니다.**
>
> Gateway 가 필요로 하는 것은 읽기뿐이므로, **`ReadSystemInformation` 만 가진 별도 모니터 전용 계정**을 권합니다. 어차피 계정을 만드는 김이라 추가 비용이 거의 없습니다. 급하면 `tms-svc` 로 시작해도 동작은 합니다.

### ⛔ 이것만 하면 조용히 실패합니다 — 가장 중요한 부분

로컬 실측에서 코디네이터가 **자체 서명 인증서**였을 때 이렇게 됐습니다.

```
ClusterStatsHttpMonitor.monitor → SSLHandshakeException: PKIX path building failed
ERROR ClusterStatsHttpMonitor  Received null/empty response for /ui/api/stats
```

그런데 기동 로그에는 `Using QueryCountBasedRouterProvider instead of default` 가 **정상 출력됩니다.** 설정은 적용됐는데 **라우터가 받는 통계가 0** 인 상태입니다. 겉보기에는 성공이고 실제로는 예전과 똑같이 동작합니다.

**사내 코디네이터가 내부 CA 발급 인증서를 쓰므로, Gateway JVM 의 truststore 에 내부 CA 가 들어 있어야 합니다.**

```bash
keytool -importcert -alias corp-root-ca -file <내부CA>.pem \
  -cacerts -storepass changeit -noprompt
# 또는 별도 truststore 를 만들고 Gateway 기동 옵션에
#   -Djavax.net.ssl.trustStore=... -Djavax.net.ssl.trustStorePassword=...
```

### 적용 후 반드시 확인

```bash
grep -iE "PKIX|Received null/empty response for /ui/api/stats" <gateway 로그>
```

**아무것도 안 나와야 정상입니다.** 나오면 통계가 안 모이는 것이고, 라우팅은 사실상 랜덤 그대로입니다.

추가 검증: 적용 전후로 두 클러스터의 `trino.execution:name=QueryManager:RunningQueries` 를 비교해 느린 쪽 러닝 쿼리가 상대적으로 낮아지는지 보시면 됩니다.

---

## 5. Gateway DB 분리 (별건이지만 같이 봐주세요)

Gateway 2대가 PostgreSQL 하나를 공유하는데 그 DB 가 VM1 에 얹혀 있습니다. **VM1 이 죽으면 두 Gateway 가 동시에 DB 를 잃습니다.** `databaseCache` 는 안전망이지 대체재가 아닙니다 — 캐시되는 것은 백엔드 목록뿐이고 만료되면 라우팅이 멈춥니다.

사용자 50명인 지금 분리하는 것이 5만 명일 때보다 압도적으로 쌉니다.

---

## 부록 — TMS 가 사용할 엔드포인트 (전부 실측 확인)

| 용도 | 메서드 | 경로 | 비고 |
|---|---|---|---|
| 백엔드 목록 | GET | `/gateway/backend/all` | R2 FR-GW-01 |
| 활성 목록 | GET | `/gateway/backend/active` | |
| 비활성화 | POST | `/gateway/backend/deactivate/{name}` | R3 안전 시퀀스 1단계 |
| 활성화 | POST | `/gateway/backend/activate/{name}` | R3 안전 시퀀스 5단계 |
| 추가/수정 | POST | `/gateway/backend/modify/{add,update}` | JSON 본문 |
| 삭제 | POST | `/gateway/backend/modify/delete` | ⚠️ **평문 이름**. JSON 을 보내면 200 인데 삭제가 안 됩니다 |
| 라우팅 규칙 조회 | GET | `/webapp/getRoutingRules` | ⚠️ **문서에 없는 경로**. `routingRules` 설정이 없으면 500 |
| liveness | GET | `/trino-gateway/livez` | |
| readiness | GET | `/trino-gateway/readyz` | ⚠️ 백엔드가 **0개여도 200**. 라우팅 준비 상태의 근거로 쓸 수 없습니다 |

백엔드 페이로드 필드: `name`, `proxyTo`, `active`, `routingGroup`, `externalUrl`
(`externalUrl` 은 선택 항목이며 Active Backends 화면의 링크만 바꿉니다. 내부/외부 호스트명이 같으면 `proxyTo` 와 같은 값이거나 비워도 무방합니다.)
