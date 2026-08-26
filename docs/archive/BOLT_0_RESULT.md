# BOLT 0 결과 — Blocker 판정 · SETUP 계획 · 근본원인 규명

> # ⚠️ 보관 문서 — 2026-08-04 시점의 기록이다
>
> **현재 상태를 알려면 이 문서를 읽지 않는다.** 여기 적힌 상태값("인간 검토 대기", "R1 착수 승인 대기")은 전부 그 뒤에 지나갔다. R1 은 사내 실환경에 배포됐고 Bolt 4(안전 재시작)까지 끝났다.
>
> 이 문서를 남겨 두는 이유는 하나다 — `BACKLOG.md` 와 `REQUIREMENTS.md` 의 **판정 근거**가 여기 있기 때문이다. "왜 이렇게 정했나"를 되짚을 때만 본다.
>
> | 지금 무엇을 보나 | 문서 |
> |---|---|
> | 현재 진행 상태 | `docs/BOLTS.md` |
> | 사람이 할 일 (결정·확인·작업) | `docs/TODO.md` |
> | 기술 사실 (유일한 출처) | `docs/TRINO_VERIFIED.md` |
> | 결정과 그 이유 | `docs/DECISIONS.md` |
>
> ## 이후 실측으로 뒤집힌 것
>
> **⛔ §3 의 `monitorType: UI_API` 권고를 따르지 않는다.** 2026-08-10 로컬 Gateway 19 실측 결과 `UI_API` 는 **401** 이다 — `/ui/api/stats` 는 폼 로그인 전용이라 basic auth 가 통하지 않는다. 실제로 동작하는 값은 **`METRICS`** 이며, 근거와 전체 실측표는 `docs/runbooks/gateway-config-request.md` §4-1 에 있다.
>
> **§3 의 SETUP 목록(S1~S8) 은 여기서 갱신되지 않는다.** 각 항목의 현재 상태와 담당은 `docs/TODO.md` 의 W 항목으로 옮겼다.

---

**수행일**: 2026-08-04
**근거 문서**: `docs/TRINO_VERIFIED.md` (모든 기술적 사실의 출처)
**당시 상태**: 인간 검토 대기. §4 의 결정 3건이 승인되어야 Bolt 1(R1 상세 설계)로 진행.

---

## 1. 요약

| Bolt 0 Task | 상태 |
|---|---|
| Task 1 — Trino 477 API/SPI 검증 (T1-1~T1-7) | ✅ 완료. 7/7 확인 |
| Task 2 — Gateway 검증 (T2-1~T2-7) | ✅ 완료. 7/7 확인 (버전 확정 미해결 → G-1) |
| Task 3 — OPA 검증 (T3-1~T3-4) | ✅ 완료. 4/4 확인 |
| Task 4 — Blocker 해소 | **B1 해소 / B2 해소 / B5 해소 / B4 미해소(인간)** |
| Task 5 — 워크로드 특성화 | ⏳ 템플릿 작성 완료(`WORKLOAD_PROFILE.md`). **데이터 수집은 인간/실환경 접근 필요** |
| Task 6 — SETUP 항목 정리 | ✅ 완료 (§3) |
| Task 7 — 근본 원인 규명 | ⏳ 체크리스트 작성 완료(§5). **실행은 실환경 접근 필요** |

**가장 큰 발견 3가지**

1. **FR-LOGLEVEL은 폐기 대상이 아니다.** OSS Trino 477에 런타임 로그 레벨 변경 수단이 **존재한다** — REST가 아니라 JMX MBean(`io.airlift.log:name=Logging`)이다. 사전 가정이 틀렸다.
2. **TMS는 RMI 없이 순수 HTTP로 모든 JMX 지표를 읽을 수 있다.** Trino 477이 `/v1/jmx/mbean`을 `MANAGEMENT_READ`로 노출한다. Python 백엔드로 관측성 전반이 구현 가능하다는 뜻이며, 이것이 R1~R2 설계의 기반이 된다.
3. **B1(charset)은 우리가 고칠 문제가 아니라 업스트림에서 이미 고쳐진 문제다.** Trino Gateway **19**(2026-05-11)에 수정이 포함되었다. 조치는 코드가 아니라 **버전 업그레이드**다.

---

## 2. Task 4 — Blocker 판정

### B1 — Gateway charset 이슈 → **해소 (업스트림 수정됨). 조치: Gateway 업그레이드**

