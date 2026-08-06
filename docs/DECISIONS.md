# DECISIONS — 의사결정 기록

> **소유자**: `orchestrator`
> 되돌리기 비용이 큰 결정만 기록한다. 결정한 사람, 날짜, 근거, 뒤집는 조건을 남긴다.
> 형식: 결정은 **번복 가능**하다. 단 번복하려면 여기 적힌 근거가 무효가 되었음을 보여야 한다.

---

## D-001 — FR-QUERY-HISTORY를 R1 범위에서 제외한다

| 항목 | 내용 |
|---|---|
| **날짜** | 2026-08-06 |
| **결정자** | Platform Owner (인간) |
| **상태** | 확정 |

**결정**: 완료 쿼리 히스토리 기능은 **이미 별도 프로젝트로 구현되어 운영 중**이다. TMS가 다시 만들지 않는다. R1 범위에서 제외하고, 추후 두 프로젝트를 통합한다.

**근거**: 중복 구현 회피. 특히 EventListener는 코디네이터 프로세스 내부에서 동작하므로 **두 개를 돌리면 코디네이터 부하가 두 배가 된다** (NFR-PERF-03 위반 위험).

**영향**

| 대상 | 영향 |
|---|---|
| R1 범위 | 6개 → **5개** (FR-PORTAL, FR-QUERY-LIVE, FR-CLUSTER-HEALTH, FR-AUDIT-ACTION, FR-LOG-DEEPLINK) |
| `src/event-listener/` | R1에서 생성하지 않음 |
| `data-pipeline-dev` | R1 배정 작업 없음 |
| **B4** (히스토리 저장소) | **R1 블로커에서 해제**, 통합 시점으로 이월 |
| FR-LD-01 | 딥링크 진입점이 "실행 중 쿼리 / 노드 / 헬스"로 축소. **완료 쿼리 진입점은 R1에 없다** |
| FR-PT-02 | 링크 허브에 기존 히스토리 시스템 추가 |
| R2 FR-WL-05, FR-BM-05 | 기존 시스템 데이터에 의존 → **통합 전까지 설계만, 구현 보류** |

**뒤집는 조건**: 기존 시스템이 `QueryContext.resourceGroupId` 를 보존하지 않거나, TMS가 조회 가능한 인터페이스를 제공하지 못하는 것으로 밝혀지면 R2 범위를 재검토해야 한다.

**관련**: `REQUIREMENTS.md` FR-QUERY-HISTORY 절, `BACKLOG.md` §3 B4

---

## D-005 — TMS는 전용 서비스 계정 `tms-svc` 를 쓴다 (`prometheus_scraper` 재사용 금지)

| 항목 | 내용 |
|---|---|
| **날짜** | 2026-08-06 |
| **결정자** | `trino-expert` 검증 → Platform Owner 승인 |
| **상태** | ✅ **확정 및 적용 완료 (2026-08-06)** — `rules.json` 규칙 추가, 계정 발급, `prometheus_scraper` 권한 축소 모두 완료 |

**결정**: TMS는 Trino 호출에 **전용 basic auth 계정 `tms-svc`** 를 사용한다. 기존 `prometheus_scraper` 계정을 재사용하지 않는다.

**근거**

1. **기능상 불가**: 현재 `rules.json` 의 `queries` 첫 규칙이 `prometheus_scraper` 에 `allow: []` 다. first-match-wins이므로 이 계정은 `view`·`kill` 이 전부 거부된다 → FR-QUERY-LIVE와 FR-QL-04가 동작하지 않는다.
2. **실패 방식이 위험**: `GET /v1/query` 는 403이 아니라 **빈 목록**을 반환한다(필터 기반 거부). "실행 중 쿼리 0건"으로 보여 한가한 정상 클러스터와 구별되지 않는다.
3. **감사 추적성**: 계정을 공유하면 Trino 측에서 "Prometheus 스크레이핑"과 "TMS 액션"을 구분할 수 없다.
4. **권한 분리**: `prometheus_scraper` 규칙을 TMS에 맞게 고치면, Prometheus 스크레이퍼에 쿼리 kill 권한을 주게 된다.

**필요 권한 (최소권한)**

| 섹션 | `tms-svc` | 비고 |
|---|---|---|
| `system_information` | `["read"]` | **`write` 없음.** R3 graceful shutdown 승인 시 추가 |
| `queries` | `["view", "kill"]` | **`execute` 없음** — 원칙 A1(TMS는 SQL을 제출하지 않는다)의 강제 수단 |

**부수 권고 (별건)**: `prometheus_scraper` 의 `system_information` 을 `["read","write"]` → `["read"]` 로 축소. 스크레이핑에는 `read` 만 필요하며, `write` 는 graceful shutdown 트리거 권한이다. **해당 계정은 아직 미사용이므로 지금 줄이면 비용이 0이다.**

**뒤집는 조건**: 계정 발급 자체가 불가능한 조직 제약이 있다면 `prometheus_scraper` 의 `queries` 규칙을 `["view","kill"]` 로 바꾸는 대안이 있으나, 위 3·4번 문제를 감수해야 한다.

**관련**: `ARCHITECTURE.md` §6-3-2, `TRINO_VERIFIED.md` §T3-6

---

## D-002 — 저장소를 PUBLIC으로 유지한다

| 항목 | 내용 |
|---|---|
| **날짜** | 2026-08-04 |
| **결정자** | Platform Owner (인간) |
| **상태** | 확정 |

**결정**: `github.com/choseungyoon/trino-management-service` 를 PUBLIC으로 운영한다.

**근거**: 최초 push 전 "사내 인프라 구조·SPOF 위치가 공개된다"는 경고를 안내받고 공개를 선택했다.

**제약**: 자격증명·호스트명·IP·내부 URL은 커밋 금지. `.gitignore`(`config.secret.yaml`, `*.pem`, `.env` 등)에 의존하지 말고 커밋 전 diff를 확인한다.

---

## D-003 — FR-LOGLEVEL을 폐기하지 않고 축소 존치한다

| 항목 | 내용 |
|---|---|
| **날짜** | 2026-08-04 (Bolt 0) |
| **결정자** | `trino-expert` 검증 → 인간 확인 |
| **상태** | 확정 (구현 방식 D-2는 미결) |

**결정**: OSS Trino 477에 런타임 로그레벨 변경 수단이 **존재한다**(JMX MBean `io.airlift.log:name=Logging`). BOLT_0.md가 지시한 "미지원 시 폐기"는 발동하지 않는다. 단 "재시작 후에도 유지" 특성은 삭제한다.

**미결**: 호출 경로 — (A) Jolokia (B) JVM 헬퍼 (C) 기능 드롭. **권고는 (C)**. R4 항목이므로 R1 착수를 막지 않는다.

**근거**: `TRINO_VERIFIED.md` §T1-3
