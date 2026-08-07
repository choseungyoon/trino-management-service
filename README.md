# TMS — Trino Management Service

OSS Trino로 5만 사용자 규모 서비스를 안정 운영하기 위한 자체 관리 플랫폼.
Starburst Enterprise 사용 불가 환경에서 동등한 운영 역량(모니터링·알람·사용자 추적·증설·접근제어 가시성)을 확보하는 것이 목표.

---

## 지금 할 일

**Bolt 0 (검증) 완료.** 결과는 `docs/TRINO_VERIFIED.md`, `docs/BOLT_0_RESULT.md`.
현재 **Bolt 1 = R1 상세 설계** 단계다.

```
Using AI-DLC, Bolt 1(R1 상세 설계)를 수행한다.
docs/TRINO_VERIFIED.md 에 없는 config property / API 경로 / SPI 시그니처는 사용하지 않는다.
```

**R1 범위 (2026-08-06 갱신)** — FR-PORTAL, FR-QUERY-LIVE, FR-CLUSTER-HEALTH, FR-AUDIT-ACTION, FR-LOG-DEEPLINK
**FR-QUERY-HISTORY는 R1에서 제외** — 별도 프로젝트로 이미 구현됨. 추후 통합.

---

## 리포지토리 구조

```
.
├── CLAUDE.md                  # ★ Claude Code 진입점 (절대 규칙)
├── README.md                  # 이 파일
├── docs/
│   ├── BOLT_0.md              # ★ 첫 작업: 검증 전용
│   ├── BACKLOG.md             # 전체 항목 판정 (SETUP/BUILD/DELEGATE/REJECT)
│   ├── REQUIREMENTS.md        # 상세 요구사항 + AC (부록 A = v0.2 추가분)
│   ├── TEAMS.md               # 에이전트 역할·권한·승인 게이트
│   ├── MARKET_RESEARCH.md     # SEP / Cloudera Manager / Datadog 벤치마킹
│   ├── AIOPS.md               # AI Agent 운영 자동화 (R6+)
│   ├── TRINO_VERIFIED.md      # (Bolt 0 산출물) 검증 완료 사실
│   ├── WORKLOAD_PROFILE.md    # (Bolt 0 산출물) 워크로드 특성화
│   ├── BOLTS.md               # (진행 중) Bolt 이력
│   ├── DECISIONS.md           # (진행 중) 의사결정 기록
│   ├── REVIEW_LOG.md          # (진행 중) 리뷰 이력
│   └── runbooks/              # 운영 런북
│       ├── deploy.md          # ⭐ 사내 실환경 배포 (git pull → DB → 기동 → Trino 연결)
│       ├── db-setup.md        # PostgreSQL 초기 구축
│       └── local-account-setup.md  # 로컬 계정 (AD 연동 전까지 임시)
├── src/
│   ├── tms/
│   │   ├── api/               # FastAPI 라우트
│   │   ├── clients/           # Trino / Gateway / OPA 클라이언트
│   │   ├── core/              # 인증·인가·감사 미들웨어
│   │   ├── ingest/            # 이벤트 수집
│   │   └── web/               # UI
│   ├── event-listener/        # Trino EventListener 플러그인 (Java)
│   └── routing-service/       # External Routing Service (R4)
├── ops/
│   ├── ansible/               # 설정 배포 / 증설
│   ├── systemd/               # 유닛 파일
│   ├── packer/                # 골든 이미지
│   ├── prometheus/
│   ├── alertmanager/
│   └── grafana/
├── config/
│   ├── config.yaml            # 일반 설정
│   └── config.secret.yaml     # ★ gitignore 대상
└── tests/
```

---

## 핵심 설계 결정 (확정)

| 결정 | 내용 |
|---|---|
| 인프라 | VM + systemd. **K8s 미사용** |
| 증설 | 수동/스크립트. **확장 단위는 워커가 아니라 클러스터** |
| 접근제어 | OPA policy-as-code, Git 관리. **권한 UI 만들지 않음** |
| 업그레이드 | **Blue/Green만.** in-place 금지 (코디네이터 HA 부재) |
| 클러스터 간 분배 | **least-loaded 라우터.** 정적 가중치 미사용 |
| 관측성 | Prometheus + Grafana + Alertmanager 위임 |
| 로그 | Loki 또는 OpenSearch 위임. TMS는 딥링크만 |

---

## 절대 원칙

1. **NFR-ISOLATION** — TMS가 죽어도 쿼리는 산다. 쿼리 경로에 개입하지 않는다.
2. **검증 없이 단정하지 않는다** — Trino 477 공식 문서 확인 필수.
3. **쓰기 액션은 reason 필수 + 감사 기록.**
4. **파괴적 액션은 안전 시퀀스를 건너뛸 수 없다.**
5. **비목표를 침범하지 않는다.**

상세는 `CLAUDE.md` 참조.

---

## 릴리스 로드맵

| R | 목표 | 주요 기능 |
|---|---|---|
| R1 | 지금 무슨 일이 일어나는가 | 포털, **실행 중 쿼리**, 헬스, 감사, 로그 딥링크 (쿼리 히스토리는 별도 프로젝트) |
| R2 | 측정하고 비교할 수 있다 | 워크로드 뷰, 라우팅 조회, Gateway 콘솔, SLO, 벤치마크 |
| R3 | 안전하게 조작할 수 있다 | Fleet, 설정변경/재시작, drift 추적 |
| R4 | 세밀하게 제어할 수 있다 | 카탈로그, OPA 가시성, 로그레벨, 라우팅 서비스 |
| R5 | 클러스터를 찍어낼 수 있다 | 프로비저닝, Blue/Green 업그레이드 |
| R6+ | 스스로 운영한다 | AIOps (`AIOPS.md`) |

---

## Blocker 현황 (2026-08-06)

| # | 내용 | 상태 |
|---|---|---|
| ~~B1~~ | Gateway charset 이슈 | **해소** — 업스트림 수정됨(Gateway 19). 조치 = 업그레이드 |
| ~~B2~~ | `catalog.management` 동작 | **해소** — `dynamic` 동작 확인. `ALTER CATALOG`는 477에 부재 |
| ~~B4~~ | 히스토리 저장소 선정 | **R1 범위 밖으로 이월** — 별도 프로젝트가 이미 담당 |
| ~~B5~~ | 런타임 로그레벨 API | **해소** — OSS 477에 존재(JMX MBean). REST 아님 → FR-LOGLEVEL 축소 존치 |
| **B6** | **운영 Gateway 버전·설정 확인** | **미해소** — 플랫폼팀 확인 필요 |

상세는 `docs/BOLT_0_RESULT.md` §2.

---

## 즉시 착수 가능한 SETUP 항목

개발을 기다릴 필요가 없다. `BOLT_0.md` Task 6 참조.

우선순위: **S1** (`QueryCountBasedRouterProvider` 활성화 — 클러스터 성능 편차 즉시 완화), **S5** (PostgreSQL 분리 — 현존 SPOF 제거)
