# CLAUDE.md — TMS 프로젝트 컨텍스트

> 이 파일은 Claude Code가 매 세션 시작 시 읽는 진입점이다. **모든 에이전트는 작업 전 이 파일을 읽는다.**

---

## 프로젝트 개요

**TMS (Trino Management Service)** — Starburst Enterprise를 사용할 수 없는 환경에서, OSS Trino로 5만 사용자 규모 서비스를 안정 운영하기 위한 자체 관리 플랫폼.

**방법론**: AI-DLC (Inception → Construction → Operations, Bolt 단위)

---

## 환경 사실 (변경 시 이 파일을 갱신할 것)

| 항목 | 값 |
|---|---|
| Trino 버전 | **477** |
| 클러스터 | 2개 (코디네이터 1 + **워커 11** 각각, 2026-08-13 확인). `node.environment` 는 **클러스터마다 다른 값** |
| 노드 사양 | 서버당 RAM **560GB**, JVM `-Xmx 250G`, `memory.heap-headroom-per-node 30GB` → 워커 쿼리 풀 220GB, **클러스터 총 2,420GB** |
| 메모리 상한 | **`query.max-memory` 적용 완료 (2026-08-15).** 이전 값 4016GB 는 클러스터 총량보다 커서 상한이 없는 상태였다. ⚠️ 힙이 아직 250G 면 클러스터가 2,420GB 이므로 900GB = **37%** 다 — 힙을 400G 로 올리면 24% 가 된다. `query.max-memory-per-node` 는 176GB 유지 (270GB 는 힙 400G 를 요구한다) |
| 리소스 그룹 | **db 매니저** (D-010, 2026-08-14 사내 적용). TMS PostgreSQL 의 `trino_resource_groups` schema. 값 변경은 `UPDATE` → 10초 반영, 재시작 없음. **group provider 없음** (`etc/group-provider.properties` 부재) → `userGroup`/`user_group_regex` 셀렉터는 영구 미매칭 |
| Gateway | **버전 19** (2026-08-07 확인), 2대, PostgreSQL 공유 (현재 VM1에 co-located = **SPOF**) |
| Gateway 설정 | 백엔드는 **Gateway UI로 등록**. **라우팅 그룹 미사용**(= 기본 랜덤 라우팅). `databaseCache` 활성, **`expireAfterWrite: 10m`** (⚠️ DB 장애 10분 초과 시 라우팅 실패 — §T2-4) |
| LB | IP HASH (**세션 어피니티로 교체 예정 — 임시 우회책**) |
| 인프라 | VM + systemd (**K8s 미사용, 확정**) |
| 증설 | 수동/스크립트 (**확정**) |
| 접근제어 | OPA policy-as-code, 플랫폼팀 Git 관리 (**확정**) |
| 스토리지 | Ceph S3 (Spooling), Iceberg + HMS |
| 규모 | **현재 약 50명**, 목표 5만 사용자 (2026-08-08 확인) |
| 운영 단계 | **아직 운영 서비스 아님** — 설정 변경이 용이한 시기 |
| Python | 3.9+ 호환 필수 |
| 백엔드 | FastAPI + systemd |

**사내 제약**
- 외부 PyPI 직접 접근 불가 → Artifactory 프록시 경유
- 외부 도구에 코드 붙여넣기 금지 (오류 메시지만 공유 가능)

---

## 절대 규칙 (위반 시 작업 반려)

### 1. 검증 없이 단정하지 않는다
**Trino 477 공식 문서에서 확인하지 않은 config property, API 경로, SPI 시그니처를 제안하거나 코드에 넣지 않는다.**
버전 간 삭제·변경이 잦다. **과거에 존재하지 않는 property를 제안해 클러스터 기동 실패를 유발한 이력이 있다.**
확인 불가 시 "확인 불가"라고 쓴다. 추측으로 채우지 않는다.

### 2. NFR-ISOLATION — 쿼리 경로 불침범
TMS가 완전히 다운되어도 모든 쿼리는 정상 실행되어야 한다.
- 쿼리 실행 프록시 코드 작성 금지
- EventListener는 **비동기 + 버퍼 + 백프레셔**. 저장소 다운 시 **이벤트를 버릴지언정 코디네이터를 블로킹하지 않는다**
- External Routing Service는 실패 시 `defaultRoutingGroup`으로 폴백

### 3. 쓰기 액션은 예외 없이
`reason` 파라미터 필수 (없으면 400) + 감사 기록 + 확인 절차. 관리자 역할 한정.

