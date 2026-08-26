# BOLTS — Bolt 이력 및 계획

> **소유자**: `orchestrator`
> TEAMS.md §5 순서: ① 계획 제시 → **인간 승인** → ② `trino-expert` 선행 검증 → ③ 담당 에이전트 실행 → ④ `reviewer` 게이트 → ⑤ 인간 최종 확인

---

## Bolt 0 — 검증 전용 ✅ 완료

| 항목 | 내용 |
|---|---|
| 기간 | 2026-08-04 |
| 담당 | `trino-expert` 주도 |
| 산출물 | `TRINO_VERIFIED.md`, `archive/BOLT_0_RESULT.md`, `WORKLOAD_PROFILE.md`(초안) |
| 결과 | T1~T3 검증 18/18 완료. Blocker B1/B2/B5 해소, B6 신규 식별 |

---

## Bolt 1 — R1 상세 설계 ✅ **완료** (승인 후 Bolt 2 진행)

> **2026-08-06 진행 상황**: U1~U8 산출물 작성 완료. 아래 계획은 원안이며, 실제 산출물은 §Bolt 1 결과 참조.

| 항목 | 내용 |
|---|---|
| **목표** | R1 5개 기능의 **구현 가능한 수준의 설계 확정.** 코드는 작성하지 않는다 |
| **기간** | 2~3일 |
| **담당** | `orchestrator` 조정, `backend-dev`·`frontend-dev` 설계, `trino-expert` 게이트 |
| **선행 조건** | R1 착수 승인 ✅ (2026-08-06) |

### R1 범위 (D-001 반영)

| FR | 내용 | 주 데이터 소스 (Bolt 0 검증 완료) |
|---|---|---|
| FR-PORTAL | SSO 포털 + 링크 허브 | 사내 LDAP/AD |
| FR-QUERY-LIVE | 실행 중 쿼리 모니터링 + kill | `GET /v1/query`, `PUT /v1/query/{id}/killed` |
| FR-CLUSTER-HEALTH | 합성 헬스 + 조치 조언 | `GET /v1/jmx/mbean/…`, `GET /v1/info`, `system.runtime.nodes` |
| FR-AUDIT-ACTION | 운영 액션 감사 | TMS 자체 저장소 |
| FR-LOG-DEEPLINK | 로그 시스템 딥링크 | URL 생성만 (순수 함수) |

**⛔ 범위 밖**: FR-QUERY-HISTORY, `src/event-listener/`, 완료 쿼리 조회 화면

### Unit of Work

| # | UoW | 담당 | 산출물 |
|---|---|---|---|
| U1 | **아키텍처 확정** — 컴포넌트 경계, 배포 단위(systemd), 설정 파일 구조 | `backend-dev` | `docs/ARCHITECTURE.md` |
| U2 | **외부 연동 클라이언트 설계** — Trino/Gateway 클라이언트, 타임아웃·서킷브레이커·`unknown` 폴백 규약 | `backend-dev` + `trino-expert` | U1에 포함 |
| U3 | **FR-CLUSTER-HEALTH 설계** — health test 카탈로그(판정식·임계값·조치 조언), roll-up 규칙 | `backend-dev` | `docs/HEALTH_TESTS.md` |
| U4 | **FR-QUERY-LIVE 설계** — 폴링 주기·캐시 전략, kill 흐름, NFR-PERF-03 부하 예산 | `backend-dev` | U1에 포함 |
| U5 | **FR-AUDIT-ACTION 설계** — append-only 데이터 모델, `reason` 강제 미들웨어 | `backend-dev` | `docs/AUDIT_MODEL.md` |
| U6 | **FR-PORTAL 설계** — 인증 흐름, 역할(조회자/운영자/관리자) → 화면·API 권한 매트릭스 | `backend-dev` + `frontend-dev` | U1에 포함 |
| U7 | **FR-LOG-DEEPLINK 설계** — URL 생성기 시그니처 (데이터 소스 비의존) | `backend-dev` | U1에 포함 |
| U8 | **API 명세** — R1 전체 엔드포인트, 요청/응답, 오류 규약 | `backend-dev` | `docs/API_R1.md` |

### 완료 정의 (DoD)

