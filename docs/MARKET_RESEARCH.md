# TMS Market Research — 운영 관리 서비스 벤치마킹

> **문서 목적**: Trino Management Service(TMS)에 담을 기능을 결정하기 위한 경쟁/참조 제품 조사.
> **판단 원칙**: "좋아 보이니까 넣는다"가 아니라 **"우리 환경(VM/systemd + Trino Gateway + OPA + 5만 사용자)에서 실제 운영 문제를 푸는가"** 로만 채택한다.
> **최종 결정권**: 인간(Platform Owner). 본 문서는 AI가 정리한 근거이며 채택 결정은 인간이 확정한다.

---

## 0. 조사 대상 및 선정 이유

| 제품 | 조사 이유 | 우리와의 관계 |
|---|---|---|
| Starburst Enterprise (SEP) Web UI | Trino 상용판. 기능 범위의 **상한선** 정의 | 직접 경쟁 제품, 사용 불가(라이선스) |
| Cloudera Manager (CM) | 온프렘 VM 기반 대규모 클러스터 관리의 **정석** | 우리와 인프라 형태가 가장 유사 |
| Datadog | 관측성/알림 설계의 **업계 표준 패턴** | 알림 철학과 SLO 모델 차용 |
| Trino Gateway UI (OSS) | 이미 보유 중 | 중복 개발 방지 기준선 |

---

## 1. Starburst Enterprise Web UI

### 1-1. 제공 기능 전수 (공식 문서 기준)

| 메뉴 | 기능 | 설명 |
|---|---|---|
| Overview | 클러스터 현황 | 시작 시 기본 탭. 클러스터 현재 활동 요약 |
| Query | Query editor | 웹 기반 SQL 작성/실행 클라이언트 |
| Query | Saved queries | 쿼리 탭 저장/관리/공유, "Shared with me"로 타인 공유 쿼리 미리보기·실행 |
| Query | Query overview | 실행 중/최근 쿼리 목록, ID 클릭 시 상세 |
| Data | Catalogs | 카탈로그 트리 탐색(스키마·테이블·뷰·컬럼), 커넥터명 표시, 각 레벨 description 편집 |
| Data | Data maintenance | 파일 compaction, 통계 수집, 오래된 스냅샷·미사용 파일 삭제 |
| Data | Schema discovery | 알려진 스키마 위치의 신규 테이블·뷰 식별 및 등록 |
| Data products | — | 큐레이션된 데이터 자산 발행/검색/관리 |
| AI | AIDA / Models | 대화형 분석 어시스턴트, LLM·임베딩 모델 연결 |
| Access control | Roles and privileges | 내장 RBAC(BIAC). 관리자가 UI로 데이터 접근 제어 |
| Access control | Masks and filters | 행·컬럼 수준 데이터 제한 |
| Admin | Workload management | 리소스 그룹 관리·모니터링, 활동 확인, 관련 쿼리 식별, 과도한 제한으로 인한 병목 규명 |
| Admin | Cluster history | 최근 쿼리/지정 기간의 클러스터 리소스 소비 |
| Admin | Cluster logging | 로그 레벨 변경. log.properties 편집과 동일 효과이나 **재시작 불필요**. sysadmin 역할 필요. 전 노드 적용, 재시작 후에도 유지 |
| Admin | Audit log | 쿼리 실행 감사 추적 |
| Admin | Usage metrics | 기간별 사용량 개요 + 비용 추정 |
| — | Notification settings | 카탈로그 로드 실패, SQL job 실패 등 이벤트 알림 생성 |
| 공통 | 세션 타임아웃 | `web-ui.session-timeout` + `insights.user-inactivity-timeout`(클릭·키입력 추적, 비활성 시 자동 로그아웃) |
| 공통 | Switch roles | 부여된 역할 간 전환, `*` 옵션은 `SET ROLE ALL`과 동일 |
| 공통 | Client token (`/ui/token`) | JWT 토큰 노출 → JWT 인증 클라이언트 연결용 |
| 공통 | Customize login | 로고 업로드, 배너 메시지 |
| 공통 | 테마 | Light / Dark / System |

### 1-2. 핵심 인사이트

**(A) SEP조차 인가 UI를 자체 흡수하지 않았다.**
SEP는 Help 메뉴에 외부 인가 앱(Apache Ranger 등) UI 링크를 설정으로 넣게 해뒀다
(`insights.authorization-application.url`, `insights.authorization-application.label`).
→ **결론: 인가 관리 UI는 별도 도구에 위임하는 것이 벤더도 인정한 패턴.** 우리는 OPA + Git이 그 도구다.