### 4. 비목표를 침범하지 않는다
아래는 **만들지 않는다.** "있으면 좋을 것 같아서" 추가하는 것을 금지한다.

| 비목표 | 대체 |
|---|---|
| 웹 SQL 에디터 | Superset SQL Lab |
| 메트릭 차트/대시보드 자체 구현 | Grafana |
| 알림 엔진 자체 구현 | Alertmanager |
| 로그 수집·인덱싱·검색 자체 구현 | Loki / OpenSearch |
| 권한 관리 UI (RBAC 편집) | OPA + Git PR |
| 데이터 카탈로그 / 데이터 프로덕트 / LLM 어시스턴트 | 범위 밖 |
| 클러스터 간 정적 가중치 라우팅 | `QueryCountBasedRouterProvider` (least-loaded) |

### 5. 파괴적 액션의 안전 시퀀스
클러스터/컴포넌트 재시작은 **반드시**: routing group 비활성화 → 유입 중단 확인 → 실행 쿼리 drain → 재시작 → 헬스 확인 → 재활성화.
**이 시퀀스를 건너뛰는 경로는 구현하지 않는다.**
워커 제거는 **graceful shutdown 선행 필수**.

### 6. 코드 주석은 영어로 작성한다

### 7. 인간 결정 영역을 침범하지 않는다
`[NEEDS-HUMAN-DECISION]` 태그 발견 시 **즉시 중단하고 질문**한다.

---

## 문서 구조

> **전량이다.** 여기 없는 문서는 없다. 하나를 추가한다면 이 표에도 추가한다 — 목록에 없는 문서는 아무도 읽지 않고, 아무도 읽지 않는 문서는 조용히 틀려진다.
>
> **읽는 순서**: `CLAUDE.md` → (기술 사실이 필요하면) `TRINO_VERIFIED.md` → (다음 할 일이면) `WORK_BOARD.md` → `NEXT_STEPS.md` → 나머지는 필요할 때.

### 항상

| 파일 | 내용 |
|---|---|
| `CLAUDE.md` | 이 파일. 절대 규칙 · 환경 사실 |
| `docs/TRINO_VERIFIED.md` | **검증 완료 사실만 기록** (trino-expert 소유). **여기 없는 property/API/SPI 는 코드에 넣지 않는다** |
| `docs/NEXT_STEPS.md` | **사람이 해야만 진행되는 것 전량** — 결정(D)/확인(V)/작업(W) + 권장 순서 |
| `docs/WORK_BOARD.md` | **착수 전에 읽는다.** TMS 작업 보드(`/work`)의 스냅샷 — 무엇이 결정 대기이고 무엇이 진행 중인가. **자동 생성 (`tms-work-export`) — 손으로 고치지 않는다** |

### 무엇을 만들 것인가

| 파일 | 언제 읽나 |
|---|---|
| `docs/REQUIREMENTS.md` | 구현 착수 시. **릴리스 계획은 부록 B 가 최신** (부록 A = v0.2 추가분) |
| `docs/BACKLOG.md` | 작업 범위 확인 시. 항목별 SETUP/BUILD/DELEGATE/REJECT 판정 |
| `docs/DESIGN_R2.md` | R2 착수 시. 설계 + 착수 가능 여부 판정 |
| `docs/DESIGN_WL07.md` | 리소스 그룹 편집(FR-WL-07~10). **검증 규칙 전량(V1~V11 · W1~W5)은 여기가 출처** |

### 어떻게 만들었나 (구현 참조)

| 파일 | 언제 읽나 |
|---|---|
| `docs/ARCHITECTURE.md` | 컴포넌트 경계·배포 단위·성능 예산 확인 시 |
| `docs/API_R1.md` | 엔드포인트 추가/변경 시 |
| `docs/HEALTH_TESTS.md` | 헬스 테스트 추가·임계값 조정 시 |
| `docs/AUDIT_MODEL.md` | 감사 대상 액션 추가 시 |
| `docs/PERF_MEASUREMENT.md` | NFR-PERF-03 부하 판단 시 |

### 왜 그렇게 정했나

| 파일 | 언제 읽나 |
|---|---|
| `docs/DECISIONS.md` | 결정을 되돌리거나 재확인할 때 (D-001~) |
| `docs/BOLTS.md` | 진행 상태·이력 확인 시 |
| `docs/TEAMS.md` | 역할·승인 게이트 확인 시 |
| `docs/MARKET_RESEARCH.md` | 설계 판단 근거 필요 시 (SEP/Cloudera/Datadog) |

