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
| 클러스터 | 2개 (코디네이터 1 + 워커 12 각각) |
| Gateway | **버전 19** (2026-08-07 확인), 2대, PostgreSQL 공유 (현재 VM1에 co-located = **SPOF**) |
| Gateway 설정 | 백엔드는 **Gateway UI로 등록**. **라우팅 그룹 미사용**(= 기본 랜덤 라우팅). `databaseCache` 활성, **`expireAfterWrite: 10m`** (⚠️ DB 장애 10분 초과 시 라우팅 실패 — §T2-4) |
| LB | IP HASH (**세션 어피니티로 교체 예정 — 임시 우회책**) |
| 인프라 | VM + systemd (**K8s 미사용, 확정**) |
| 증설 | 수동/스크립트 (**확정**) |
| 접근제어 | OPA policy-as-code, 플랫폼팀 Git 관리 (**확정**) |
| 스토리지 | Ceph S3 (Spooling), Iceberg + HMS |
| 목표 규모 | 5만 사용자 |
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

## 문서 구조 (읽는 순서)

| 파일 | 내용 | 언제 읽나 |
|---|---|---|
| `CLAUDE.md` | 이 파일. 절대 규칙 | **항상** |
| `docs/BACKLOG.md` | 전체 개발 항목 및 판정 (SETUP/BUILD/DELEGATE/REJECT) | 작업 범위 확인 시 |
| `docs/REQUIREMENTS.md` | 상세 요구사항 + AC. 부록 A에 v0.2 추가분 | 구현 착수 시 |
| `docs/TEAMS.md` | 에이전트 역할·권한·승인 게이트 | 역할 확인 시 |
| `docs/MARKET_RESEARCH.md` | SEP/Cloudera/Datadog 벤치마킹 근거 | 설계 판단 근거 필요 시 |
| `docs/AIOPS.md` | AI Agent 운영 자동화 (R6+) | R6 이후 |
| `docs/BOLT_0.md` | 첫 작업 명세. 검증 전용 | 참고 (수행 완료) |
| `docs/TRINO_VERIFIED.md` | **검증 완료 사실만 기록** (trino-expert 소유). 여기 없는 property/API는 코드에 넣지 않는다 | **기술 가정 확인 시 항상** |
| `docs/BOLT_0_RESULT.md` | Bolt 0 판정 결과 — Blocker 판정, SETUP 우선순위, 근본원인 체크리스트, 인간 결정 대기 목록 | 착수 범위·우선순위 확인 시 |
| `docs/WORKLOAD_PROFILE.md` | 워크로드 특성화 (**데이터 미수집**). B4·SLO 목표값을 막고 있음 | 사이징·SLO 논의 시 |
| `docs/runbooks/deploy.md` | **사내 실환경 배포 가이드** — git pull → DB → 설정 → systemd → Trino 연결까지 전 과정 | 실환경 배포·업데이트 시 |

---

## 현재 상태 (2026-08-06 갱신)

**단계**: Bolt 0(검증) 완료 → **R1 착수 승인됨 → Bolt 1 (R1 상세 설계) 진행 중**
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

**코드 작성 가능 여부**: **Bolt 1 계획 승인 후.** 설계 확정 전 구현 금지.

**Bolt 0에서 뒤집힌 사전 가정 (기억할 것)**
- **런타임 로그 레벨 변경은 OSS Trino 477에 존재한다** — REST가 아니라 JMX MBean `io.airlift.log:name=Logging`. FR-LOGLEVEL은 폐기가 아니라 축소 존치.
- **TMS는 RMI 없이 HTTP로 JMX를 읽을 수 있다** — `GET /v1/jmx/mbean/{objectName}` (`MANAGEMENT_READ`). 관측성 전반의 수집 경로.
- **Gateway charset 버그(B1)는 업스트림에서 이미 수정됐다** — Gateway 19. 조치는 개발이 아니라 업그레이드.
- **`ALTER CATALOG`는 Trino 477에 없다.** 카탈로그 "변경" 기능을 만들지 않는다.
- **Gateway 19가 리소스 그룹 관리 기능을 제거했다.** FR-WORKLOAD의 데이터 소스는 Trino다.

---

## 착수 명령

R1 착수가 승인되면:

```
Using AI-DLC, Bolt 1(R1 상세 설계)를 수행한다.
docs/TRINO_VERIFIED.md 에 없는 config property / API 경로 / SPI 시그니처는 사용하지 않는다.
```

**승인 전에는 코드를 작성하지 않는다.**