| 항목 | 내용 |
|---|---|
| 이슈 | [trino-gateway#1032](https://github.com/trinodb/trino-gateway/issues/1032) — *"requestAnalyzerConfig fails to parse SQL body when Content-Type charset is not set"* (2026-04-24 등록) |
| 증상 | `analyzeRequest: true` 인데 `trinoQueryProperties.body`가 항상 비고 `catalogs`/`tables`/`schemas`가 `[]`. Gateway가 `charset is not set in the request` DEBUG 로그를 남김 |
| 영향 클라이언트 | `trino-python-client` (0.337.0 확인됨) 등 Content-Type에 charset을 안 붙이는 클라이언트 |
| 수정 | [PR #1054](https://github.com/trinodb/trino-gateway/pull/1054) "Set default charset to utf8" — **머지됨 2026-05-09** |
| 포함 릴리스 | **Trino Gateway 19 (2026-05-11)** — 릴리스 노트: *"Prevent SQL analysis failures due to missing character encoding. (#1032)"* |
| 참고 | [PR #1107](https://github.com/trinodb/trino-gateway/pull/1107)은 **머지되지 않았다.** 후속 개선 시도였을 뿐 수정의 본체가 아니다 |

**판정**: **해소 조건부.** Gateway **19 이상**이면 해소. 미만이면 미해소.
**조치**: G-1(운영 Gateway 버전 확인) → 19 미만이면 업그레이드. 업그레이드 시 아래 파괴적 변경 2건을 반드시 함께 처리한다.

> **Gateway 19 업그레이드 시 동반되는 파괴적 변경**
> 1. **리소스 그룹 관리 기능 전면 제거** ([#656](https://github.com/trinodb/trino-gateway/issues/656)). 기존 DB 테이블은 보존되나 신규 배포에서는 생성/관리되지 않는다. → **FR-WORKLOAD의 데이터 소스를 Trino로 변경해야 한다** (이미 반영).
> 2. 라우팅 설정 키 `addXForwardedHeaders` → **`forwardedHeadersEnabled`** 로 이름 변경 ([#1005](https://github.com/trinodb/trino-gateway/pull/1005)). 기존 config에 이 키가 있으면 반드시 수정.
>
> **S3(카탈로그/스키마 기반 라우팅 규칙)는 이 업그레이드 이후에만 착수한다.**

---

### B2 — `catalog.management` 동작 → **해소. 단 FR-CATALOG 범위 축소**

**확인된 사실** (TRINO_VERIFIED §T1-6)

- `catalog.management` = `static`(기본) | `dynamic` — **존재하고 동작한다**
- `catalog.store` = `file`(기본) | `memory`
- **`ALTER CATALOG`는 Trino 477에 존재하지 않는다.** (477 문서 트리에 `create-catalog.md` / `drop-catalog.md` / `show-catalogs.md` 만 있고 `alter-catalog.md` 없음)

**판정**

| BACKLOG 항목 | 기존 | 판정 후 |
|---|---|---|
| 5-1a 카탈로그 등록 | BUILD(조건부) | **BUILD 확정** — `CREATE CATALOG … USING … WITH (…)` |
| 5-1b 카탈로그 변경 | **BLOCKED** | **REJECT.** `ALTER CATALOG` 부재. 변경 = DROP+CREATE이며, Hive/Iceberg는 DROP 시 리소스 미해제로 **코디네이터·워커 재시작 필요** → "카탈로그 변경 기능"을 UI로 제공하면 무중단으로 오인된다 |
| 5-1c 카탈로그 제거 | BUILD(경고 필수) | **BUILD 유지 + 경고 강화** |

**FR-CATALOG에 반드시 표시할 사실 (공식 문서 원문 근거)**
1. `catalog.management=dynamic` 은 **experimental**이며 *"Because of the security implications the syntax might change and be backward incompatible."*
2. Hive / Iceberg / Delta Lake / Hudi 는 DROP 시 리소스를 완전히 해제하지 못한다 → **재시작 필요**
3. `CREATE CATALOG` **쿼리 전문이 Web UI에 그대로 보인다** — 자격증명 평문 입력 절대 금지
4. 자격증명은 `'${ENV:VAR}'` 환경변수 참조를 쓴다. **해당 환경변수가 전 노드에 secret으로 설정되어 있어야 한다**

**남은 인간 결정**: `catalog.store` 선택 — `file`(코디네이터 카탈로그 디렉토리 쓰기 권한 필요, 골든 이미지 읽기전용 전략과 충돌 가능) vs `memory`(재시작 시 소실). `[NEEDS-HUMAN-DECISION]` 유지. **R4 항목이므로 지금 결정할 필요는 없다.**

---

### B5 — 런타임 로그레벨 API → **해소. 사전 가정이 틀렸다**

**BOLT_0.md는 "OSS에 없으면 폐기"를 지시했다. 검증 결과 OSS에 있다.**

| 질문 | 답 | 근거 |
|---|---|---|
| 재시작 없이 변경 가능? | **가능** | Trino 477 `Server.java`가 `LogJmxModule`을 무조건 등록 → `io.airlift.log:name=Logging` MBean의 `setLevel` / `setRootLevel` |
| REST API로 가능? | **불가** | `/v1/jmx`(airlift `MBeanResource`)는 `@GET` 4개뿐. 쓰기 메서드 없음 |
| 공식 문서에 있나? | **없음** | `/docs/477/admin/logging.html`에 런타임 변경 언급 전무. **문서화되지 않은 내부 API** |
| 재시작 후 유지? | **안 됨** | JVM 인메모리 상태만 변경 |
| 전 노드 일괄? | **안 됨** | 노드별 개별 호출 (클러스터당 13회) |

**판정: FR-LOGLEVEL 폐기하지 않는다. 단 아래를 확정한다.**

| 요구사항 | 변경 |
|---|---|
| FR-LL-01 로거별 레벨 변경 | **존치.** 단 "전 노드 순차 적용, 부분 실패 가능"을 설계에 반영 |
| FR-LL-02 변경 로거 목록 + 리셋 | **존치.** `getAllLevels()`로 조회 가능. "리셋"은 TMS가 원래 값을 기억해 되돌리는 방식 |
| FR-LL-03 관리자 권한 + 감사 | **존치** |
| FR-LL-04 DEBUG 자동 만료 | **존치. 중요도 상승** — 재시작 없이는 안 돌아오므로 만료 장치가 유일한 안전판 |
| (SEP의 "재시작 후에도 유지") | **삭제.** OSS에서 재현 불가. 영속화가 필요하면 Ansible로 `log.properties`를 함께 갱신하는 별개 작업 |

**남은 결정 (§4 D-1)**: 호출 경로. Python은 JMX/RMI를 못 한다.
- (A) 전 노드에 **Jolokia** JVM 에이전트 → HTTP로 MBean 쓰기. **신규 에이전트 의존성 + 공격면 증가**
- (B) JVM 헬퍼 프로세스(작은 Java 유틸)를 TMS가 호출. **신규 컴포넌트**
- (C) **FR-LOGLEVEL 자체를 드롭**하고, 로그 레벨 변경은 Ansible + 재시작(FR-CLUSTER-OPS 안전 시퀀스 경유)으로만 제공. **"재시작 없는 변경"을 포기**

> **R4 항목이므로 지금 결정하지 않아도 R1 착수는 가능하다.** 다만 (A)/(B)는 인프라 변경을 수반하므로 조기에 방향을 잡는 편이 낫다.

---

### B4 — 히스토리 저장소 선정 → **미해소. 인간 결정 영역 (`[NEEDS-HUMAN-DECISION]`)**

**Bolt 0에서 할 수 있는 것은 여기까지다.** 이벤트량 추정에 필요한 워크로드 데이터가 없다. `docs/WORKLOAD_PROFILE.md`에 수집 절차와 계산식을 준비했다.

**Bolt 0가 확정한 입력값** (TRINO_VERIFIED §T1-1)
- 저장 대상은 `QueryCompletedEvent` 이며 최상위 9개 필드 + 하위 구조체
- **`QueryStatistics.taskStatistics`, `operatorSummariesProvider`, `QueryMetadata.plan`, `jsonPlan` 은 쿼리당 수 MB에 달할 수 있다** → 기본 저장 대상에서 제외하고 화이트리스트로 관리
- 화이트리스트 기준 1건당 크기는 **약 2~5 KB (JSON)** 로 추정된다 — **이 값은 추정이며, `WORKLOAD_PROFILE.md`의 실측 절차로 확정해야 한다**

**결정 순서**: 워크로드 데이터 수집 → 이벤트량/용량 산정 → PostgreSQL vs Elasticsearch 결정.
**R1 착수 전에 반드시 닫아야 한다.** FR-QUERY-HISTORY는 R1의 핵심이다.

---

## 3. Task 6 — SETUP 항목 및 실행 우선순위

> **SETUP은 개발이 아니라 설정이다. R1을 기다릴 필요가 없고, 기다려서도 안 된다.**
> 아래 항목 전부 **TMS 코드와 무관**하며 플랫폼팀이 즉시 착수할 수 있다.

### 실행 순서 (의존 관계 반영)

```
1단계 (즉시, 독립):     S5 ─── S6
                          │
2단계 (S5 이후):        S8 ─┴─ S4
                        
3단계 (Gateway≥19):     S1 ──→ S2 ──→ S3
                        
병행 (독립):            S7
```

### 우선순위표

| 순위 | # | 항목 | 왜 이 순위인가 | 선행 조건 | 근거 |
|---|---|---|---|---|---|
| **1** | **S5** | **PostgreSQL을 Gateway VM1에서 분리 + HA** | **현존 SPOF.** VM1이 죽으면 두 Gateway가 동시에 DB를 잃는다. S4·S8의 전제이며, Gateway 크로스 인스턴스 queryId 조회도 이 DB에 의존한다 (TRINO_VERIFIED §T2-7) | 없음 | §T2-7 |
| **1** | **S6** | node_exporter + Prometheus + Grafana 기본 대시보드 | **S1과 Task 7의 측정 기반.** 이것 없이는 "S1이 효과가 있었나"를 판정할 수 없다. Trino JMX 지표는 `/v1/jmx/mbean` 또는 JMX exporter로 수집 | 없음 | §T1-7 |
| **2** | **S8** | `databaseCache` 활성화 | Gateway DB 장애 안전망. **기본값이 `false`다.** 단 캐시 대상은 백엔드 목록뿐이므로 S5의 대체재가 아니다 | S5 권장 | §T2-4 |
| **2** | **S4** | LB IP HASH 교체 | **재해석 필요 — 아래 참조** | S5 필수 | §T2-7 |
| **3** | **S1** | `QueryCountBasedRouterProvider` 활성화 | **현재 성능 편차를 즉시 완화한다.** 기본 라우터가 문자 그대로 난수 분배이므로 설정 3줄로 least-loaded 라우팅이 켜진다 | Gateway 재시작 | §T2-1, §T2-2 |
| **4** | **S2** | 사용자 기반 라우팅 규칙 작성 | `analyzeRequest: true` 시 `trinoRequestUser` 사용 가능 | S1, `requestAnalyzerConfig` | §T2-5 |
| **5** | **S3** | 카탈로그/스키마 기반 라우팅 규칙 | B1 해소(=Gateway 19+) 필수 | **Gateway ≥ 19** | §T2-5, B1 |
| 병행 | **S7** | Loki 또는 OpenSearch 로그 수집 | FR-LOG-DEEPLINK(R1)의 전제. **OPA decision log 수집(FR-OPA-04)도 여기에 얹는다** | 없음 | §T3-3 |

### 항목별 실행 지침

**S1 — `QueryCountBasedRouterProvider`**

```yaml
backendState:
  username: <username>
  password: <password>
  ssl: <false|true>
  xForwardedProtoHeader: <false|true>

clusterStatsConfiguration:
  monitorType: UI_API      # ⚠️ 기본값 INFO_API로는 통계가 안 모인다

modules:
  - io.trino.gateway.ha.module.QueryCountBasedRouterProvider

monitor:
  taskDelay: 1m            # 기본 1분. 짧은 쿼리 비중이 높으면 낮출 것
```

> ⚠️ **`monitorType` 변경을 빠뜨리지 말 것.** 기본 `INFO_API`로는 running/queued 통계가 수집되지 않아 라우터가 제 역할을 못 한다. 허용값: `INFO_API`, `METRICS`, `JDBC`, `JMX`, `UI_API`, `NOOP`. 문서가 요구하는 값은 `UI_API` 또는 `JDBC`.
> **검증 방법**: 적용 전후로 두 클러스터의 `trino.execution:name=QueryManager:RunningQueries`(S6로 수집)를 비교한다. 느린 클러스터의 러닝 쿼리 수가 상대적으로 낮아져야 한다.

**S4 — LB 교체: 재해석**

> **BOLT_0.md의 "IP HASH → 세션 어피니티" 전제는 검증 결과 수정이 필요하다.**
>
> Gateway는 queryId→backend 매핑을 로컬 캐시에서 찾지 못하면 **공유 PostgreSQL을 조회**한다 (`BaseRoutingManager` → `HaQueryHistoryManager.getBackendForQueryId`). 즉 **Trino 클라이언트 프로토콜을 위해 LB 어피니티가 필요하지 않다.**
>
> 진짜 문제는 IP HASH가 **소수의 BI 서버/프록시 IP에서 오는 대량 트래픽을 한쪽 Gateway로 몰아버리는 것**이며, 이는 쿠키 어피니티로도 해결되지 않는다.
>
> **권고: 어피니티 없는 라운드로빈 또는 least-connection.** 단 아래 전제를 모두 확인한 뒤에 적용한다.
>
> | 전제 | 확인 방법 |
> |---|---|
> | `dataStore.queryHistoryEnabled` 가 true (기본값) | Gateway config 확인. **false면 크로스 게이트웨이 조회가 전 백엔드 브루트포스로 전락** |
> | `dataStore.queryHistoryHoursRetention` > 최장 실행 쿼리 시간 | Gateway config + WORKLOAD_PROFILE의 최대 실행시간 |
> | PostgreSQL HA (= **S5 완료**) | S5 |
> | OAuth2 사용 시 두 Gateway가 동일 `cookieSigningSecret` | Gateway config |
>
> **S5 전에 S4를 하지 말 것.** DB가 SPOF인 상태에서 어피니티를 없애면 장애 반경만 넓어진다.

**S8 — `databaseCache`**

```yaml
databaseCache:
  enabled: true            # 기본값 false
  expireAfterWrite: 1h     # null로 두면 만료 없음 → 장기 DB 장애에도 라우팅 생존
  refreshAfterWrite: 5s
```

> `expireAfterWrite` 만료 후 DB가 여전히 죽어 있으면 **요청이 실패한다** (stale 폴백 없음). 목표 DB 복구 시간보다 길게 잡거나 `null`을 검토할 것.
> **캐시 대상은 백엔드 클러스터 목록뿐이다.** 쿼리 히스토리 기록과 queryId 조회는 캐시되지 않는다 → FR-GW-04의 AC를 이에 맞춰 축소했다.

**S7 — 로그 수집**

- Trino 로그 + **OPA decision log** 를 함께 수집한다.
- OPA 측: `decision_logs.console: true` → stdout → Promtail/Filebeat.
- ⚠️ decision log의 `input` 필드에 **SQL 전문과 사용자 식별정보**가 들어간다. `decision_logs.mask_decision` 정책을 반드시 함께 설계할 것.
- ⚠️ Trino 측 `opa.log-requests` / `opa.log-responses` 는 **켜지 말 것** (전 쿼리의 요청·응답 본문이 DEBUG로 쏟아진다). 인가 감사는 OPA decision log로 한다.

---

## 4. 인간 결정이 필요한 항목

| # | 결정 | 언제까지 | 기본 권고 |
|---|---|---|---|
| **D-1** | **B4 히스토리 저장소** — PostgreSQL vs Elasticsearch | **R1 착수 전 (필수)** | `WORKLOAD_PROFILE.md` 수집 후 결정. 이벤트량이 일 100만 건 미만이고 검색 요건이 단순하면 PostgreSQL(기존 자산 재활용) |
| **D-2** | **FR-LOGLEVEL 구현 방식** — (A) Jolokia 에이전트 / (B) JVM 헬퍼 / (C) 기능 드롭 | R4 착수 전 (조기 결정 권장) | **(C) 드롭 검토를 권한다.** 문서화되지 않은 내부 API + 신규 에이전트 의존성 + 전 노드 개별 호출 + 재시작 시 소실 — 비용 대비 가치가 낮다 |
| **D-3** | **Gateway 19+ 업그레이드 승인** | B1 해소를 원하면 필수 | 승인 권고. 단 리소스 그룹 기능 제거 + `forwardedHeadersEnabled` 개명을 함께 처리 |
| (기존) | `catalog.store` = `file` vs `memory` | R4 | 미결 유지 |
| (기존) | SLO 목표값 | R2 | `WORKLOAD_PROFILE.md` 수집 후 |

---

## 5. Task 7 — 근본 원인 규명 체크리스트

> **"동일 스펙 클러스터의 성능 차이"는 정상이 아니라 결함이다.** S1(least-loaded 라우팅)은 증상 완화이지 치료가 아니다. 원인이 config drift라면 라우팅 개선 없이 해결된다.
>
> **이 작업은 실환경 접근이 필요하다.** 아래는 실행 절차이며, 결과는 `docs/CLUSTER_DIFF_REPORT.md`(신규)에 기록한다.

### 우선순위 순 체크리스트

| # | 확인 항목 | 방법 | 판정 기준 |
|---|---|---|---|
| **1** | **워커 수 및 실제 등록 워커 수 일치** | 각 클러스터에서 `SELECT node_id, node_version, coordinator, state FROM system.runtime.nodes` | 두 클러스터 모두 워커 12 + 코디네이터 1 = 13행, 전부 `active`. **불일치면 여기서 끝. 다른 걸 볼 필요 없다** |
| **2** | **Trino 버전 일치** | 위 쿼리의 `node_version` 컬럼 | 전 노드 동일 값 |
| **3** | **config 파일 체크섬 비교** | 전 노드 `etc/config.properties`, `etc/jvm.config`, `etc/catalog/*.properties`, `etc/log.properties` 의 `sha256sum` 비교 | 역할(coordinator/worker)별로 두 클러스터가 동일. **차이 나는 파일과 라인을 전부 기록** |
| **4** | **JVM 옵션 / GC 설정** | `etc/jvm.config` diff + 실행 중 프로세스의 `jcmd <pid> VM.flags` | 힙 크기, GC 종류, `-XX:` 플래그 전부 동일 |
| **5** | **런타임 지표 비교 (부하 동일 조건)** | S6 수집 지표: `java.lang:type=Memory:HeapMemoryUsage.used`, GC pause, `trino.execution:name=QueryManager:ExecutionTime.FiveMinutes.P50`, `trino.failuredetector:name=HeartbeatFailureDetector:ActiveCount` | 느린 쪽에서 GC pause 또는 heap 사용률이 유의하게 높은가 |
| **6** | **하이퍼바이저 상 물리 호스트 배치** | 가상화 플랫폼 콘솔에서 각 워커 VM의 호스트 확인 | 한 클러스터의 워커가 소수 호스트에 몰려 있는가 (노이지 네이버) |
| **7** | **디스크 I/O 실측** | 전 워커에서 동일 조건 `fio` 실행 (spill 디렉토리 대상) | 두 클러스터 간 IOPS/지연 차이 |
| **8** | **네트워크 지연 실측** | 코디네이터↔워커, 워커↔Ceph S3 엔드포인트 RTT 및 대역폭 | 두 클러스터 간 차이 |

### 실행 원칙

- **1~4번을 먼저 한다.** 비용이 거의 없고, config drift라면 여기서 즉시 드러난다.
- **5~8번은 프로덕션 부하 중에 하지 않는다.** 특히 7번(`fio`)은 그 자체가 성능 저하를 유발한다. **대상 클러스터를 Gateway routing group에서 비활성화한 뒤** 실행한다 (`POST /gateway/backend/deactivate/{name}`).
- 이 절차는 **FR-BENCHMARK(R2) 및 FR-FLEET-DRIFT(R3)로 자동화된다.** 지금은 수동으로 하되, 수동 절차를 그대로 기록해 두면 그것이 곧 자동화 명세가 된다.

---

## 6. Bolt 0 완료 조건 (DoD) 체크

| DoD 항목 | 상태 |
|---|---|
| `docs/TRINO_VERIFIED.md` 작성 — 모든 항목에 결과 또는 "확인 불가" 기재 | ✅ 완료. 미해소 8건은 §5에 사유·담당·기한과 함께 명시 |
| Task 1~3의 모든 `[VERIFY]` 해소 또는 명시적 미해소 처리 | ✅ 완료 |
| Blocker B1/B2/B5 판정 | ✅ 완료 (§2) |
| FR-LOGLEVEL 존폐 결정 및 문서 반영 | ✅ **존치(축소)** 로 판정, `REQUIREMENTS.md` 반영. **구현 방식은 D-2로 인간 결정 대기** |
| `docs/WORKLOAD_PROFILE.md` 초안 | ✅ 템플릿·수집절차·계산식 작성. **데이터는 인간/실환경 접근 필요** |
| SETUP 항목 목록 및 실행 우선순위 확정 | ✅ 완료 (§3) |
| 검증 결과로 무효화된 요구사항을 BACKLOG/REQUIREMENTS에 반영 | ✅ 완료 |
| **인간 검토 및 R1 착수 승인** | ⏳ **대기 중** |

**R1 착수를 막고 있는 것 (2건)**
1. **D-1 (B4 히스토리 저장소)** — FR-QUERY-HISTORY가 R1의 핵심이므로 필수
2. **G-1/G-2 (Gateway 버전·설정 확인)** — FR-GATEWAY, FR-ROUTING-VIEW 설계 전제
