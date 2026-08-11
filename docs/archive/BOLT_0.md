# BOLT 0 — 검증 전용 (코드 작성 금지)

> # ⚠️ 보관 문서 — 수행 완료 (2026-08-04)
>
> **이것은 지시서이지 사실이 아니다.** 여기 적힌 표의 빈 칸은 채워졌고, 답은 전부 `docs/TRINO_VERIFIED.md` 로 갔다. 판정은 `docs/archive/BOLT_0_RESULT.md` 다.
>
> 이 지시서의 사전 가정 중 **셋이 검증에서 뒤집혔다** — 남겨 두는 이유가 그것이다. 뒤집힌 목록은 `CLAUDE.md` 의 "Bolt 0에서 뒤집힌 사전 가정"에 있다.

**목표**: 구현 착수 전에 모든 기술 가정을 공식 문서와 실환경으로 검증하고, Blocker 4건을 해소한다.
**기간**: 2~3일
**담당**: `trino-expert` 주도, `orchestrator` 조정
**산출물**: `docs/TRINO_VERIFIED.md`, Blocker 해소 결정 기록

> **왜 이 Bolt가 먼저인가**: 검증 없이 착수하면 존재하지 않는 API를 대상으로 코드를 짜게 된다. 과거에 정확히 이 실패가 있었다 — 존재하지 않는 Trino config property를 사용해 클러스터 기동이 실패했다.
>
> **이 Bolt를 건너뛰지 말 것.**

---

## 규칙

1. **코드를 작성하지 않는다.** 검증 스크립트도 최소한으로.
2. 모든 검증 결과는 **출처 URL 또는 실행 결과**와 함께 기록한다.
3. 확인 불가 항목은 **"확인 불가"로 명시**한다. 추측으로 채우지 않는다.
4. 검증 결과가 요구사항을 무효화하면 **요구사항을 폐기하고 문서에 반영**한다.

---

## Task 1 — Trino 477 API/SPI 검증 (최우선)

각 항목을 **Trino 477 공식 문서**에서 확인한다. 현재(current) 문서가 아니라 **477 버전 문서**를 봐야 한다.

| # | 검증 대상 | 이유 | 결과 |
|---|---|---|---|
| T1-1 | EventListener SPI 인터페이스 시그니처 및 `QueryCompletedEvent` 필드 목록 | FR-QUERY-HISTORY 전체의 기반 | |
| T1-2 | Graceful shutdown 엔드포인트 경로·HTTP 메서드·페이로드·인증 방식 | FR-FLEET, 워커 축소 | |
| T1-3 | **런타임 로그 레벨 변경 API의 OSS 지원 여부** | **미지원 시 FR-LOGLEVEL 전체 폐기** | |
| T1-4 | 리소스 그룹 상태 조회 방법 (JMX MBean 경로 또는 `system.runtime` 테이블) | FR-WORKLOAD | |
| T1-5 | 실행 중 쿼리 조회 방법 및 kill API | FR-QUERY-LIVE | |
| T1-6 | `catalog.management` / `catalog.store` 지원 값, ALTER CATALOG 지원 여부 | FR-CATALOG | |
| T1-7 | JMX 메트릭 노출 방식 및 주요 MBean 이름 | 관측성 전반 | |

**T1-3 특별 지침**: Starburst Enterprise 문서에는 "재시작 없이 로그 레벨 변경" 기능이 명확히 존재한다. **그러나 이것이 SEP 전용 기능일 가능성이 높다.** OSS Trino 477에 동등한 API가 없다면 FR-LOGLEVEL을 폐기하고 `BACKLOG.md`, `REQUIREMENTS.md`, 릴리스 계획에서 제거한다. **대안을 임의로 발명하지 말 것** (예: 파일 수정 + 재시작은 "재시작 없는 변경"이 아니다).

---

## Task 2 — Trino Gateway 검증

| # | 검증 대상 | 이유 | 결과 |
|---|---|---|---|
| T2-1 | **현재 사용 중인 라우터 확인** — `modules` 설정에 무엇이 있는가 | 기본값은 `StochasticRoutingManager`(무작위 분배). 이것이 현재 성능 편차 문제의 일부일 수 있음 | |
| T2-2 | `QueryCountBasedRouterProvider` 활성화 방법 및 `clusterStatsConfiguration.monitorType`(UI_API/JDBC), `backendState` 설정 | 클러스터 간 적응 분배 확보 | |
| T2-3 | Gateway REST API 스펙 (backend/routing rule 관리) | FR-GATEWAY | |
| T2-4 | `databaseCache` 설정 및 DB 다운 시 동작 확인 | HA 안전망 | |
| T2-5 | 라우팅 규칙에서 `requestAnalyzerConfig.analyzeRequest=true` 시 사용 가능한 필드 (TrinoRequestUser, TrinoQueryProperties) | FR-ROUTING-VIEW, SQL 기반 라우팅 | |
| T2-6 | External Routing Service 연동 규격 (요청/응답 포맷) | FR-ROUTING-SVC | |
| T2-7 | 세션 어피니티(쿠키 기반) 설정 방법 — IP HASH 대체 | LB 개선 | |

**T2-1은 즉시 확인 가치가 있다.** 기본 라우터가 무작위 분배라면, 설정 한 줄로 "느린 클러스터에 트래픽 덜 보내기"가 해결된다.

---

## Task 3 — OPA 검증

