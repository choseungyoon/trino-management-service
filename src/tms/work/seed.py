"""What the board starts with (FR-BOARD).

Hand-curated, not parsed. Parsing `DECISIONS.md` and `REQUIREMENTS.md` was the
first idea and it is the wrong one: those documents are prose, their shape
changes whenever a decision needs a different shape, and a parser would either
break constantly or quietly drop the items it stopped recognising - which on a
board reads as "nothing is waiting on you".

So each entry below is a pointer, and the pointer is all the board claims to
own besides status. The title exists to be recognisable; the reasoning stays in
the document named by `source_doc`, and the screen says so.

Seeding is idempotent: an item whose key already exists is left exactly as it
is, because by then its status belongs to whoever has been moving it.

Python 3.9 compatible.
"""

from tms.work.items import (
    BLOCKED,
    DECISION,
    DONE,
    DROPPED,
    IN_PROGRESS,
    NEEDS_DECISION,
    PLANNED,
    REQUIREMENT,
    TASK,
)

#: (key, kind, title, status, release, blocked_by, source_doc)
SEED = (
    # ---------------------------------------------------------- decisions
    ("D-001", DECISION, "쿼리 히스토리는 기존 프로젝트 소관 — R1 범위 제외",
     DONE, "R1", None, "docs/DECISIONS.md"),
    ("D-004", DECISION, "TMS 저장소는 전용 PostgreSQL 인스턴스",
     DONE, "R1", None, "docs/DECISIONS.md"),
    ("D-007", DECISION, "R1 인증은 로컬 계정. AD 연동은 이월",
     BLOCKED, "R2", "AD 사양 확보", "docs/DECISIONS.md"),
    ("D-009", DECISION, "재시작 실행 방식은 설정으로 고르고 기본값은 manual",
     DONE, "R3", None, "docs/DECISIONS.md"),
    ("D-010", DECISION, "리소스 그룹을 db 매니저로. TMS PostgreSQL 전용 schema",
     DONE, "R2", None, "docs/DECISIONS.md"),
    # Superseded, kept: a reversed decision is still the record of why it was
    # made, which is the answer when the same question comes back.
    ("D-011", DECISION, "UI 는 서버 렌더. SPA 미도입 — D-016 으로 대체됨",
     DONE, None, None, "docs/DECISIONS.md"),
    ("D-012", DECISION, "tms-svc 에 ExecuteQuery 부여. A1 강제를 코드로 이동",
     DONE, "R3", None, "docs/DECISIONS.md"),
    ("D-013", DECISION, "작업 보드. 상태는 보드가, 근거는 문서가 갖는다",
     DONE, "R2", None, "docs/DECISIONS.md"),
    ("D-014", DECISION, "벤치마크 쿼리 세트를 설정에서 DB 로. 화면에서 편집한다",
     DONE, "R2", None, "docs/DECISIONS.md"),
    ("D-015", DECISION, "벤치마크는 운영 중에도 실행. 제외 여부는 차단이 아니라 기록",
     DONE, "R2", None, "docs/DECISIONS.md"),
    ("D-016", DECISION, "프론트를 React 19 SPA 로. 런타임 Node 없음",
     DONE, None, None, "docs/DECISIONS.md"),
    ("W-9", TASK, "화면 12개 × 필요한 API 목록화 (docs/FRONTEND_PLAN.md)",
     DONE, None, None, "docs/FRONTEND_PLAN.md"),
    ("D-2", DECISION, "restart_mode 를 ansible 로 전환할 것인가",
     NEEDS_DECISION, None,
     "사람 결정 — TMS 호스트가 전 노드 SSH 를 갖는다", "docs/NEXT_STEPS.md"),

    # ------------------------------------------------------- requirements
    ("FR-WL-07", REQUIREMENT, "리소스 그룹 설정 트리 조회 + 실행 상태 대조",
     DONE, "R2", None, "docs/DESIGN_WL07.md"),
    ("FR-WL-08", REQUIREMENT, "리소스 그룹 값 수정",
     DONE, "R2", None, "docs/DESIGN_WL07.md"),
    ("FR-WL-09", REQUIREMENT, "리소스 그룹·셀렉터 추가/삭제",
     DONE, "R2", None, "docs/DESIGN_WL07.md"),
    ("FR-WL-10", REQUIREMENT, "리소스 그룹 변경 이력 + 되돌리기",
     DONE, "R2", None, "docs/DESIGN_WL07.md"),
    ("FR-BOARD", REQUIREMENT, "작업 보드 — 상태·요청·댓글 + 저장소 내보내기",
     DONE, "R2", None, "docs/REQUIREMENTS.md"),
    ("FR-FL-02", REQUIREMENT, "미조인 워커 식별",
     DONE, "R3", None, "docs/REQUIREMENTS.md"),
    ("FR-FL-04", REQUIREMENT, "증설 플레이북 실행 훅",
     DONE, "R3", None, "docs/REQUIREMENTS.md"),
    ("FR-FL-05", REQUIREMENT, "실행 이력 및 진행 상태 추적",
     DONE, "R3", None, "docs/REQUIREMENTS.md"),
    # Retitled when it stopped being a gate - the old title read as enforcement.
    ("FR-BM-04", REQUIREMENT, "벤치마크 실행 조건 기록 — Quiet / Serving traffic",
     DONE, "R2", None, "docs/runbooks/benchmark.md"),
    ("FR-BM-01", REQUIREMENT, "표준 쿼리 세트 실행",
     DONE, "R2", None, "docs/runbooks/benchmark.md"),
    ("FR-BM-03", REQUIREMENT, "벤치마크 실행 간 비교",
     DONE, "R2", None, "docs/runbooks/benchmark.md"),
    ("FR-BM-06", REQUIREMENT, "쿼리 세트를 화면에서 관리 + 쿼리별 실행 이력",
     DONE, "R2", None, "docs/DECISIONS.md"),
    ("FR-BM-02", REQUIREMENT, "컴포넌트별 CPU/Net/Disk 시계열 수집",
     BLOCKED, "R2", "Prometheus 미구축 (NEXT_STEPS W-6)", "docs/REQUIREMENTS.md"),
    ("FR-BM-05", REQUIREMENT, "프로덕션 쿼리 샘플 기반 세트 생성",
     BLOCKED, "R2", "히스토리 프로젝트 통합 (D-001)", "docs/REQUIREMENTS.md"),
    ("FR-GW-04", REQUIREMENT, "Gateway databaseCache 폴백 표시",
     BLOCKED, "R2",
     "Gateway 가 캐시 적중 신호를 노출하지 않는다 — 화면은 결과만 말한다",
     "docs/REQUIREMENTS.md"),
    ("FR-SLO", REQUIREMENT, "SLO / Error Budget",
     BLOCKED, "R2", "목표값(사람 결정) + 워크로드 데이터 미수집",
     "docs/REQUIREMENTS.md"),
    ("FR-CO-01", REQUIREMENT, "클러스터 설정 조회·변경",
     BLOCKED, "R3", "조회용 플레이북을 사람이 써야 한다", "docs/REQUIREMENTS.md"),
    ("FR-FD-01", REQUIREMENT, "노드별 config 체크섬 수집·비교",
     BLOCKED, "R3", "체크섬 수집 플레이북이 선행", "docs/REQUIREMENTS.md"),
    ("FR-CATALOG", REQUIREMENT, "카탈로그 등록/제거",
     BLOCKED, "R4",
     "catalog.management=dynamic 이 experimental · Hive/Iceberg 무중단 제거 불가 · catalog.store 미결",
     "docs/BACKLOG.md"),
    ("FR-ROUTING-SVC", REQUIREMENT, "External Routing Service",
     BLOCKED, "R4",
     "TMS 가 쿼리 경로에 들어간다 · 분기 근거가 될 워크로드 데이터 없음",
     "docs/BACKLOG.md"),
    ("FR-OPA", REQUIREMENT, "OPA 정책 상태 가시성",
     BLOCKED, "R4", "OPA 미배포 (NEXT_STEPS W-3)", "docs/REQUIREMENTS.md"),

    # -------------------------------------------------------------- tasks
    # 010-017 are applied; 018/019 arrived afterwards, so this is not done.
    ("W-8", TASK, "마이그레이션 010~019 적용 + 보드 초기 적재",
     IN_PROGRESS, "R2", "010~017 적용 완료 · 018/019 미적용",
     "docs/runbooks/onsite-checklist.md"),
    ("V-9", TASK, "리소스 그룹 편집 사내 검증 (010/011 선행)",
     PLANNED, "R2", None, "docs/runbooks/onsite-checklist.md"),
    ("V-7", TASK, "작업 보드 사내 검증 (화면 + append-only)",
     DONE, "R2", None, "docs/runbooks/onsite-checklist.md"),
    # Gateway is configured; what remains is ExecuteQuery and 018/019.
    ("V-8", TASK, "벤치마크 사내 검증 — 거부 경로부터",
     BLOCKED, "R2", "ExecuteQuery 부여 + 마이그레이션 018/019",
     "docs/runbooks/onsite-checklist.md"),
    ("W-1", TASK, "NFR-PERF-03 프로덕션 실측 (피크 시간대)",
     PLANNED, "R1", "사람이 돌려야 한다 — R1 DoD 마지막 항목",
     "docs/NEXT_STEPS.md"),
    ("W-2", TASK, "Gateway API 역할 계정 발급",
     BLOCKED, "R2", "타 팀", "docs/NEXT_STEPS.md"),
    ("W-3", TASK, "워커 OPA 배포",
     BLOCKED, "R3", "타 팀 · V-5(graceful shutdown 실증) 선행",
     "docs/NEXT_STEPS.md"),
    ("W-5", TASK, "Gateway DB 를 VM1 에서 분리 + HA",
     IN_PROGRESS, None, "인프라 — 사용자가 처리 중", "docs/NEXT_STEPS.md"),
    ("W-6", TASK, "Prometheus + Grafana / 로그 수집",
     BLOCKED, "R2", "타 팀 · FR-BM-02 와 FR-LOG-DEEPLINK 의 선행",
     "docs/NEXT_STEPS.md"),
)


def seed(repository, actor: str = "docs") -> int:
    """Insert what is missing. Returns how many were added.

    Idempotent by key: an item that already exists is left alone, status and
    all. Re-seeding must never reset a status someone moved - that would make
    the board lie about work that is actually finished.
    """
    from tms.work.store import DuplicateKey

    added = 0
    for key, kind, title, status, release, blocked_by, source in SEED:
        try:
            repository.create(key=key, kind=kind, title=title, status=status,
                              created_by=actor, release=release,
                              blocked_by=blocked_by, source_doc=source)
            added += 1
        except DuplicateKey:
            continue
    return added
