# WORK_BOARD — 작업 보드 스냅샷

> **자동 생성이다. 손으로 고치지 마라** — `tms-work-export` 가 덮어쓴다.
> 상태의 주인은 TMS 의 보드이고, **근거의 주인은 각 항목이 가리키는 문서**다.
> 둘이 어긋나면 문서가 이긴다.

> 생성 시각: 2026-08-24 10:18 UTC

| 상태 | 건수 |
|---|---|
| Needs a decision | 1 |
| Blocked | 14 |
| In progress | 1 |
| Planned | 4 |
| Done | 18 |
| Dropped | 0 |

---

## Needs a decision (1)

> Waiting on a person. Nothing else will move this.

| 키 | 제목 | 종류 | 릴리스 | 막는 것 | 근거 문서 |
|---|---|---|---|---|---|
| `D-2` | restart_mode 를 ansible 로 전환할 것인가 | decision | — | 사람 결정 — TMS 호스트가 전 노드 SSH 를 갖는다 | `docs/NEXT_STEPS.md` |

## Blocked (14)

> Waiting on something named — see what it is blocked by.

| 키 | 제목 | 종류 | 릴리스 | 막는 것 | 근거 문서 |
|---|---|---|---|---|---|
| `D-007` | R1 인증은 로컬 계정. AD 연동은 이월 | decision | R2 | AD 사양 확보 | `docs/DECISIONS.md` |
| `FR-BM-02` | 컴포넌트별 CPU/Net/Disk 시계열 수집 | requirement | R2 | Prometheus 미구축 (NEXT_STEPS W-6) | `docs/REQUIREMENTS.md` |
| `FR-BM-05` | 프로덕션 쿼리 샘플 기반 세트 생성 | requirement | R2 | 히스토리 프로젝트 통합 (D-001) | `docs/REQUIREMENTS.md` |
| `FR-CATALOG` | 카탈로그 등록/제거 | requirement | R4 | catalog.management=dynamic 이 experimental · Hive/Iceberg 무중단 제거 불가 · catalog.store 미결 | `docs/BACKLOG.md` |
| `FR-CO-01` | 클러스터 설정 조회·변경 | requirement | R3 | 조회용 플레이북을 사람이 써야 한다 | `docs/REQUIREMENTS.md` |
| `FR-FD-01` | 노드별 config 체크섬 수집·비교 | requirement | R3 | 체크섬 수집 플레이북이 선행 | `docs/REQUIREMENTS.md` |
| `FR-GW-04` | Gateway databaseCache 폴백 표시 | requirement | R2 | Gateway 가 캐시 적중 신호를 노출하지 않는다 — 화면은 결과만 말한다 | `docs/REQUIREMENTS.md` |
| `FR-OPA` | OPA 정책 상태 가시성 | requirement | R4 | OPA 미배포 (NEXT_STEPS W-3) | `docs/REQUIREMENTS.md` |
| `FR-ROUTING-SVC` | External Routing Service | requirement | R4 | TMS 가 쿼리 경로에 들어간다 · 분기 근거가 될 워크로드 데이터 없음 | `docs/BACKLOG.md` |
| `FR-SLO` | SLO / Error Budget | requirement | R2 | 목표값(사람 결정) + 워크로드 데이터 미수집 | `docs/REQUIREMENTS.md` |
| `V-8` | 벤치마크 사내 검증 — 거부 경로부터 | task | R2 | Gateway 연동 + ExecuteQuery 부여가 선행 | `docs/runbooks/onsite-checklist.md` |
| `W-2` | Gateway API 역할 계정 발급 | task | R2 | 타 팀 | `docs/NEXT_STEPS.md` |
| `W-3` | 워커 OPA 배포 | task | R3 | 타 팀 · V-5(graceful shutdown 실증) 선행 | `docs/NEXT_STEPS.md` |
| `W-6` | Prometheus + Grafana / 로그 수집 | task | R2 | 타 팀 · FR-BM-02 와 FR-LOG-DEEPLINK 의 선행 | `docs/NEXT_STEPS.md` |

## In progress (1)

> Being built now.

| 키 | 제목 | 종류 | 릴리스 | 막는 것 | 근거 문서 |
|---|---|---|---|---|---|
| `W-5` | Gateway DB 를 VM1 에서 분리 + HA | task | — | 인프라 — 사용자가 처리 중 | `docs/NEXT_STEPS.md` |