**(B) UI로 보이지만 실제로는 엔진 기능인 것들.**
BIAC(Roles/privileges, Masks & filters), Data products, AIDA는 화면을 베낀다고 생기지 않는다. 백엔드 전체를 만들어야 한다.
→ **결론: 범위 밖.**

**(C) 라이선스 없는 클러스터는 기본 Trino Web UI로 표시된다.**
공식 문서가 밝히듯 Trino UI에는 Insights, data products, query editor, domain management가 없다.
→ 우리가 OSS로 채워야 할 **정확한 격차 목록**이 이것이다.

### 1-3. 채택 판정

| 기능 | 판정 | 근거 |
|---|---|---|
| Query overview / Cluster history / Audit log | **채택(통합)** | Trino 히스토리는 코디네이터 heap 상주 → 재시작 시 소실. EventListener 외부화 필수 |
| Workload management | **채택** | 리소스 그룹 관점 뷰가 OSS에 부재. 5만 동시성 관리의 핵심 |
| Cluster logging | **채택** | 재시작 없는 로그 레벨 변경 = 프로덕션 진단 필수. 구현 난이도 대비 효용 큼 |
| Notification settings | **비채택 → Alertmanager** | 재개발 불필요 |
| Overview / Usage metrics | **비채택 → Grafana** | 대시보드가 정확히 이 역할 |
| Query editor / Saved queries | **비채택 → Superset SQL Lab** | 이미 보유. 최저 ROI |
| Roles/privileges, Masks & filters | **비채택** | SEP 엔진 종속 + OPA policy-as-code 결정과 충돌 |
| Catalogs / Data maintenance / Schema discovery | **비채택(추후)** | Iceberg 유지보수는 SQL 프로시저 + 스케줄러가 정공법 |
| Data products / AI | **비채택** | 별도 제품군, 범위 밖 |
| Switch roles / `/ui/token` | **부분 채택** | JWT 토큰 발급 페이지는 사내 클라이언트 온보딩에 유용 |
| Customize login / 테마 | **비채택** | 안정성 기여도 0. 단, 다크모드는 저비용이므로 P3 |

---

## 2. Cloudera Manager

> **왜 중요한가**: CM은 온프렘 VM 기반 대규모 분산 클러스터 관리의 레퍼런스다. 우리 인프라 형태(VM/systemd, K8s 아님)와 가장 유사하며, SEP UI에 **없는** 운영 기능을 다수 보유한다.

### 2-1. 차용할 핵심 개념

**(A) Health Test (건강 검진) 모델 — 최우선 차용**

CM은 서비스/역할 인스턴스 레벨에서 health test 결과를 보여주고, 진단을 돕는 차트를 함께 제시한다.
결정적으로 **health test는 상태가 나빠졌을 때 취할 수 있는 조치에 대한 조언(advice)을 포함한다.**

- 개별 health test를 **활성/비활성**할 수 있고, 어떤 test가 알림을 유발할지, 어떤 test가 전체 health 계산에 포함될지 결정 가능
- 개별 test와 별개로 **"roll-up" health test**를 따로 비활성화 가능 → 개별 지표는 나빠도 전체는 정상으로 볼 수 있는 유연성
- 결과가 "Concerning" 또는 "Bad"이면 **이벤트로 Event Server에 전달**
- 임계값 조정으로 언제 이벤트/알림이 되는지 제어

> **왜 이게 Grafana보다 나은가**: Grafana는 "메트릭이 몇이다"를 보여준다. Health Test는 **"정상인가 / 왜 비정상인가 / 무엇을 하라"** 를 보여준다. 5만 사용자 환경의 1차 대응자(운영자)에게는 후자가 훨씬 유용하다.

**(B) 설정 변경 감사 로그 (Configuration Change Audit) — 강력 차용**

CM은 서비스/역할에 수행된 **액션 이력**과 **설정 변경 감사 로그**를 볼 수 있다.
또한 설정 변경 시 **"Reason for change"(변경 사유)를 입력**하게 한다.

> **우리에게 주는 시사점**: TMS에서 graceful shutdown, 워커 증설, 로그 레벨 변경 같은 파괴적 액션을 수행할 때 **누가·언제·왜** 를 반드시 기록해야 한다. 변경 사유 필수 입력은 저비용 고효용 패턴.

**(C) Stale Config / Restart Wizard**

CM은 설정이 변경되었으나 아직 재시작되지 않은 **"stale" 서비스**를 표시하고, 그 옆 아이콘으로 **클러스터 재시작 마법사**를 띄운다.

> **우리에게 주는 시사점**: Ansible로 워커 config를 배포한 뒤 "어떤 노드가 아직 반영 안 됐는가"를 추적하는 **config drift 대시보드**가 필요하다. VM/systemd + 골든 이미지 환경에서 drift는 산발적 장애의 주원인이다.