- [ ] U1~U8 산출물 작성 완료
- [ ] 모든 Trino/Gateway 연동점이 `TRINO_VERIFIED.md` 항목을 **인용**한다 (검증 안 된 API 사용 0건)
- [ ] 모든 설계 요소가 **FR-ID에 추적 가능**
- [ ] NFR-ISOLATION 준수 명시 — 쿼리 경로 개입 0건, 프록시 0건
- [ ] NFR-DEGRADE 준수 명시 — 의존 컴포넌트별 다운 시 동작 정의
- [ ] NFR-PERF-03 부하 예산 산정 (**기존 히스토리 프로젝트 EventListener 부하와 합산 기준**)
- [ ] 쓰기 API 전수에 `reason` 필수 + 감사 기록 설계 반영
- [ ] `trino-expert` 게이트 통과
- [ ] **인간 승인 → Bolt 2 (구현) 착수**

### 리스크

| # | 리스크 | 영향 | 완화 |
|---|---|---|---|
| R1-1 | **G-7 미해소** — 우리 인증(OPA+TLS) 조합에서 `/v1/jmx/mbean` 이 실제로 접근 가능한지 미확인 | **높음.** FR-CLUSTER-HEALTH의 주 수집 경로 | **Bolt 1 중 `trino-expert`가 실환경 실험으로 선행 확인.** 불가 시 JMX connector(SQL) 또는 RMI로 대체 설계 |
| R1-2 | **B6 미해소** — Gateway 버전·설정 미확정 | 중간. 클러스터 목록 소스와 Gateway 헬스 테스트 | Gateway 의존을 **선택적 어댑터**로 분리. 미가용 시 정적 설정 fallback + 해당 테스트만 `unknown` |
| R1-3 | 코디네이터 폴링 부하가 NFR-PERF-03 초과 | 높음 | 폴링 주기·캐시를 설계에 명시하고, **기존 프로젝트 부하와 합산**해 예산 산정 |
| R1-4 | 감사 로그 저장소가 미정 | 중간 | **신규 결정 D-004 필요** (아래) |
| R1-5 | 완료 쿼리 동선 단절 — 운영자가 "방금 끝난 쿼리"를 TMS에서 못 본다 | 중간 | FR-PT-02 링크 허브 + FR-LD-01 딥링크로 기존 시스템에 연결. **통합 Bolt에서 근본 해소** |

### 신규 인간 결정 필요

**D-004 — TMS 자체 저장소 선정 (감사 로그 + 헬스 이벤트)**

FR-QUERY-HISTORY가 빠지면서 B4(대용량 히스토리 저장소)는 이월됐지만, **FR-AUDIT-ACTION과 FR-CH-07(헬스 이벤트 기록)에는 여전히 저장소가 필요하다.**

| 항목 | 예상 규모 |
|---|---|
| 감사 로그 | 운영 액션만 기록 → **일 수백 건 수준.** 쿼리 이벤트와 규모가 다르다 |
| 헬스 이벤트 | 상태 전이 시에만 기록 → 소량 |

> **권고: PostgreSQL.** 규모가 작고, 감사 로그는 append-only + 정형 검색이며, 사내에 이미 운영 경험이 있다. Elasticsearch를 검토할 이유가 없다.
> **주의**: Gateway용 PostgreSQL과 **분리**한다. Gateway DB는 쿼리 경로에 관여하므로 TMS가 부하를 얹으면 NFR-ISOLATION 취지에 어긋난다.

---

### Bolt 1 결과 (2026-08-06)

**산출물**

| UoW | 산출물 | 상태 |
|---|---|---|
| U1, U2, U4, U6, U7 | `docs/ARCHITECTURE.md` | ✅ |
| U3 | `docs/HEALTH_TESTS.md` | ✅ |
| U5 | `docs/AUDIT_MODEL.md` | ✅ |
| U8 | `docs/API_R1.md` | ✅ |

**설계 중 확정된 핵심 판단**