## Planned (4)

> Agreed and unblocked, not started.

| 키 | 제목 | 종류 | 릴리스 | 막는 것 | 근거 문서 |
|---|---|---|---|---|---|
| `V-7` | 작업 보드 사내 검증 (화면 + append-only) | task | R2 | — | `docs/runbooks/onsite-checklist.md` |
| `V-9` | 리소스 그룹 편집 사내 검증 (010/011 선행) | task | R2 | — | `docs/runbooks/onsite-checklist.md` |
| `W-1` | NFR-PERF-03 프로덕션 실측 (피크 시간대) | task | R1 | 사람이 돌려야 한다 — R1 DoD 마지막 항목 | `docs/NEXT_STEPS.md` |
| `W-8` | 마이그레이션 010~017 적용 + 보드 초기 적재 | task | R2 | — | `docs/runbooks/onsite-checklist.md` |

## Done (18)

> Built and in the repository.

| 키 | 제목 | 종류 | 릴리스 | 막는 것 | 근거 문서 |
|---|---|---|---|---|---|
| `D-001` | 쿼리 히스토리는 기존 프로젝트 소관 — R1 범위 제외 | decision | R1 | — | `docs/DECISIONS.md` |
| `D-004` | TMS 저장소는 전용 PostgreSQL 인스턴스 | decision | R1 | — | `docs/DECISIONS.md` |
| `D-009` | 재시작 실행 방식은 설정으로 고르고 기본값은 manual | decision | R3 | — | `docs/DECISIONS.md` |
| `D-010` | 리소스 그룹을 db 매니저로. TMS PostgreSQL 전용 schema | decision | R2 | — | `docs/DECISIONS.md` |
| `D-011` | UI 는 서버 렌더. SPA 프레임워크 도입 안 함 | decision | — | — | `docs/DECISIONS.md` |
| `D-012` | tms-svc 에 ExecuteQuery 부여. A1 강제를 코드로 이동 | decision | R3 | — | `docs/DECISIONS.md` |
| `D-013` | 작업 보드. 상태는 보드가, 근거는 문서가 갖는다 | decision | R2 | — | `docs/DECISIONS.md` |
| `FR-BM-01` | 표준 쿼리 세트 실행 | requirement | R2 | — | `docs/REQUIREMENTS.md` |
| `FR-BM-03` | 벤치마크 실행 간 비교 | requirement | R2 | — | `docs/REQUIREMENTS.md` |
| `FR-BM-04` | 벤치마크 프로덕션 보호 — 라우팅 그룹 제외 강제 | requirement | R2 | — | `docs/REQUIREMENTS.md` |
| `FR-BOARD` | 작업 보드 — 상태·요청·댓글 + 저장소 내보내기 | requirement | R2 | — | `docs/REQUIREMENTS.md` |
| `FR-FL-02` | 미조인 워커 식별 | requirement | R3 | — | `docs/REQUIREMENTS.md` |
| `FR-FL-04` | 증설 플레이북 실행 훅 | requirement | R3 | — | `docs/REQUIREMENTS.md` |
| `FR-FL-05` | 실행 이력 및 진행 상태 추적 | requirement | R3 | — | `docs/REQUIREMENTS.md` |
| `FR-WL-07` | 리소스 그룹 설정 트리 조회 + 실행 상태 대조 | requirement | R2 | — | `docs/DESIGN_WL07.md` |
| `FR-WL-08` | 리소스 그룹 값 수정 | requirement | R2 | — | `docs/DESIGN_WL07.md` |
| `FR-WL-09` | 리소스 그룹·셀렉터 추가/삭제 | requirement | R2 | — | `docs/DESIGN_WL07.md` |
| `FR-WL-10` | 리소스 그룹 변경 이력 + 되돌리기 | requirement | R2 | — | `docs/DESIGN_WL07.md` |

## Dropped (0)

> Decided against. Kept so the reasoning stays findable.

_없음._

---

**열린 항목 20건.** 착수 전에 이 파일을 읽는다 — 특히 "Needs a decision" 은 사람이 답하기 전까지 아무것도 움직이지 않는 항목이다.