**(D) 프로세스 관리 계층**

CM은 supervisord로 프로세스를 시작하고, 로그 리다이렉션, 프로세스 실패 통지, 실효 UID 설정 등을 처리한다.
**크래시된 프로세스를 자동 재시작**하며, 시작 직후 반복 크래시하면 해당 역할 인스턴스에 **bad health 플래그**를 세운다.
중요한 안전 특성: **CM Server/Agent를 중지해도 서비스는 내려가지 않고, 실행 중인 역할 인스턴스는 계속 동작한다.**

> **우리에게 주는 시사점 (매우 중요)**:
> 1. 우리의 supervisord는 **systemd**다. `Restart=on-failure` + `StartLimitBurst`로 동일 패턴 구현 가능.
> 2. **"반복 크래시 → bad health 플래그"** 는 그대로 차용. 재시작 루프에 빠진 워커를 조용히 방치하면 안 된다.
> 3. **"관리 도구가 죽어도 서비스는 산다"** — 이것이 TMS의 최상위 설계 원칙이다. TMS는 쿼리 실행 경로에 절대 끼어들지 않는다.

### 2-2. 채택 판정

| CM 개념 | 판정 | TMS 반영 |
|---|---|---|
| Health Test + 조치 조언 | **채택 (P0)** | FR-CLUSTER-HEALTH |
| 설정 변경 감사 + 변경 사유 입력 | **채택 (P0)** | FR-AUDIT-ACTION |
| Stale config / drift 추적 | **채택 (P1)** | FR-FLEET-DRIFT |
| 반복 크래시 → bad health | **채택 (P1)** | FR-CLUSTER-HEALTH |
| 관리도구 다운 ≠ 서비스 다운 | **채택 (아키텍처 원칙)** | NFR-ISOLATION |
| Rolling restart wizard | **부분 채택 (P2)** | 워커 rolling restart는 graceful shutdown과 결합 |
| Host 단위 임계값 기반 디스크 모니터링 | **채택 (P1)** | node_exporter로 충족 → Grafana 위임 |

---

## 3. Datadog

> **왜 중요한가**: Datadog은 기능 자체보다 **알림 설계 철학**이 차용 가치가 있다. 5만 사용자 환경에서 가장 흔한 실패는 "알림이 없어서"가 아니라 **"알림이 너무 많아서 아무도 안 봐서"** 다.

### 3-1. 차용할 핵심 개념

**(A) SLO + Error Budget 모델 — 강력 차용**

Datadog SLO는 SRE 툴킷의 핵심으로, 애플리케이션 성능에 대한 **명확한 목표를 정의하는 프레임워크**를 제공하여 일관된 사용자 경험, 기능 개발과 플랫폼 안정성 간 균형, 커뮤니케이션 개선을 돕는다.
- **롤링 error budget**으로 엔지니어링 우선순위를 정하고 배포 신뢰도를 높인다
- error budget이 소진되고 있을 때 선제적으로 알린다
- SLO 목표/임계값을 걸어 **어떤 SLA가 위험한지 한눈에** 파악
- **SLO가 없는 서비스를 즉시 식별**해 관측 사각지대를 제거

> **우리에게 주는 시사점**: Phase 0에서 정의할 SLO(가용성, p95 지연, 최대 큐 대기시간)를 **TMS 화면의 1급 시민**으로 만들어야 한다. "CPU 80%" 알림이 아니라 **"이번 달 error budget 60% 소진"** 이 경영진과 대화하는 언어다.

**(B) 알림 피로(Alert Fatigue) 방지 — 필수 차용**

업계 통용 원칙:
- **모든 알림은 SLO와 런북에 매핑되어야 한다. 매핑 안 되면 아무도 호출하지 말아야 한다.**
- **증상(사용자 영향)에 알림을 걸고, 원인(모든 CPU 요동)에 걸지 말라**
- 배포·점검 시에는 **downtime을 스케줄**해 예상된 소음을 침묵시켜라
- 심각도로 라우팅 — Alert는 호출, Warning은 Slack
- **90일간 아무도 조치하지 않은 모니터는 소음이다. 삭제하라**
- **No Data 처리**: 조용한 메트릭은 에이전트 사망을 의미할 수 있다. No Data가 알림이어야 하는지 결정하라
- 계절성 메트릭에 고정 임계값을 쓰지 말라 → 이상탐지 사용

**(C) Composite Monitor / Notification Rules**

여러 알림을 조합한 composite monitor로 소음을 최소화하고, 태그 기반 알림 라우팅으로 모니터마다 수동 설정 없이 담당 팀에 전달한다.