| # | 판단 | 근거 |
|---|---|---|
| A1 | **TMS는 Trino에 SQL 쿼리를 제출하지 않는다.** REST + JMX-over-HTTP만 | `system.runtime.*` 폴링은 쿼리 슬롯을 먹고, **하루 약 17,000건의 TMS 쿼리를 기존 히스토리 시스템에 주입해 남의 데이터를 오염시킨다** |
| A3 | 폴링 주체를 `tms-collector` 단일 유닛으로 분리 | API를 스케일아웃하면 폴링도 N배가 되어 NFR-PERF-03이 조용히 깨진다 |
| — | 완료 상태(`FINISHED`/`FAILED`)는 `GET /v1/query` 에서 요청하지 않는다 | 완료 쿼리는 D-001로 기존 프로젝트 소관. 응답 크기도 줄어든다 |
| — | 감사 저장소 불가 시 **쓰기 API 전면 503** | 감사 없는 쓰기를 허용하는 우회로를 만들지 않는다 (AU1) |

**Bolt 1에서 추가 검증한 Trino 477 사실** (`TRINO_VERIFIED.md` 보강 대상)
- `QueryState` enum 값 9종: `QUEUED`, `WAITING_FOR_RESOURCES`, `DISPATCHING`, `PLANNING`, `STARTING`, `RUNNING`, `FINISHING`, `FINISHED`, `FAILED`
- `BasicQueryInfo` / `BasicQueryStats` 필드 전량 — FR-QUERY-LIVE에 필요한 `resourceGroupId`, `elapsedTime`, `totalCpuTime`, `peakUserMemoryReservation`, `progressPercentage` 가 **전부 존재**

### ⚠️ Bolt 1이 제안하는 요구사항 축소 (인간 승인 필요)

| 항목 | 내용 | 이관처 |
|---|---|---|
| **FR-CH-06** | 반복 크래시 감지 — systemd `Restart=` 이력이 필요해 노드 접근이 전제된다 | R3 FR-FLEET |
| **FR-LD-02** | 노드 상세 → 로그 딥링크 — R1에 노드 상세 화면 자체가 없다 | R3 FR-FLEET |
| 헬스 테스트 6종 | GC pause(MBean 미검증), 리소스그룹 큐뎁스(R2), 디스크(node_exporter), systemd(R3), OPA(R4) | 각 표기 |

### Bolt 1 잔여 리스크

| # | 내용 | 처리 시점 |
|---|---|---|
| ~~G-7~~ | ~~`/v1/jmx/mbean` 접근 가능 여부~~ | **2026-08-06 해소** — 환경이 `access-control.name=file` + `rules.json` 임을 확인. `MANAGEMENT_READ` → `checkCanReadSystemInformation` → **`system_information` 규칙이 없으면 기본 전부 거부** (§T3-6). **조치 = `rules.json` 에 규칙 한 블록 추가** (`ARCHITECTURE.md` §6-3-1) |
| ~~B7~~ | ~~`rules.json` 실물 확인~~ | **2026-08-06 해소.** `system_information` 에 `prometheus_scraper`(read,write), `queries` 에 `prometheus_scraper`(allow: []) + catch-all(execute,view,kill) 확인. **→ D-005: 전용 계정 `tms-svc` 필요** (`ARCHITECTURE.md` §6-3-2) |
| ~~A-1~~ | ~~`rules.json` 에 `tms-svc` 규칙 추가~~ | ✅ **완료 (2026-08-06)** |
| ~~A-2~~ | ~~`tms-svc` basic auth 계정 발급~~ | ✅ **완료 (2026-08-06)** |
| ~~A-3~~ | ~~`prometheus_scraper` 를 `read` 로 축소~~ | ✅ **완료 (2026-08-06)** |

| ~~H-02~~ | ~~`GET /v1/info` 기동 상태 필드명~~ | **해소** — `ServerInfo` record 의 `starting`(boolean). 소스 확인 @477 |
| ~~H-03~~ | ~~`ActiveNodeCount` 의 코디네이터 포함 여부~~ | **해소** — 포함한다. 실측(12워커→13) |
| ~~D-004~~ | ~~감사·헬스 이벤트 저장소~~ | **해소** — 신규 PostgreSQL 인스턴스로 확정 |
| — | 기존 히스토리 시스템의 queryId URL 패턴 | 플랫폼팀 확인. 미확인 시 링크 미렌더링 |

