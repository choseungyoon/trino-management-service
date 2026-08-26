# TMS — Trino Management Service

OSS Trino로 5만 사용자 규모 서비스를 안정 운영하기 위한 자체 관리 플랫폼.
Starburst Enterprise 사용 불가 환경에서 동등한 운영 역량(모니터링·알람·사용자 추적·증설·접근제어 가시성)을 확보하는 것이 목표.

---

## 지금 어디까지 왔나 (2026-08-12)

**R1 사내 실환경 배포 완료.** 이후 R2 일부(워크로드 뷰, Gateway 콘솔)와 R3 선행분(안전 재시작, Fleet)까지 구현했다.

| 화면 | 상태 |
|---|---|
| 포털 · 실행 중 쿼리(kill 포함) · 헬스 · 감사 · 로그 딥링크 | R1, 운영 중 |
| 워크로드(리소스 그룹) 뷰 | 기본 비활성 (`workload.enabled`) — NFR-PERF-03 실측 후 켠다 |
| Gateway 백엔드 콘솔 | 운영 중 |
| 안전 재시작 (FR-CO-02) | 구현 완료. `manual` 실환경 검증 완료, `ansible` 모드 준비됨 |
| Fleet 인벤토리 + graceful shutdown | 구현 완료 |

**다음에 무엇을 하느냐**는 `docs/TODO.md` 하나만 보면 된다 — 사내에서 할 것 · 결정 · 타 팀으로 나뉘어 있다.
**무엇을 했느냐**는 `docs/BOLTS.md`.

---

## 문서 지도

> 문서가 여럿인 이유는 **소유자와 수명이 다르기 때문**이다. 아래 분류가 곧 "이 문서를 고쳐도 되는가"의 답이다.

### 매번 읽는 것

| 문서 | 무엇 |
|---|---|
| `CLAUDE.md` | ★ 진입점. 절대 규칙 · 환경 사실 |
| `docs/TRINO_VERIFIED.md` | ★ **기술 사실의 유일한 출처.** 여기 없는 property/API 는 코드에 넣지 않는다 |
| `docs/TODO.md` | 사람이 해야만 진행되는 것 전량 |

### 무엇을 만들 것인가

| 문서 | 무엇 |
|---|---|
| `docs/REQUIREMENTS.md` | 요구사항 + AC. 부록 B = 최신 릴리스 계획 |
| `docs/BACKLOG.md` | 항목별 판정 (SETUP / BUILD / DELEGATE / REJECT) |
| `docs/DESIGN_R2.md` | R2 설계 및 착수 가능 여부 |

### 어떻게 만들었나

| 문서 | 무엇 |
|---|---|
| `docs/ARCHITECTURE.md` | 컴포넌트 경계, 배포 단위, 성능 예산 |
| `docs/API_R1.md` | R1 엔드포인트 명세 |
| `docs/HEALTH_TESTS.md` | 헬스 테스트 카탈로그 (판정식·임계·조치 조언) |
| `docs/AUDIT_MODEL.md` | append-only 감사 데이터 모델 |
| `docs/PERF_MEASUREMENT.md` | NFR-PERF-03 부하 실측 결과 |

### 왜 그렇게 정했나

| 문서 | 무엇 |
|---|---|
| `docs/DECISIONS.md` | 결정 기록 (D-001~). 되돌리려면 여기부터 |
| `docs/BOLTS.md` | Bolt 이력 및 계획 |
| `docs/MARKET_RESEARCH.md` | SEP / Cloudera / Datadog 벤치마킹 |
| `docs/TEAMS.md` | 에이전트 역할·권한·승인 게이트 |

### 손에 들고 하는 것

| 문서 | 무엇 |
|---|---|
| `docs/runbooks/deploy.md` | ⭐ 사내 실환경 배포 전 과정 |
| `docs/runbooks/upgrade-r2-r3.md` | 운영 중 업데이트 배포 절차 |
| `docs/runbooks/db-setup.md` | PostgreSQL 초기 구축 |
| `docs/runbooks/local-account-setup.md` | 로컬 계정 (AD 연동 전까지) |
| `docs/runbooks/gateway-config-request.md` | Gateway 설정 요청서 (실측 근거 포함) |
| `docs/templates/` | 채워 넣는 파일 (인벤토리 등) |

### 아직 데이터가 없는 것 / 나중 것

| 문서 | 무엇 |
|---|---|
| `docs/WORKLOAD_PROFILE.md` | 워크로드 특성화 — **데이터 미수집.** SLO 목표값을 막고 있다 |
| `docs/AIOPS.md` | AI Agent 운영 자동화 (R6+) |

### `docs/archive/` — 읽지 않아도 되는 것

수행이 끝났고 **현재 상태를 반영하지 않는다.** 남겨 둔 이유는 판정의 출처이기 때문이며, 각 파일 첫머리에 무엇이 뒤집혔는지 적어 두었다.

---

## 리포지토리 구조

```
.
├── CLAUDE.md                  # ★ Claude Code 진입점 (절대 규칙)
├── README.md                  # 이 파일
├── PRODUCT.md                 # 제품 정의 (UI 언어·사용자·포지셔닝)
├── docs/                      # 위 "문서 지도" 참조
├── src/tms/
│   ├── api/                   # FastAPI 라우트 + 서비스 계층
│   ├── clients/               # Trino / Gateway / 노드 클라이언트
│   ├── core/                  # 설정·인증·인가·감사 미들웨어
│   ├── collector/             # 폴링 루프 → PostgreSQL 스냅샷 (단일 인스턴스)
│   ├── health/                # 헬스 테스트 판정 엔진
│   ├── fleet/                 # 인벤토리 파싱, 노드 상태
│   ├── ops/                   # 안전 재시작 시퀀스 + 실행기(manual/ansible)
│   └── web/                   # 서버 렌더 UI (Jinja2)
├── migrations/                # SQL 마이그레이션 (순차 적용)
├── ops/systemd/               # tms-api.service, tms-collector.service
├── scripts/                   # 연결 검증, 부하 실측, 비밀번호 해시
├── config/
│   ├── config.yaml            # 일반 설정
│   └── config.secret.yaml     # ★ gitignore 대상
└── tests/                     # 단위 + integration + browser
```

**아직 없는 것** (설계상 의도된 부재): `src/event-listener/` — 별도 히스토리 프로젝트 소관(D-001). `src/routing-service/` — R4.

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
| R6+ | 스스로 운영한다 | AIOps (`docs/AIOPS.md`) |

> R2/R3 는 순서대로 끝나지 않았다. 실제로 무엇이 서 있는지는 맨 위 표를, 무엇이 남았는지는 `docs/TODO.md` D-4 를 본다.