> **우리에게 주는 시사점**: "코디네이터 heap 높음" AND "큐 뎁스 증가" 가 동시일 때만 호출 → 단독 요동은 무시. 태그(`cluster=`, `resource_group=`)로 라우팅.

**(D) 이상탐지 (Watchdog)**

Watchdog은 설정 없이 동작하며, 시스템의 **정상 동작 baseline을 선제적으로 계산**해 이를 기준으로 이상 행동을 탐지한다.

> **판정: 1차 범위 밖.** ML 이상탐지는 매력적이지만 우리는 아직 baseline 데이터조차 없다. **P3 이후.** 단, EventListener 데이터를 쌓아두면 나중에 가능해지므로 **데이터 스키마 설계 시 이를 염두**에 둔다.

### 3-2. 채택 판정

| Datadog 개념 | 판정 | TMS 반영 |
|---|---|---|
| SLO + Error Budget 대시보드 | **채택 (P1)** | FR-SLO |
| 알림→런북 매핑 강제 | **채택 (P0, 정책)** | 모든 Alertmanager 룰에 runbook_url 라벨 필수 |
| 증상 기반 알림 (원인 아님) | **채택 (원칙)** | 알림 설계 가이드에 명문화 |
| Downtime/Silence 스케줄 | **채택 (P1)** | 증설·점검 시 알림 억제 |
| 심각도별 라우팅 | **채택 (P1)** | P1=호출, P2=Slack |
| No Data 알림 | **채택 (P0)** | EventListener 수집 중단 감지 |
| Composite monitor | **채택 (P2)** | Alertmanager 룰 조합으로 구현 |
| Watchdog/ML 이상탐지 | **비채택 (P3+)** | baseline 데이터 부재. 스키마만 대비 |

---

## 4. 종합 — TMS 차별화 포인트

세 제품 어디에도 없고 **우리만 필요한** 기능이 있다. 이것이 TMS의 존재 이유다.

| 기능 | 왜 어디에도 없나 |
|---|---|
| **Gateway Routing Group 운영 콘솔** | SEP는 Gateway를 쓰지 않음. CM은 Trino를 모름. 우리는 Gateway가 확장의 축 |
| **클러스터 단위 수평 증설 워크플로** | 코디네이터 HA 부재 → 확장 단위가 워커가 아니라 **클러스터**. 이 개념 자체가 우리 고유 |
| **OPA 정책 상태 가시성** | 어느 제품도 OPA 사이드카 헬스/decision log를 Trino와 묶어 보여주지 않음 |
| **VM/systemd 골든이미지 drift 추적** | K8s 제품군은 이 문제가 없음. CM이 가장 가깝지만 Trino 미지원 |

---

## 5. 최종 기능 우선순위 (P0~P3)

| P | 기능 | 출처 |
|---|---|---|
| P0 | 통합 쿼리 히스토리/감사 조회 | SEP(Query overview+Audit log+Cluster history) |
| P0 | Cluster Health Test + 조치 조언 | Cloudera Manager |
| P0 | 액션 감사 로그 (변경 사유 필수) | Cloudera Manager |
| P0 | 알림→런북 매핑 정책 | Datadog |
| P1 | 워크로드(리소스 그룹) 관리 뷰 | SEP(Workload management) |
| P1 | Fleet 운영 콘솔 (클러스터/워커/graceful shutdown) | **TMS 고유** |
| P1 | Gateway Routing Group 관리 | **TMS 고유** |
| P1 | SLO / Error Budget 대시보드 | Datadog |
| P1 | Config drift 추적 | Cloudera Manager |
| P2 | 런타임 로그 레벨 변경 | SEP(Cluster logging) |
| P2 | OPA 정책 상태 가시성 | **TMS 고유** |
| P2 | JWT 토큰 발급 페이지 | SEP(`/ui/token`) |
| P3 | 다크모드, 이상탐지, 비용 추정 | 다수 |

---

## 6. 검증 필요 항목 (AI가 단정하지 않은 것)

다음은 **구현 착수 전 공식 문서로 반드시 확인**해야 한다. 버전 간 변동이 잦아 AI 기억에 의존하면 안 된다.

- [ ] Trino 477의 EventListener SPI 인터페이스 시그니처
- [ ] Trino 477의 graceful shutdown REST 엔드포인트 경로 및 인증 방식
- [ ] Trino 477의 런타임 로그 레벨 변경 API 지원 여부 (SEP 전용 기능일 가능성 있음 — **미지원이면 P2 기능 폐기**)
- [ ] Trino 477의 리소스 그룹 상태 조회 방법 (JMX MBean 또는 시스템 테이블)
- [ ] Trino Gateway 현재 버전의 REST API 스펙 (backend/routing rule 관리)
- [ ] OPA decision log 포맷 및 수집 방식