> **⚠️ 자격증명 취급**: `tms-svc` 비밀번호는 **`config/config.secret.yaml`(gitignore) 또는 `/etc/tms/tms.env` 에만** 둔다. 이 저장소는 **PUBLIC**이다(D-002).
> **A-1~A-3 완료로 R1 진행을 막는 항목은 없다.** 남은 미확정은 히스토리 URL 패턴 하나이며, 비어 있으면 링크가 렌더링되지 않을 뿐 기능은 동작한다.

---

## Bolt 2 — R1 구현 🟢 **구현 완료 · 실환경 배포 완료 — DoD 3건 잔여**

> **2026-08-08 현황**: R1 5개 기능을 구현하고 사내 실환경에 배포해 운영 중이다.
> 배포 과정에서 발견·수정한 결함은 `runbooks/deploy.md` 와 각 커밋에 기록했다.
> **잔여 DoD**: NFR-PERF-03 실측(A-1), 테스트 커버리지 80% 확인, reviewer 체크리스트.
> 셋 다 `docs/TODO.md` 로 이관했다.

| 항목 | 내용 |
|---|---|
| **목표** | R1 5개 기능 구현 및 테스트. `TRINO_VERIFIED.md` 에 없는 API는 쓰지 않는다 |
| **기간** | 3일 (V1 결과에 따라 조정) |
| **담당** | `backend-dev` 주도, `trino-expert` 게이트, `frontend-dev`, `reviewer` |
| **선행 조건** | A-1/A-2/A-3 ✅ 완료. **D-004 승인 필요** |

### Unit of Work

| # | UoW | 산출물 | 비고 |
|---|---|---|---|
| **V1** | **실환경 연결 검증 (최우선, 코드 이전)** | `TRINO_VERIFIED.md` 갱신 | 아래 §V1 |
| ~~V2~~ | ✅ **완료** — `pyproject`, `config.yaml`, 설정 로더, systemd 유닛 2종, DB 마이그레이션 | `src/tms/core/config.py`, `migrations/001_init.sql`, `ops/systemd/` | 19 tests |
| ~~V3~~ | ✅ **완료** — Trino 클라이언트 (REST + JMX), 오류 분류, 서킷브레이커, transport 추상화 | `src/tms/clients/` | 34 tests |
| ~~V4~~ | ✅ **완료** — 폴링 루프, 스냅샷 기록, stale 판정, **H-09 교차검증 상시화**, 적응형 백오프, advisory lock 단일 인스턴스 강제 | `src/tms/collector/` | 43 tests |
| ~~V5~~ | ✅ **완료** — 헬스 엔진 H-01~H-09, roll-up, 안정화 카운트, stale 강등 | `src/tms/health/` | 41 tests |
| ~~V6~~ | ✅ **완료** — 감사 강제(컨텍스트 매니저), append-only 저장소, 소스 스캐너 | `src/tms/core/audit*.py` | 23 tests |
| ~~V7~~ | ✅ **완료** — 서비스 계층 + FastAPI 래핑, 역할 매트릭스, 헬스 브리지 | `src/tms/api/`, `collector/health_writer.py` | 45 tests |
| ~~V8~~ | ✅ **완료** — 딥링크 생성기 (V7 에 포함) | `src/tms/api/deeplinks.py` | — |
| ~~V9~~ | ✅ **완료(축소)** — 로컬 계정 인증 + 세션 (D-007). **AD 연동은 이월** | `src/tms/core/{passwords,sessions,localauth}.py` | 30 tests |
| V10 | UI | `src/tms/web/` | 차트 자체 구현 금지 |
| V11 | 테스트 — 핵심 로직 80% + **NFR-PERF-03 실측 ✅(로컬)** | `tests/`, `docs/PERF_MEASUREMENT.md` | 프로덕션 재측정 필요 |

### §V1 — 실환경 연결 검증 (코드 작성 전 선행)

**A-1/A-2/A-3이 완료됐어도 문서상 성립과 실제 동작은 다르다.** 아래를 `tms-svc` 자격으로 실제 호출해 확인한 뒤 구현에 들어간다. 결과는 `TRINO_VERIFIED.md` 에 기록한다.