### 손에 들고 하는 것 (런북)

| 파일 | 언제 읽나 |
|---|---|
| `docs/runbooks/deploy.md` | 사내 실환경 최초 배포 (git pull → DB → 설정 → systemd → Trino 연결) |
| `docs/runbooks/upgrade-r2-r3.md` | 운영 중 업데이트 배포 |
| `docs/runbooks/db-setup.md` | PostgreSQL 초기 구축 |
| `docs/runbooks/local-account-setup.md` | 로컬 계정 (AD 연동 전까지) |
| `docs/runbooks/gateway-config-request.md` | 운영팀 협의 시. **로컬 19 실측 기반** — `monitorType` 은 `METRICS` (UI_API 는 401) |
| `docs/runbooks/resource-groups-db.md` | 리소스 그룹 file → db 전환 (D-010) + 메모리 재설정. **한 번에 한 클러스터씩** |
| `docs/runbooks/benchmark.md` | 벤치마크 하네스(FR-BM) 사용. **TMS 는 클러스터를 라우팅에서 빼 주지 않는다** — 확인하고 거부만 한다 |
| `docs/runbooks/executequery-grant.md` | `tms-svc` 에 `ExecuteQuery` 부여 (D-012). **부여 전 OPA 카탈로그 규칙 확인이 조건** |
| `docs/templates/` | 채워 넣는 파일 (클러스터 인벤토리 등) |

### 데이터 대기 / 나중

| 파일 | 언제 읽나 |
|---|---|
| `docs/WORKLOAD_PROFILE.md` | 사이징·SLO 논의 시. **데이터 미수집** — SLO 목표값을 막고 있음 |
| `docs/AIOPS.md` | R6 이후 |

### `docs/archive/` — 현재 상태가 아니다

**착수 범위·우선순위를 여기서 읽지 않는다.** 수행이 끝난 기록이며, 이후 실측에 뒤집힌 내용이 있다. 각 파일 첫머리에 무엇이 뒤집혔는지 적어 두었다. 남겨 둔 이유는 `BACKLOG.md`·`REQUIREMENTS.md` 판정의 출처이기 때문이다.

| 파일 | 무엇 |
|---|---|
| `docs/archive/BOLT_0.md` | Bolt 0 지시서 (수행 완료) |
| `docs/archive/BOLT_0_RESULT.md` | Bolt 0 판정 결과. **§3 의 `monitorType: UI_API` 권고는 틀렸다** |
| `docs/archive/mockups-r1.html` | R1 UI 목업. 실물은 `src/tms/web/` |

---

## 현재 상태 (2026-08-14 갱신)

**단계**: R1 실환경 배포 완료 → Bolt 4 안전 재시작(FR-CO-02) → Fleet(FR-FL-01/03) → **리소스 그룹 db 전환(D-010) 사내 적용 완료 + 편집 화면(FR-WL-07~10) 구현 완료**

**착수 전에 `docs/WORK_BOARD.md` 를 읽는다.** 관리자가 `/work` 화면에서 올린 요청과 각 항목의 현재 상태가 거기에 있다. 파일은 `tms-work-export` 가 만든다 — 보드는 사내망 DB 에 있고, 사외에서 읽을 방법은 이 파일뿐이다.

**둘의 경계 — 겹치는 게 아니라 나뉜다.**

| 문서 | 무엇의 주인 |
|---|---|
| `docs/WORK_BOARD.md` (= `/work`) | **상태**. 무엇이 결정 대기·차단·진행 중인가. 관리자 요청의 접수처 |
| `docs/NEXT_STEPS.md` | **사람이 해야만 진행되는 것**의 상세 — 왜 사람이어야 하는지, 무엇을 확인해야 하는지 |
| `docs/DECISIONS.md`·`REQUIREMENTS.md`·`BACKLOG.md` | **근거**. 왜 그렇게 정했나 |

보드와 문서가 어긋나면 **문서가 이긴다.** 보드는 상태만 갖는다 — 여기에 근거를 복사해 두면 두 개의 진실이 생기고, 그게 부록 B 와 `BACKLOG.md` 가 어긋났던 이유다.

### 재시작 실행 방식 — 켜기 전에 읽을 것

`cluster_ops.restart_mode` 는 **기본 `manual`** 이다. `ansible` 로 바꾸면 **TMS 호스트가 모든 Trino 노드에 SSH 접근**을 갖는다. 편의가 아니라 보안 결정이며 D-009 에 기록돼 있다. Ansible 이 설치돼 있다는 이유로 켜지 않는다.