| # | 검증 대상 | 이유 | 결과 |
|---|---|---|---|
| T3-1 | Trino 477 OPA access control 설정 property 목록 | 접근제어 기반 |  |
| T3-2 | Batch authorization 관련 property 정확한 이름 (컬럼 마스킹 batch 포함) | 컬럼 단위 권한 성능 | |
| T3-3 | OPA decision log 포맷 및 수집 방식 | FR-OPA | |
| T3-4 | 워커 노드 대상 인가 설정 방법 (graceful shutdown 권한) | FR-FLEET 연동 | |

**T3-2 주의**: batch 관련 property 이름은 버전 간 다르다. 476에서는 `opa.policy.batched-uri`였고 이후 컬럼 마스킹 batch가 추가되었다. **477 문서로 확인할 것.**

---

## Task 4 — Blocker 해소

| Blocker | 조치 | 담당 |
|---|---|---|
| **B1** Gateway charset 이슈 | 현재 해소 상태 확인. 미해소면 SQL 기반 라우팅 요구사항을 BLOCKED로 유지 | trino-expert |
| **B2** `catalog.management` 동작 | T1-6 결과로 판정 | trino-expert |
| **B4** 히스토리 저장소 선정 | **이벤트량 추정 후 인간 결정** (아래 Task 5) | **HUMAN** |
| **B5** 런타임 로그레벨 API | T1-3 결과로 판정 | trino-expert |

---

## Task 5 — 워크로드 특성화 (인간 협업 필요)

**저장소 선정과 SLO 목표값은 이 데이터 없이 결정할 수 없다.** "5만 사용자"는 사이징 기준이 될 수 없다.

수집할 것:
- 피크 동시 실행 쿼리 수
- 일일/시간당 쿼리 수 (→ 이벤트량 추정 → 저장소 용량 산정)
- p50 / p95 쿼리 실행시간
- BI 툴 주도 vs 애드혹 비율
- 평균/최대 결과셋 크기
- 사용자별 쿼리 분포 (상위 소수가 대부분을 차지하는가)

**산출물**: `docs/WORKLOAD_PROFILE.md`

> 현재 히스토리가 in-memory라 과거 데이터가 없다면, **가용한 범위의 샘플링 + Gateway DB의 쿼리 히스토리**를 활용한다. Gateway는 자체 DB에 쿼리 히스토리를 저장하므로 여기서 상당 부분 추정 가능하다.

---

## Task 6 — 즉시 실행 가능한 SETUP 항목 정리

개발이 아니라 **설정으로 해결되는 항목**을 목록화한다. 이것들은 R1을 기다릴 필요가 없다.

| # | 항목 | 근거 |
|---|---|---|
| S1 | `QueryCountBasedRouterProvider` 활성화 | 클러스터 간 적응 분배. 성능 편차 문제 완화 |
| S2 | 사용자 기반 라우팅 규칙 작성 | Gateway 라우팅 규칙 엔진 |
| S3 | 카탈로그/스키마 기반 라우팅 규칙 (B1 해소 후) | TrinoQueryProperties |
| S4 | LB를 IP HASH → 세션 어피니티로 교체 | T2-7 결과 적용 |
| S5 | PostgreSQL을 Gateway VM에서 분리 + HA | SPOF 제거 |
| S6 | node_exporter + Prometheus + Grafana 기본 대시보드 | 관측성 기반 |
| S7 | Loki 또는 OpenSearch 로그 수집 | FR-LOG-DEEPLINK 전제 |
| S8 | `databaseCache` 활성화 | Gateway DB 장애 안전망 |

**S1과 S5는 우선순위가 높다.** S1은 현재 성능 문제를 즉시 완화할 수 있고, S5는 현존 SPOF를 제거한다.

---

## Task 7 — 근본 원인 규명 착수 (병행)

**"동일 스펙 클러스터의 성능 차이"는 정상이 아니라 결함이다.** 라우팅으로 우회하면 결함이 영구화된다.

확인 항목:
- [ ] 두 클러스터의 config 파일 체크섬 비교 (drift 확인)
- [ ] JVM 옵션 / GC 설정 동일 여부
- [ ] 하이퍼바이저 상 물리 호스트 배치 (노이지 네이버 가능성)
- [ ] 디스크 I/O 성능 실측 비교
- [ ] 네트워크 지연 실측 (코디네이터↔워커, 워커↔S3)
- [ ] 워커 수 및 실제 등록 워커 수 일치 여부

> 이 작업은 FR-BENCHMARK가 완성되면 자동화되지만, **지금 수동으로라도 확인할 가치가 크다.** 원인이 config drift라면 라우팅 개선 없이도 해결된다.

---

## Bolt 0 완료 조건 (Definition of Done)

- [ ] `docs/TRINO_VERIFIED.md` 작성 완료 — 모든 항목에 결과 또는 "확인 불가" 기재
- [ ] Task 1~3의 모든 `[VERIFY]` 항목 해소 또는 명시적 미해소 처리
- [ ] Blocker B1/B2/B5 판정 완료
- [ ] FR-LOGLEVEL 존폐 결정 및 문서 반영
- [ ] `docs/WORKLOAD_PROFILE.md` 초안 작성
- [ ] SETUP 항목 목록 및 실행 우선순위 확정
- [ ] 검증 결과로 무효화된 요구사항을 `BACKLOG.md` / `REQUIREMENTS.md`에 반영
- [ ] **인간 검토 및 R1 착수 승인**

---

## 다음 단계

Bolt 0 완료 후 **Bolt 1 = R1 상세 설계**로 진행한다.
R1 범위: FR-PORTAL, FR-QUERY-HISTORY, FR-QUERY-LIVE, FR-CLUSTER-HEALTH, FR-AUDIT-ACTION, FR-LOG-DEEPLINK

**Bolt 1은 인간이 R1 착수를 승인한 뒤에만 시작한다.**