| # | 확인 | 실패 시 영향 |
|---|---|---|
| V1-1 | `GET /v1/info` — 인증 없이 200, **기동 상태 필드명 확정** | H-01/H-02 |
| V1-2 | `GET /v1/jmx/mbean` — `tms-svc` basic auth로 200 | **H-03~H-07 전체** |
| V1-3 | §3-2의 MBean 7종이 실제로 존재하고 값이 나오는가. **`ActiveCount` 가 코디네이터를 포함하는가** | H-03 보정 |
| V1-4 | `GET /v1/query?state=…` — 목록이 **비어 있지 않은가** (권한 필터 확인) | FR-QUERY-LIVE |
| V1-5 | `/v1/query` 응답 크기 실측 (피크 기준) | 폴링 주기 조정 |

**V1 실행 결과 (2026-08-06)**

| # | 결과 |
|---|---|
| V1-1 | ✅ `/v1/info` PUBLIC 확인 |
| V1-2 | ✅ `/v1/jmx/mbean` 200 — **A-1/A-2 (`tms-svc` + `system_information:read`) 실환경 동작 확인** |
| V1-3 | ⛔→✅ `HeartbeatFailureDetector` 500 (477 문서 오류) → **`trino.node:name=CoordinatorNodeManager` 로 정정, 200 확인**. `ActiveNodeCount=13` → **코디네이터 포함 확정** |
| **V1-4** | ✅ **해소 (쿼리 실행 중 재실행)** — `list=1, RunningQueries=1` 일치. **`tms-svc` 의 `queries:view` 실환경 동작 확인** |
| **V1-5** | ✅ **측정 완료** — **3,493 bytes / 1 query** (0.02s). 쿼리당 약 3.5 KB |
| V1-7 | ✅ `/metrics` 접근 가능 |

**V1-5 사이징 판단 (쿼리당 3.5 KB 기준)**

| 동시 실행 쿼리 | 폴링 1회 응답 | 5초 주기 대역폭 |
|---|---|---|
| 50 | ~175 KB | ~35 KB/s |
| 200 | ~700 KB | ~140 KB/s |
| 500 | ~1.7 MB | ~350 KB/s |
| 1,000 | ~3.5 MB | ~700 KB/s |

> **결론: 기본 폴링 주기 5초를 유지한다.** 동시 1,000건이어도 코디네이터당 700 KB/s 수준으로 사내망에서 문제되지 않는다.
> **⚠️ 단, 쿼리당 크기는 SQL 텍스트 길이가 지배한다.** 3.5 KB는 표본 1건이며, BI 툴의 긴 생성 SQL이 많으면 크게 늘 수 있다. **피크 동시 실행 수도 아직 모른다** (`WORKLOAD_PROFILE.md` W2 미수집). → **자동 백오프를 구현으로 흡수한다** (§B2-5).

**V1 미수행 항목**

| # | 사유 |
|---|---|
| V1-6 (kill 시험) | 프로덕션 쿼리 대상 금지. Bolt 2 구현 후 통제된 환경에서 수행 |
| V1-8 (코디네이터 CPU 부하) | 실측에 Prometheus/모니터링 필요 (S6 미완). **구현 후 DoD에서 측정** |
| V1-6 | `PUT /v1/query/{id}/killed` — **비프로덕션 또는 자체 생성 쿼리로만** 시험 | FR-QL-04 |
| V1-7 | `GET /metrics?name[]=…` 응답과 MBean 이름 매핑 | 폴링 7건→1건 최적화 여부 |
| V1-8 | 폴링 on/off 시 코디네이터 CPU 차이 (NFR-PERF-03, **기존 EventListener와 합산**) | 부하 예산 |

> **V1-6 주의**: kill 시험은 프로덕션 쿼리를 대상으로 하지 않는다. TMS 자체 계정으로 긴 쿼리를 하나 띄워 그것만 죽인다. 대상 확인 없는 시험은 금지(CLAUDE.md 절대 규칙 5의 취지).

### 완료 정의 (DoD)