**독립된 deactivate 토글은 만들지 않는다.** 유입 차단은 안전 시퀀스의 1단계로만 도달할 수 있다 — 별도 토글이 있으면 그것이 곧 절대규칙 5 를 건너뛰는 경로다.

---

**이전 단계**: Bolt 0(검증) 완료 → R1 착수 승인 → Bolt 1/2 (R1 설계·구현)
**미해소 Blocker**: **0건** — B6는 2026-08-07 부분 해소(버전 19 · `databaseCache` 활성 확인). 백엔드 목록 등록 방식만 운영팀 회신 대기이며 R1을 막지 않는다. B1/B2/B3/B5 해소, B4는 R1 범위 밖으로 이월.

### ⛔ R1 범위 변경 (2026-08-06 인간 결정)

**FR-QUERY-HISTORY를 R1에서 제외한다.** 이미 별도 프로젝트로 구현되어 운영 중이다. 추후 두 프로젝트를 통합한다.

| 영향 | 내용 |
|---|---|
| R1 범위 | FR-PORTAL, **FR-QUERY-LIVE**, FR-CLUSTER-HEALTH, FR-AUDIT-ACTION, FR-LOG-DEEPLINK (5개) |
| `src/event-listener/` | **R1에서 만들지 않는다** |
| `data-pipeline-dev` | R1 배정 작업 없음 |
| B4 | R1을 막지 않음. 통합 시점으로 이월 |
| FR-LD-01 | R1 딥링크 진입점은 **실행 중** 쿼리·노드·헬스로 한정. 완료 쿼리는 기존 시스템 소관 |

**금지**: 기존 프로젝트가 이미 하는 일(EventListener 수집, 완료 쿼리 저장/검색)을 TMS에 다시 만들지 않는다.

---

## ⚠️ 실측에 뒤집힌 가정 (기억할 것)

**전부 "문서·통념이 맞다고 여겼다가 재 봤더니 아니었던" 것들이다.** 근거는 모두 `TRINO_VERIFIED.md`.

### Bolt 0 검증에서 (2026-08-04)

- **런타임 로그 레벨 변경은 OSS Trino 477에 존재한다** — REST가 아니라 JMX MBean `io.airlift.log:name=Logging`. FR-LOGLEVEL은 폐기가 아니라 축소 존치.
- **TMS는 RMI 없이 HTTP로 JMX를 읽을 수 있다** — `GET /v1/jmx/mbean/{objectName}` (`MANAGEMENT_READ`). 관측성 전반의 수집 경로.
- **Gateway charset 버그(B1)는 업스트림에서 이미 수정됐다** — Gateway 19. 조치는 개발이 아니라 업그레이드.
- **`ALTER CATALOG`는 Trino 477에 없다.** 카탈로그 "변경" 기능을 만들지 않는다.
- **Gateway 19가 리소스 그룹 관리 기능을 제거했다.** FR-WORKLOAD의 데이터 소스는 Trino다.

### 로컬 실환경 실측에서 (2026-08-10~11)

- **`GET /v1/node` 는 477 에 없다 (404).** "보조 소스"가 아니라 소스가 아니다.
- **`system.runtime.nodes` 는 `PERMISSION_DENIED`** — `ExecuteQuery` 가 필요하고 TMS 는 의도적으로 갖고 있지 않다. 노드 조인 여부는 개수 비교로만 판정한다 (D-1 미결).
- **`trino.metadata:name=DiscoveryNodeManager` 는 477 에 없다** (`trino.node:name=CoordinatorNodeManager` 로 개명). → **Gateway 19 의 `monitorType: JMX` 는 477 에서 못 쓴다.**
- **Gateway 19 `monitorType: UI_API` 는 401** — `/ui/api/stats` 는 폼 로그인 전용. **쓸 수 있는 값은 `METRICS`.** (`archive/BOLT_0_RESULT.md` §3 의 UI_API 권고는 이걸로 폐기됐다)
- **Gateway 19 는 백엔드 활성/비활성 시 `invalidateBackendCache()` 를 호출한다** — 안전 시퀀스 1단계에서 `databaseCache.expireAfterWrite: 10m` 을 기다릴 필요가 없다.
- **ansible-core 는 쓰기 가능한 `HOME` 없이 import 단계에서 죽는다 (exit 5).** `ProtectHome=true` 아래에서 `restart_mode: ansible` 을 쓰려면 `HOME`/`ANSIBLE_HOME` 을 `StateDirectory` 로 돌려야 한다 (구현됨).
