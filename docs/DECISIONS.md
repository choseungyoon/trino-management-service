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

## D-007 — R1 인증은 로컬 계정으로 시작하고 AD 연동을 뒤로 미룬다

| 항목 | 내용 |
|---|---|
| **날짜** | 2026-08-06 |
| **결정자** | Platform Owner (인간) |
| **상태** | ✅ 확정 (**임시 조치**) |

**결정**: FR-PT-01의 AD/LDAP 연동을 R1에서 구현하지 않는다. 대신 **로컬 계정 + 임시 비밀번호**로 접속한다.

**⚠️ 조정한 부분 (AI 판단, 인간 확인 필요)**: 요청은 "admin 계정 하나"였으나 **이름 있는 로컬 계정 N개**를 지원하도록 만들었다.

> **이유**: 공유 `admin` 계정 하나면 모든 감사 기록의 `actor` 가 `admin` 이 된다. **"누가 이 쿼리를 죽였나"에 답할 수 없게 되며, 이는 FR-AUDIT-ACTION이 존재하는 이유 그 자체다.** 임시 인증 수단이 릴리스의 핵심 기능을 조용히 무력화해서는 안 된다.
> 구현 비용은 동일하다(계정 테이블이 1행이냐 N행이냐의 차이). 지금 계정 하나만 등록해 써도 되고, 나중에 사람별로 늘려도 코드 변경이 없다.

**구현 방식**

| 항목 | 내용 |
|---|---|
| 비밀번호 저장 | **PBKDF2-HMAC-SHA256, 600,000 iterations, 16바이트 salt.** 표준 라이브러리만 사용 (Artifactory 제약) |
| 평문 비밀번호 | **설정에서 거부한다.** `password` 키가 있으면 기동 실패. `password_hash` 만 읽는다 |
| 해시 생성 | `scripts/hash_password.py` — 운영자 로컬에서 실행. 평문이 터미널 밖으로 나가지 않는다 |
| 계정 위치 | `config.secret.yaml`(gitignore) 또는 환경변수. **`config.yaml` 금지 — 저장소가 PUBLIC(D-002)** |
| 세션 | 서명된 stateless 토큰. idle(기본 30분) + absolute(기본 12시간) 이중 만료 (FR-PT-03) |
| 세션 비밀키 | `TMS_SESSION_SECRET` 또는 `portal.session_secret`. **없으면 기동 실패** |
| 임시 비밀번호 | `must_change_password: true` → **변경 전까지 다른 API 전부 403** |
| 로그인 실패 | 5회/5분 잠금. 미존재 사용자와 오답을 메시지·시간 모두 구별 불가하게 처리 |

**알려진 한계 (수용)**

1. **로그아웃이 토큰을 서버측에서 무효화하지 못한다.** stateless 설계의 대가이며, idle 타임아웃이 유일한 상한이다.
2. **비밀번호 변경이 프로세스 재시작 후 사라진다.** 프로세스가 gitignore된 설정 파일을 소유하지 않기 때문이다. API가 새 해시를 응답으로 돌려주고, 운영자가 파일에 반영한다. 응답에 이 사실을 명시한다.
3. 세션 비밀키를 바꾸면 전 사용자가 로그아웃된다.

> **이 세 가지는 AD 연동으로 해소된다. 임시 모드임을 코드가 기동 시 WARN 로그로 알린다.**

**뒤집는 조건**: AD 연동(V9 재개) 시 `local_users` 를 비우면 로컬 인증이 자동 비활성화된다. 코드 제거 없이 전환 가능하다.

**관련**: `REQUIREMENTS.md` FR-PT-01, `docs/runbooks/local-account-setup.md`

---

## D-006 — R1에서 FR-CH-06, FR-LD-02 를 R3으로 이관한다

| 항목 | 내용 |
|---|---|
| **날짜** | 2026-08-06 |
| **결정자** | Platform Owner (인간) |
| **상태** | ✅ 확정 |

**결정**: 아래 두 요구사항을 R1에서 빼고 R3 FR-FLEET에서 다룬다.

| 요구사항 | 이관 사유 |
|---|---|
| FR-CH-06 반복 크래시 감지 | systemd `Restart=` 이력이 필요하다. **R1에는 노드 에이전트가 없다** |
| FR-LD-02 노드 상세 → 로그 딥링크 | **R1에 노드 상세 화면 자체가 없다.** 링크를 걸 진입점이 없다 |

**대안 (R1에서 가능한 범위)**: 헬스 상태 전이 이벤트(FR-CH-07) 이력으로 **사후 확인**은 가능하다. H-01이 짧은 간격으로 `BAD`↔`GOOD`을 반복하면 그 자체가 반복 크래시 신호다. 자동 플래그만 R3으로 미룬다.

**관련**: `HEALTH_TESTS.md` §6, `API_R1.md` §5

---

## D-004 — TMS 자체 저장소는 신규 PostgreSQL 인스턴스

| 항목 | 내용 |
|---|---|
| **날짜** | 2026-08-06 |
| **결정자** | Platform Owner (인간) |
| **상태** | ✅ 확정 |

**결정**: 감사 로그(`audit_action`)와 헬스 이벤트(`health_event`)는 **TMS 전용 신규 PostgreSQL 인스턴스**에 저장한다.

**근거**

1. **규모가 작다** — 감사는 일 수백 건, 헬스 이벤트는 상태 전이 시에만. Elasticsearch를 검토할 이유가 없다.
2. **Gateway PostgreSQL과 분리한다** — Gateway DB는 쿼리 라우팅(queryId→backend 조회)에 관여하므로 **쿼리 경로의 일부**다. TMS가 부하를 얹으면 NFR-ISOLATION 취지에 어긋난다.
3. 감사 로그는 append-only + 정형 검색이다. 관계형이 맞다.

**함의**: 이 DB가 죽으면 **TMS 쓰기 액션이 전면 503이 된다** (AU1, 의도된 설계). 조회 기능도 스냅샷을 못 읽어 제한된다. → **가용성 설계를 별도로 검토해야 한다** (백업/복구 절차는 `sre-agent` 소관).

**B4와의 구분**: B4(대용량 쿼리 히스토리 저장소)는 **별개이며 여전히 미결**이다. D-001로 R1 범위 밖이 되어 통합 시점으로 이월됐다.

**관련**: `AUDIT_MODEL.md` §2, `ARCHITECTURE.md` §4

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