- [x] V1 전 항목 확인 및 `TRINO_VERIFIED.md` 반영 — 로컬 Trino 477 + 실환경 양쪽에서 확인
- [x] `API_R1.md` 전 엔드포인트 동작 — 실 스택 대상 스모크 전수 통과
- [x] 쓰기 4개 전수: `reason` 없으면 400, 감사 기록 남음, DB 불가 시 503
- [x] `audit_action` 대상 UPDATE/DELETE 코드 부재 + 마이그레이션에 `REVOKE` 포함 — 스캐너 + GRANT 검증
- [x] `BAD`/`CONCERNING` 상태에 `advice` 빈 값 없음 — 엔진에서 강제 + 테스트
- [ ] **NFR-PERF-03 실측 충족** (기존 EventListener 합산 기준) → `TODO.md` W-1
- [x] **핵심 로직 테스트 커버리지 80%** — 2026-08-08 측정: 전체 **80%**. `formatting.py` 100%, `views.py` 92%, `routes.py` 70%. Postgres 어댑터 제외 시 83%
- [ ] **`reviewer` 체크리스트 전 항목 통과** → 미수행
- [x] 자격증명이 저장소에 없음 (**PUBLIC 저장소**) — 매 커밋 전 스캔

### 리스크

| # | 리스크 | 완화 |
|---|---|---|
| B2-1 | V1에서 JMX 접근이 여전히 막힘 | H-01/H-02만으로 R1 축소 출시 후 규칙 재조정 |
| B2-2 | 코디네이터 부하가 예산 초과 | 폴링 주기 상향 + `/metrics` 묶음 조회 전환 |
| B2-3 | LDAP/AD 연동 사양 미확인 | **기존 FastAPI 자산의 인증 패턴 재사용**. 미확인 시 V9를 뒤로 미루고 나머지 진행 |
| B2-4 | 기존 히스토리 URL 패턴 미확인 | 설정 비우면 링크 미렌더링. 기능 차단 없음 |
| **B2-5** | **`queries:view` 미검증 + 피크 응답 크기 미측정** (V1-4/V1-5 미결론) | ① 폴링 주기를 **설정값**으로 두고 기본 5초, 응답이 임계 초과 시 **자동 백오프**하도록 구현 ② collector가 H-09 교차검증을 **상시 수행**해 권한 문제를 런타임에 자체 탐지 ③ 쿼리 실행 중 V1 재실행으로 확정. **구현을 막지 않는다** |

---

## Bolt 3 — R2 상세 설계 🟡 **설계 완료 — 인간 검토 대기**

| 항목 | 내용 |
|---|---|
| **목표** | R2 착수 전 설계. `TRINO_VERIFIED.md` 에 없는 API 는 쓰지 않는다 |
| **산출물** | `docs/DESIGN_R2.md` |
| **선행** | Bolt 2(R1 구현) 완료. R1 DoD 잔여 2건은 R2 를 막지 않는다 |
| **상태** | 2026-08-08 작성 완료 |

### 이번 Bolt 의 결론은 설계가 아니라 **범위**다

설계에 앞서 전제 사실을 실측·재확인한 결과, **요구사항 5개 중 온전히 만들 수 있는 것이 2개**였다.

| 기능 | 판정 | 막는 것 |
|---|---|---|
| FR-WORKLOAD | 🟢 착수 가능 (AC 2건 축소) | `jmxExport` 선행 설정 |
| FR-GATEWAY | 🟡 부분 착수 | API 계정(B-1), 규칙 조회 API 부재 |
| FR-ROUTING-VIEW | 🔴 대부분 불가 | 규칙 조회 API 부재 |
| FR-SLO | 🔴 불가 | 워크로드 데이터 미수집 + 목표값 인간 결정 |
| FR-BENCHMARK | ⚪ 범위 불일치 | 문서 간 R2/R3 불일치 |

### 설계 중 뒤집힌 사전 가정 2건

1. **리소스 그룹을 REST 로 조회한다는 전제가 틀렸다.** `GET /v1/resourceGroupState/{id}` 는 루트 그룹 이름만 받고(점 경로 404), 응답도 root + 1단계까지다. **JMX 열거가 유일한 경로**이며, 이로써 Bolt 0 의 미해소 항목 G-5(ObjectName 확인 불가)도 해소됐다.
2. **Gateway 라우팅 규칙 조회 API 가 없다.** 쓰기(`POST /webapp/updateRoutingRules`)만 존재한다. FR-GW-05 와 FR-RV-01 은 구현 경로가 없다. 규칙 파일은 Gateway 호스트에 있고 TMS 는 접근하지 않는다.

