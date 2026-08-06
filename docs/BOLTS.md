# BOLTS — Bolt 이력 및 계획

> **소유자**: `orchestrator`
> TEAMS.md §5 순서: ① 계획 제시 → **인간 승인** → ② `trino-expert` 선행 검증 → ③ 담당 에이전트 실행 → ④ `reviewer` 게이트 → ⑤ 인간 최종 확인

---

## Bolt 0 — 검증 전용 ✅ 완료

| 항목 | 내용 |
|---|---|
| 기간 | 2026-08-04 |
| 담당 | `trino-expert` 주도 |
| 산출물 | `TRINO_VERIFIED.md`, `BOLT_0_RESULT.md`, `WORKLOAD_PROFILE.md`(초안) |
| 결과 | T1~T3 검증 18/18 완료. Blocker B1/B2/B5 해소, B6 신규 식별 |

---

## Bolt 1 — R1 상세 설계 🟡 **설계 완료 — 인간 검토 대기**

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
| **B7** | **`rules.json` 실물 확인** — ① `system_information` 섹션 존재 여부 ② **`queries` 섹션 존재 여부**(있으면 기본 허용이 깨져 TMS에 `view`/`kill` 규칙 별도 필요) ③ TMS 서비스 계정명 | **Bolt 2 착수 전** |

> **B7이 R1 전체를 막지는 않는다.** `file` 기본값 기준으로 FR-PORTAL·FR-QUERY-LIVE·FR-AUDIT-ACTION·FR-LOG-DEEPLINK와 H-01/H-02는 조치 없이 동작한다. 막히는 것은 **FR-CLUSTER-HEALTH의 JMX 기반 테스트(H-03~H-07)** 뿐이다. → **Bolt 2를 규칙 승인과 병렬 진행 가능.**
| H-02 | `GET /v1/info` 의 기동 상태 필드명 미확정 | Bolt 2 실응답으로 확정. 그전까지 미구현 |
| H-03 | `ActiveCount` 가 코디네이터를 포함하는지 미확정 | Bolt 2 실측 보정 |
| D-004 | 감사·헬스 이벤트 저장소 (PostgreSQL 권고) | **인간 승인 대기** |
| — | 기존 히스토리 시스템의 queryId URL 패턴 | 플랫폼팀 확인. 미확인 시 링크 미렌더링 |

---

## Bolt 2 — R1 구현 (예정)

Bolt 1 승인 후 계획 수립. **착수 전 G-7 확인 필수.**