### 핵심 설계 판단

| # | 판단 | 근거 |
|---|---|---|
| W1 | 리소스 그룹 데이터는 **JMX 열거**로 수집 | REST 는 깊이 제한 |
| W2 | 화면은 "활동한 그룹만" 이라고 **명시** | 지연 생성. 전체 설정을 알 방법이 없다 |
| G1 | deactivate 는 **"유입 차단"까지만** 책임진다 | drain 판정·재시작은 R3. 안전 시퀀스를 건너뛰는 경로를 만들지 않는다 |
| G2 | 클러스터 CRUD 는 **Gateway API 경유** | D-008 부기. TMS 는 목록의 주인이 되지 않는다 |
| S1 | FR-SLO 는 **히스토리 통합 이후로 이월** | 목표값·근거 데이터·완료 쿼리가 동시에 막고 있다 |

### 인간 결정 대기 6건

`DESIGN_R2.md` §7 참조. **H1(R2 범위 문서 불일치)은 착수 전에 답이 필요하다** — `BACKLOG.md` 와 `REQUIREMENTS.md` 의 R2 구성이 다르다.



---

## Bolt 4 — R3 선행: 안전 재시작 (FR-CO-02/03/04) 🟢 **구현 완료**

| 항목 | 내용 |
|---|---|
| **목표** | R2 가 "절반짜리 기능"으로 보이는 문제를 해소. R3 의 재시작을 앞당겨 함께 구현 |
| **산출물** | `src/tms/ops/` (5개 모듈), `migrations/004~007`, 재시작 화면 2종, 테스트 60여 건 |
| **상태** | 2026-08-09 구현·검증 완료. 사내 연결은 `runbooks/upgrade-r2-r3.md` |

### 왜 R3 을 앞당겼나

R2 만 놓고 보면 TMS 는 "클러스터가 아프다는 걸 보여주지만 아무것도 못 하는 화면"이다. Gateway 비활성화(§G1)까지만 있고 그 다음이 없으면, 운영자는 TMS 에서 문제를 발견하고 터미널로 옮겨가야 한다. **관측과 조치 사이의 이 단절이 R2 를 반쪽으로 보이게 하는 원인**이었다.

### 구조 — 4개의 이음매

| 모듈 | 책임 | 왜 분리했나 |
|---|---|---|
| `sequence.py` | **무엇이 허용되는가** (상태 기계) | I/O 없음. 순서 규칙 전부를 Trino·Gateway 없이 테스트할 수 있다 |
| `executor.py` / `ansible.py` | **재시작을 누가 하는가** | 위험 성격이 전혀 다르다. D-009 |
| `repository.py` | **재시작이 프로세스보다 오래 산다** | 잊힌 시퀀스 = 조용한 장애 |
| `service.py` | 이 셋을 실제로 굴린다 | 관측 → 판단 → 기록의 순서를 강제 |

### 이번 Bolt 에서 확인한 사실 3건

1. **비밀 마스킹 정규식이 Ansible 의 실제 변수명을 전부 놓치고 있었다.** `\bpassword\b` 는 `vault_password` 와 매칭되지 않는다 — `_` 와 `p` 사이에 단어 경계가 없기 때문이다. `ansible_ssh_pass`, `become_password`, `AWS_SECRET_ACCESS_KEY` 도 마찬가지였다. **가장 샐 법한 이름들이 정확히 다 빠져 있었다.** 실측 후 수정.
2. **`subprocess.run(capture_output=True)` 로는 진행 상황을 보여줄 수 없다.** 출력이 프로세스 종료 후에만 돌아온다. 몇 분짜리 재시작에서 빈 화면은 "진행 중"과 "멈춤"을 구별해주지 못한다. `Popen` + 라인 단위 스트리밍으로 교체하고, 워치독으로 타임아웃을 건다.
3. **감사 액션 드리프트 가드가 스스로 낡아 있었다.** 코드와 DB CHECK 제약의 일치를 검사하는 테스트가 `001_init.sql` 만 읽고 있었고, 액션 타입이 처음 추가되는 순간(=이번) 실패하게 되어 있었다. 마이그레이션 순서상 **마지막 정의**를 유효 정의로 보도록 수정. 진행 로그 레벨에도 같은 가드를 새로 붙였다.

### 검증

- 단위·라우트 테스트 **514건 통과** (신규 60여 건)
- **실브라우저 전 과정 통과** — Gateway 19 · Trino 477 · PostgreSQL 상대로 시작 → 드레인 → 플레이북 스트리밍 → 자동 진행 → 트래픽 복구까지 19개 항목. 라인 수 증가 `[6, 10, 13, 16, 20, 23, 26, 30, 32]` 로 **실제 스트리밍**임을 확인했고, 페이지 리로드는 0회다
- **비밀이 살아있는 출력에서 마스킹되는 것**을 브라우저에서 확인 (`ansible_ssh_pass: ***`)
- 중단(abort)이 실제 Gateway 백엔드를 `active=true` 로 되돌리는 것을 확인

---

## Bolt 5 — Fleet + 실환경 검증 (FR-FL-01/03) 🟢 **구현 완료**

| 항목 | 내용 |
|---|---|
| **목표** | "노드가 몇 대이고 각각 살아 있나"를 화면에서 답한다. 안전 재시작을 실환경에서 완주시킨다 |
| **산출물** | `src/tms/fleet/`, `src/tms/clients/node.py`, `migrations/008~009`, Fleet 화면, `docs/templates/cluster-inventory.ini.example` |
| **상태** | 2026-08-11 구현 완료. `manual` 모드 사내 완주 확인, `ansible` 모드 검증 완료 |

### 데이터 소스를 바꿔야 했던 이유

설계는 `system.runtime.nodes` 를 1차 소스로 잡고 있었다. 실측에서 **`PERMISSION_DENIED`** 였고, 필요한 `ExecuteQuery` 는 TMS 가 의도적으로 갖지 않는 권한이다. `/v1/node` 는 **477 에 아예 없다(404)**. 남은 경로는 인벤토리(정적) + 노드별 `/v1/info`(런타임) 두 개를 합치는 것뿐이었고, 그래서 **"어느 워커가 안 붙었나"는 이름이 아니라 개수로만** 답한다 (D-1 미결).

### 사내 배포에서 드러난 것 4건

| # | 무엇 | 왜 로컬에서 안 잡혔나 |
|---|---|---|
| 1 | `/gateway` 가 500 | 웹 테스트가 **연동을 전부 끈 채** 앱을 만들고 있었다 → 연동을 켠 채 전 화면을 도는 `EveryScreenTest` 추가 |
| 2 | H-08 이 항상 UNKNOWN | 수집기가 `gateway_backends` 를 넘기지 않았고, 테스트는 백엔드 `name` 으로 매칭하고 있었다 (실제로는 조인된 `cluster`) |
| 3 | `manual` 모드가 멈춘 것처럼 보임 | 동작은 옳았고 **화면이 "지금 당신 차례"라고 말하지 않았다.** 버그가 아니라 문구 문제 |
| 4 | `ansible-playbook` Errno 2 → 그 다음 exit 5 | 바이너리 검증이 없었고, `ProtectHome=true` 아래 **ansible-core 는 쓰기 가능한 HOME 없이 import 단계에서 죽는다** |

> **4번이 이번에 가장 값진 발견이다.** `Errno 2` 만 고쳤으면 그 다음에 exit 5 를 만났을 것이고, 그때는 원인이 훨씬 덜 명백했을 것이다. 진짜 ansible-core 로 재현해 보기 전까지는 알 수 없었다 — 파이썬 shim 으로는 영원히 통과한다.

### 검증

- 단위·라우트 테스트 **586건 통과**
- 실브라우저: Fleet 화면에서 **응답 없는 노드가 숨겨지지 않고 표시**되는 것, 코디네이터에는 shutdown 이 제공되지 않는 것, 직접 요청해도 서비스가 거부하는 것 확인
- 진짜 `ansible-playbook`(core 2.21) 을 실제 실행기로, `HOME` 을 systemd 와 동일하게 깨뜨린 채 실행 → 13.1초 스트리밍 후 `succeeded`
