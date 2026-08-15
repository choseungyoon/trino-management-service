# TMS Requirements Specification

**프로젝트**: Trino Management Service (TMS)
**버전**: 0.1 (Draft — 인간 검증 대기)
**방법론**: AI-DLC Inception 산출물
**작성 주체**: AI 초안 / **승인권자: Platform Owner (인간)**

---

## 0. 문서 사용 규칙 (AI 에이전트 필독)

0. **Bolt 0 (2026-08-04) 검증 결과가 반영되어 있다.** 취소선(~~`[VERIFY]`~~)이 그어진 항목은 해소된 것이며, 그 아래 "✅ Bolt 0 해소" 블록이 확정된 사실이다. **모든 사실의 출처는 `docs/TRINO_VERIFIED.md`이며, 판정 근거는 `docs/archive/BOLT_0_RESULT.md`다.**
1. **본 문서는 초안이다.** `[NEEDS-HUMAN-DECISION]` 태그가 붙은 항목은 인간 승인 전 구현 금지.
2. **`[VERIFY]` 태그가 붙은 기술 가정은 공식 문서로 검증 후 구현한다.** Trino 477 config property는 버전 간 변동이 잦다. 과거 세션에서 존재하지 않는 property를 제안해 기동 실패를 유발한 이력이 있다.
3. 모든 요구사항은 **검증 가능한 수용 기준(Acceptance Criteria)** 을 가진다. AC가 없는 요구사항은 미완성이다.
4. 코드 주석은 **영어**로 작성한다.

---

## 1. 배경 및 문제 정의

### 1-1. 현재 상태

| 항목       | 현황                                             |
| ---------- | ------------------------------------------------ |
| Trino 버전 | 477                                              |
| 클러스터   | 2개 (각 코디네이터 1 + 워커 12)                  |
| Gateway    | 2대, 공유 PostgreSQL                             |
| LB         | IP HASH (→ 세션 어피니티로 교체 예정)            |
| 인프라     | VM + systemd (K8s 미사용, **확정**)              |
| 증설 방식  | 수동/스크립트 (**확정**)                         |
| 접근제어   | OPA policy-as-code, 플랫폼팀 Git 관리 (**확정**) |
| 스토리지   | Ceph S3 (Spooling 사용)                          |
| 목표 규모  | 5만 사용자                                       |

### 1-2. 해결하려는 문제

| #   | 문제                                                            | 영향                                               |
| --- | --------------------------------------------------------------- | -------------------------------------------------- |
| P1  | Trino 쿼리 히스토리가 코디네이터 heap에만 존재 → 재시작 시 소실 | "누가 클러스터를 죽였나"에 답할 수 없음. 감사 불가 |
| P2  | 리소스 그룹 관점의 상태 뷰 부재                                 | 동시성 격리 설계가 실제 동작하는지 검증 불가       |
| P3  | 클러스터/워커 fleet 현황이 흩어져 있음                          | 증설·축소 판단을 감으로 함                         |
| P4  | 운영 액션(shutdown, 증설)의 이력이 남지 않음                    | 장애 원인 추적 불가, 감사 요건 미충족              |
| P5  | Gateway routing group 상태를 한눈에 볼 수 없음                  | 확장 단위 = 클러스터인데 그 관리 도구가 없음       |

### 1-3. 명시적 비목표 (Non-Goals)

**아래는 만들지 않는다. 에이전트가 "있으면 좋을 것 같아서" 추가하는 것을 금지한다.**

| 비목표                          | 이유                                                     |
| ------------------------------- | -------------------------------------------------------- |
| 웹 SQL 에디터                   | Superset SQL Lab 보유                                    |
| 메트릭 차트/대시보드 자체 구현  | Grafana 위임                                             |
| 알림 엔진 자체 구현             | Alertmanager 위임                                        |
| 권한 관리 UI (RBAC 편집 화면)   | OPA policy-as-code 결정과 충돌. Git PR이 관리 인터페이스 |
| 데이터 카탈로그/데이터 프로덕트 | 범위 밖                                                  |
| LLM/AI 어시스턴트               | 범위 밖                                                  |
| 쿼리 실행 프록시                | **절대 금지.** NFR-ISOLATION 위반                        |

---

## 2. 아키텍처 원칙 (위반 시 설계 반려)

| ID                       | 원칙                                                                                                       | 근거                                                                                                         |
| ------------------------ | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **NFR-ISOLATION**        | TMS는 **쿼리 실행 경로에 절대 개입하지 않는다.** TMS가 완전히 다운되어도 모든 쿼리는 정상 실행되어야 한다. | Cloudera Manager 원칙: CM Server/Agent를 중지해도 서비스는 내려가지 않고 실행 중인 역할 인스턴스는 계속 동작 |
| **NFR-READONLY-DEFAULT** | 기본은 읽기 전용. 쓰기 액션은 별도 권한 + 감사 + 확인 절차                                                 | 프로덕션 파괴 방지                                                                                           |
| **NFR-NO-SECRET**        | 자격증명은 코드/설정 파일에 평문 저장 금지. `config.secret.yaml`(gitignore) 분리                           | 기존 FastAPI 인프라 패턴 계승                                                                                |
| **NFR-DEGRADE**          | 의존 컴포넌트(Gateway, 코디네이터, OPA)가 죽어도 TMS는 해당 영역만 "unknown"으로 표시하고 나머지는 동작    | 부분 장애가 전체 블라인드로 번지지 않게                                                                      |

---

## 3. 기능 요구사항

### FR-QUERY-HISTORY (P0) — 통합 쿼리 히스토리/감사 — **⛔ R1 범위 제외 (2026-08-06)**

> **범위 결정 (인간)**: **이미 별도 프로젝트로 구현되어 운영 중이다.** TMS가 다시 만들지 않는다.
> **R1에서 제외한다.** 추후 별도 작업으로 **두 프로젝트를 통합**한다.
>
> | 항목 | 처리 |
> | --- | --- |
> | 본 요구사항(FR-QH-01~07) | **R1 미구현.** 폐기가 아니라 **외부 시스템으로 이관** |
> | `src/event-listener/` (Java 플러그인) | **R1에서 작성하지 않는다.** 기존 시스템이 이미 수집 중일 가능성이 높다 |
> | `data-pipeline-dev` 에이전트 | **R1 배정 작업 없음** |
> | **B4 (히스토리 저장소 선정)** | **R1을 더 이상 막지 않는다.** 통합 작업 시점으로 이월 |
> | `docs/WORKLOAD_PROFILE.md` | 여전히 필요하다 — **FR-SLO 목표값(R2)** 과 사이징의 근거이기 때문. 다만 R1 착수를 막지는 않는다 |
>
> **통합 시 재검토할 것** (별도 Bolt)
> - 기존 시스템의 EventListener와 TMS 컴포넌트의 **코디네이터 부하 합산** — NFR-PERF-03(CPU 1% 미만)은 개별이 아니라 **합계** 기준이어야 한다
> - R2의 **FR-WL-05**(리소스그룹→쿼리 목록)와 **FR-BM-05**(프로덕션 쿼리 샘플 추출)는 이 시스템의 데이터에 의존한다 → 통합 전까지 두 항목은 설계만 하고 구현을 보류한다
> - 이벤트에 `QueryContext.resourceGroupId` 가 포함되어 있는지 확인 (`TRINO_VERIFIED.md` §T1-1). 없으면 FR-WL-05가 성립하지 않는다
>
> 아래 원문은 **통합 작업의 참조 명세로 보존**한다.

**문제**: Trino 히스토리는 in-memory. 재시작/`query.max-history` 초과 시 소실.

**요구사항**

| ID       | 내용                                                                  | AC                                                |
| -------- | --------------------------------------------------------------------- | ------------------------------------------------- |
| FR-QH-01 | EventListener 플러그인이 `QueryCompletedEvent`를 외부 저장소로 전송   | 쿼리 완료 후 5초 내 저장소에서 조회 가능          |
| FR-QH-02 | 코디네이터 재시작 후에도 이전 쿼리 조회 가능                          | 재시작 전 실행 쿼리가 조회됨                      |
| FR-QH-03 | 사용자/기간/상태/클러스터/리소스그룹/소스별 검색                      | 각 조건 및 조합 검색 동작                         |
| FR-QH-04 | 실패 쿼리의 오류 코드·메시지·스택 조회                                | 실패 원인 식별 가능                               |
| FR-QH-05 | 쿼리별 리소스 소비(CPU time, peak memory, 스캔 바이트, 실행시간) 표시 | 상위 소비 쿼리 정렬 가능                          |
| FR-QH-06 | 수집 파이프라인 중단 감지 및 알림                                     | N분간 이벤트 0건 시 알림 (Datadog "No Data" 패턴) |
| FR-QH-07 | 보존 정책 설정 (기본 90일) 및 자동 정리                               | 만료 데이터 자동 삭제                             |

~~**[VERIFY]** Trino 477 EventListener SPI 인터페이스 시그니처, 이벤트 필드 목록~~
**✅ Bolt 0 해소** — `TRINO_VERIFIED.md` §T1-1. 요약:

- 구현할 메서드는 **`queryCompleted(QueryCompletedEvent)` 하나면 된다** (전부 `default`). `splitCompleted`는 removal 예정 → **구현 금지**.
- 설정: `etc/event-listener.properties` 의 `event-listener.name`, 복수 등록 시 `config.properties` 의 `event-listener.config-files`.
- `QueryCompletedEvent` 최상위: `metadata`, `statistics`, `context`, `ioMetadata`, `failureInfo`, `warnings`, `createTime`, `executionStartTime`, `endTime`.
- **FR-QH-05에 필요한 필드가 전부 존재한다** (`cpuTime`, `peakUserMemoryBytes`, `processedInputBytes`, `wallTime` 등).
- **`QueryContext.resourceGroupId` 가 이벤트에 포함되므로 FR-WL-05(리소스그룹↔쿼리 조인)가 이 데이터만으로 성립한다.**

**⚠️ 저장 필드 화이트리스트 필수**: `QueryStatistics.taskStatistics`, `operatorSummariesProvider`, `QueryMetadata.plan`, `jsonPlan` 은 쿼리당 수 MB에 달할 수 있다. **기본 저장 대상에서 제외하고, 저장할 필드를 명시적으로 열거한다.**

**[NEEDS-HUMAN-DECISION]** 저장소 선택 — PostgreSQL(기존 자산 재활용) vs Elasticsearch(검색 성능). **`WORKLOAD_PROFILE.md` 의 수집·사이징 절차 완료 후 결정한다. R1 착수를 막고 있는 항목(B4/D-1).**

**설계 주의**: EventListener는 코디네이터 프로세스 내에서 동작한다. **SPI에 비동기 실행·버퍼링·백프레셔 기능은 없다 (Bolt 0 확인). 전적으로 구현체 책임이다.** 반드시 비동기 + 바운디드 버퍼 + 백프레셔 설계. 저장소 다운 시 이벤트를 버릴지언정 코디네이터를 느리게 만들면 안 된다 (NFR-ISOLATION).

---

### FR-CLUSTER-HEALTH (P0) — 클러스터 헬스 및 조치 조언

**출처**: Cloudera Manager Health Test 모델

**요구사항**

| ID       | 내용                                                                           | AC                                       |
| -------- | ------------------------------------------------------------------------------ | ---------------------------------------- |
| FR-CH-01 | 클러스터/노드 단위 health test 실행 및 상태 표시 (Good/Concerning/Bad/Unknown) | 4개 상태 정확 표시                       |
| FR-CH-02 | 각 health test는 **비정상 시 취할 조치 조언(advice)을 포함**                   | 모든 Bad/Concerning에 조치 문구 존재     |
| FR-CH-03 | 개별 health test 활성/비활성 설정                                              | 비활성 test는 전체 health 계산에서 제외  |
| FR-CH-04 | roll-up health test를 개별 test와 별도로 비활성화 가능                         | 개별 지표 이상이어도 전체 정상 판정 가능 |
| FR-CH-05 | 임계값(Warning/Critical) 사용자 조정                                           | 조정 즉시 반영                           |
| ~~FR-CH-06~~ | ~~**반복 크래시 감지**~~ → **R3 FR-FLEET으로 이관 (2026-08-06 승인)**. systemd `Restart=` 이력이 필요해 노드 접근이 전제된다. R1에는 노드 에이전트가 없다 | — |
| FR-CH-07 | Bad/Concerning 상태는 이벤트로 기록 및 알림 연동                               | Alertmanager로 전달                      |

**최소 health test 목록**

- 코디네이터 응답성 / 워커 등록 수 vs 기대치 / JVM heap 사용률 / GC pause 시간
- 리소스 그룹 큐 뎁스 / 실패 쿼리 비율 / 디스크 여유공간 / systemd 유닛 상태
- Gateway → 클러스터 헬스체크 상태 / OPA 사이드카 응답성

---

### FR-AUDIT-ACTION (P0) — 운영 액션 감사

**출처**: Cloudera Manager 설정 변경 감사 + "Reason for change" 필수 입력

**요구사항**

| ID       | 내용                                                                          | AC                            |
| -------- | ----------------------------------------------------------------------------- | ----------------------------- |
| FR-AA-01 | 모든 쓰기 액션은 감사 로그 기록: 누가/언제/무엇을/왜/결과                     | 액션 후 즉시 조회 가능        |
| FR-AA-02 | **변경 사유(reason) 입력 필수.** 미입력 시 액션 거부                          | 빈 reason으로 액션 시도 → 400 |
| FR-AA-03 | 파괴적 액션(shutdown, 증설/축소, 로그레벨 변경)은 확인 다이얼로그 + 대상 명시 | 대상 미확인 시 실행 불가      |
| FR-AA-04 | 감사 로그는 **수정/삭제 불가(append-only)**                                   | UPDATE/DELETE 경로 부재       |
| FR-AA-05 | 감사 로그 검색/내보내기                                                       | CSV 내보내기 동작             |

---

### FR-WORKLOAD (P1) — 워크로드(리소스 그룹) 관리 뷰

**출처**: SEP Workload management — 리소스 그룹 관리·모니터링, 활동 확인, 관련 쿼리 식별, 과도한 제한으로 인한 병목 규명

| ID       | 내용                                                                     | AC             |
| -------- | ------------------------------------------------------------------------ | -------------- |
| FR-WL-01 | 리소스 그룹 계층 구조 시각화                                             | 트리 표시      |
| FR-WL-02 | 그룹별 running/queued 쿼리 수 실시간 표시                                | 5초 이내 갱신  |
| FR-WL-03 | ~~그룹별 큐 대기시간 p50/p95~~ → **AC 축소 (DESIGN_R2 §1-4, 2026-08-09 구현)**: 그룹별 **현재 큐 길이 + 최장 대기 쿼리의 나이**. 리소스 그룹 MBean 은 큐 대기시간 분포를 노출하지 않는다. 정확한 백분위수는 히스토리 통합 이후 | 값 표시 |
| FR-WL-04 | **병목 진단** — 제한(softConcurrencyLimit 등)에 걸려 대기 중인 그룹 강조 | 대기 원인 표시 |
| FR-WL-05 | 그룹 클릭 → 해당 그룹의 **현재** 쿼리 목록. **2026-08-09 구현.** 과거 쿼리는 D-001 이월. 매칭은 점 경로 전체 기준(하위 그룹 포함), 세그먼트 아님 | 조인 조회 동작 |
| FR-WL-06 | 그룹별 리소스 소비 랭킹. **2026-08-09 구현.** ⛔ 랭킹은 트리를 정렬한 것이 **아니라 별도 뷰**다 — 정렬된 트리는 자식을 엉뚱한 부모 밑에 놓는다 | 정렬 가능      |
| FR-WL-07 | **리소스 그룹 설정 트리 조회** (DB 소스) + 실행 중 상태(JMX)와 대조. **2026-08-14 구현** — `DESIGN_WL07.md` | 설정된 전체 목록 표시 |
| FR-WL-08 | 그룹 **값 수정** — `reason` + 감사 + admin. 10초 내 반영, 재시작 없음. **2026-08-14 구현** | 저장 후 반영 확인 |
| FR-WL-09 | 그룹·셀렉터 **추가/삭제**. ⛔ 삭제는 `ON DELETE CASCADE` 파급을 실행 전에 나열. **2026-08-14 구현** | 파급 목록 표시 |
| FR-WL-10 | 변경 **이력 + 되돌리기**. Trino 테이블에 이력이 없어 TMS 가 별도 보관. **2026-08-14 구현** | 되돌리기 동작 |

~~**[VERIFY]** Trino 477의 리소스 그룹 상태 조회 방법~~
**✅ Bolt 0 해소** — `TRINO_VERIFIED.md` §T1-4.

- ~~**1차 소스**: `GET /v1/resourceGroupState/{resourceGroupId}`~~ — **⛔ 2026-08-08 실측으로 뒤집힘.** 점 포함 ID 는 404 이며(루트 그룹 이름만 받는다), 응답도 root + 1단계까지만 내려준다. **이 경로로는 FR-WL-01(계층 시각화)이 성립하지 않는다.** 상세는 `TRINO_VERIFIED.md` §T1-4 정정 블록.
- **✅ 1차 소스는 JMX 다 (2026-08-08 확정, 미해소 G-5 해소)**. `jmxExport: true` 를 준 그룹마다 독립 MBean 이 등록되며 `name=` 에 전체 점 경로가 들어간다: `trino.execution.resourcegroups:type=InternalResourceGroup,name=global.adhoc.dashboard`. `GET /v1/jmx/mbean` 열거로 전체 트리를 복원한다.
- **⚠️ 선행 조건**: `resource-groups.json` 의 모든 그룹에 `jmxExport: true` 가 필요하다 (신규 SETUP S9). 없으면 데이터가 0이다.
- **⚠️ 지연 생성**: 쿼리가 배정된 적 없는 그룹은 MBean 도 REST 응답에도 나타나지 않는다. **"설정되었으나 유휴인 그룹"은 알 수 없다.**
- 설계 판단은 `DESIGN_R2.md` §1 참조.
- FR-WL-05(그룹↔쿼리 조인)는 `QueryCompletedEvent.context.resourceGroupId` 로 성립한다.

> **⛔ 2026-08-13 D-010 으로 전제가 바뀌었다.** 위 항목들은 리소스 그룹을 **읽는** 기능이고 데이터 소스는 JMX 다. 그 아래 FR-WL-07~10 은 **쓰는** 기능이며 데이터 소스가 다르다 — Trino 의 `db` 리소스 그룹 매니저 **테이블**이다. `DESIGN_R2.md` §1-6 이 "쓰기는 하지 않는다"고 미뤘던 이유(JMX 로 쓰면 재시작 시 되돌아간다)는 파일 매니저를 전제한 것이라 더는 성립하지 않는다.

**⚠️ 데이터 소스 변경 (중요)**: **Trino Gateway 19에서 리소스 그룹 관리 기능이 전면 제거되었다** ([#656](https://github.com/trinodb/trino-gateway/issues/656)). 릴리스 노트 원문: *"Resource groups are a Trino feature and must be managed through Trino directly."* → **FR-WORKLOAD은 Gateway가 아니라 Trino를 데이터 소스로 삼는다.**

---

### FR-FLEET (P1) — Fleet 운영 콘솔 (**TMS 고유**)

**배경**: 확장 단위는 워커가 아니라 **클러스터**다. 코디네이터 HA가 없으므로 단일 코디네이터가 확장 천장이다.

| ID       | 내용                                                        | AC                             |
| -------- | ----------------------------------------------------------- | ------------------------------ |
| FR-FL-01 | 전체 클러스터/노드 인벤토리 — **아래 필드 정의 참조**       | 전 노드 표시, 필드 누락 없음   |
| FR-FL-02 | 워커 등록 상태 및 discovery 조인 여부                       | 미조인 워커 식별               |
| FR-FL-03 | **Graceful shutdown 트리거** — in-flight task drain 후 종료 | drain 완료 확인, 쿼리 실패 0건 |
| FR-FL-04 | 증설 스크립트 실행 훅 (Ansible playbook 호출)               | 실행 결과/로그 표시            |
| FR-FL-05 | 실행 이력 및 진행 상태 추적                                 | 진행률 표시                    |
| FR-FL-06 | 모든 액션은 FR-AUDIT-ACTION 준수                            | reason 없이 실행 불가          |

**FR-FL-01 필드 정의 (인벤토리는 판단이 아니라 사실 목록. 헬스 판정은 FR-CLUSTER-HEALTH 소관)**

_정적 정보 (Ansible inventory 소스)_: hostname/IP, role(coordinator/worker/gateway), 소속 클러스터, 골든 이미지 버전, VM 스펙, 프로비저닝 일시

_런타임 정보 (실시간 조회 소스)_: Trino 버전, systemd 유닛 상태, 프로세스 uptime, **discovery 조인 여부**, config 체크섬(FR-FLEET-DRIFT 연동), last seen

~~**조인 여부 조회 설계**: `system.runtime.nodes` 를 1차 소스로, `/v1/node` 를 보조 소스로 사용한다.~~
**⛔ 2026-08-09 실측으로 두 전제가 모두 뒤집혔다 (`TRINO_VERIFIED.md` §T1-2-1).**

- `/v1/node` 는 **477 에 없다 (404).** 보조 소스가 아니라 소스가 아니다.
- `system.runtime.nodes` 는 **`ExecuteQuery` 권한을 요구**하며 TMS 계정은 갖고 있지 않다.

**실제 구현 (2026-08-09)**: 정적 정보는 **Ansible 인벤토리**, 런타임 정보는 **각 노드의 `GET /v1/info`**(`PUBLIC`, 자격증명 불필요). 개별 노드 상태는 같은 응답의 `state` 다.

**미충족 AC**: FR-FL-02(어느 워커가 미조인인지)는 `ExecuteQuery` 없이 불가능하다. 코디네이터 MBean 은 개수만 주고 식별자를 주지 않는다. TMS 는 개수 불일치를 표시하고 **화면에 이 한계를 명시**한다.

**금지**: 본 화면에 CPU/Network/Disk 사용률 그래프를 넣지 않는다. Grafana 딥링크로 대체한다 (비목표 원칙 유지).

~~**`[VERIFY]`**: `system.runtime.nodes`와 `/v1/node`의 차이, `/v1/info/state`의 인증 요구사항~~
~~**[VERIFY]** Trino 477 graceful shutdown 엔드포인트 경로·HTTP 메서드·페이로드·인증 방식~~
**✅ Bolt 0 해소** — `TRINO_VERIFIED.md` §T1-2, §T1-5, §T3-4.

**Graceful shutdown (확정)**

| 항목 | 값 |
| --- | --- |
| 요청 | `PUT /v1/info/state`, 본문 `"SHUTTING_DOWN"` (JSON 문자열 — 큰따옴표 포함) |
| 헤더 | `Content-Type: application/json`, `X-Trino-User: <권한 있는 사용자>` |
| 보안 | `@ResourceSecurity(MANAGEMENT_WRITE)` — "system information" **쓰기** 권한 필요 |
| 유예시간 | `shutdown.grace-period` (기본 `2m`) |

**⚠️ FR-FL-03 타임아웃 기본값 정정**: 종료 시퀀스는 `SHUTTING_DOWN` 진입 → **grace-period 대기** → 활성 task 완료까지 블록 → **grace-period 재대기** → 종료다. 즉 **최소 `2 × shutdown.grace-period` + 실행 중 task 완료 시간**이 걸린다 (기본 설정 시 4분+). 타임아웃을 이보다 짧게 잡으면 안 된다.

**`/v1/info` 계열 (확정)**: `GET /v1/info`, `GET /v1/info/state`, `GET /v1/info/coordinator` 는 모두 `PUBLIC`. 쓰기(`PUT /v1/info/state`)만 `MANAGEMENT_WRITE`.

**`system.runtime.nodes` 컬럼 (확정)**: `node_id`, `http_uri`, `node_version`, `coordinator`, `state` — **5개뿐이다.** FR-FL-01의 나머지 인벤토리 필드는 Ansible inventory + `/v1/info` 조합으로 채운다.

**OPA 인가 (확정)**: Trino 477 OPA 플러그인은 `checkCanWriteSystemInformation` → `action.operation = "WriteSystemInformation"` 을 OPA로 보낸다. 따라서 OPA로 graceful shutdown 인가가 **가능하다**.
- **모든 워커에 `etc/access-control.properties`(OPA 설정) 배포 필요** — graceful shutdown 문서 원문: *"These configuration must be present on all workers."*
- Rego 정책에 TMS 서비스 계정의 `WriteSystemInformation` 허용 규칙 추가 (플랫폼팀 Git).
- 조회 API(`/v1/jmx/mbean`, `/v1/query`, `/v1/resourceGroupState/...`)는 `MANAGEMENT_READ` → **`ReadSystemInformation` 허용도 필요**.
- **⚠️ 신규 실패 모드: 워커의 OPA가 죽으면 graceful shutdown이 거부된다.** 워커 OPA 헬스를 FR-OPA-01 감시 대상에 포함할 것.
- **⚠️ 미해소 G-4**: 공식 문서는 `allow-all` / `file` 만 언급하고 OPA를 언급하지 않는다. 위 결론은 소스 근거이므로 **R3 착수 전 실환경 검증 필요.**

---

### FR-FLEET-DRIFT (P1) — Config Drift 추적

**출처**: Cloudera Manager stale config 개념

| ID       | 내용                                          | AC               |
| -------- | --------------------------------------------- | ---------------- |
| FR-FD-01 | 노드별 config 체크섬 수집 및 기준값 대비 비교 | 불일치 노드 표시 |
| FR-FD-02 | 설정 변경 후 미재시작(stale) 노드 표시        | stale 플래그     |
| FR-FD-03 | 골든 이미지 버전 및 Trino 버전 불일치 탐지    | 버전 혼재 경고   |

---

### FR-GATEWAY (P1) — Gateway Routing Group 콘솔 (**TMS 고유**)

| ID       | 내용                                              | AC                        |
| -------- | ------------------------------------------------- | ------------------------- |
| FR-GW-01 | Gateway 인스턴스 상태 및 백엔드 클러스터 목록     | 전체 표시                 |
| FR-GW-02 | Routing group별 소속 클러스터 및 헬스             | 그룹 트리 표시            |
| FR-GW-03 | 클러스터 활성/비활성 토글 (Blue/Green 배포 지원)  | 토글 후 라우팅 반영       |
| FR-GW-04 | Gateway 백엔드 DB 상태 및 databaseCache 동작 여부 | ⛔ **미충족 확정 (2026-08-15)** — 아래 참조 |
| FR-GW-05 | 라우팅 규칙 조회 (읽기 전용)                      | 현재 규칙 표시            |

~~**[VERIFY]** 현재 Trino Gateway 버전의 REST API 스펙~~
**✅ Bolt 0 해소 (부분)** — `TRINO_VERIFIED.md` §T2-3, §T2-4. **단 운영 Gateway 버전 미확정 (B6/G-1)** 이므로 아래는 `main` 브랜치 기준이다.

| 용도 | 메서드 | 경로 |
| --- | --- | --- |
| 전체 조회 / 활성 조회 | GET | `/gateway/backend/all` · `/gateway/backend/active` |
| 추가 / 수정 / 삭제 | POST | `/gateway/backend/modify/add` · `/update` · `/delete` |
| **비활성화 / 활성화** | POST | `/gateway/backend/deactivate/{name}` · `/gateway/backend/activate/{name}` |
| 라우팅 규칙 갱신 | POST | `/webapp/updateRoutingRules` (ADMIN, `rulesType: FILE` + 쓰기 가능 파일) |
| liveness / readiness | GET | `/trino-gateway/livez` · `/trino-gateway/readyz` |
| Prometheus 지표 | GET | `/metrics` |

백엔드 페이로드 필드: `name`, `proxyTo`, `active`, `routingGroup`, `externalUrl`.

> **FR-CO-02 안전 시퀀스의 1단계(비활성화)·5단계(재활성화)와 FR-BM-04(벤치마크 프로덕션 보호)는 `deactivate/{name}` / `activate/{name}` 로 구현한다. 경로가 확정되었다.**

> **⛔ FR-GW-04 미충족 (2026-08-15, 구현 시도 중 확정)**
>
> 축소된 AC("백엔드 목록의 캐시 폴백 표시")조차 **TMS 가 알 수 없다.** Gateway 는 응답이 캐시에서 왔는지 DB 에서 왔는지 구분할 신호를 노출하지 않고, "목록이 안 변한다"는 것은 **캐시 폴백과 그냥 클러스터가 안 바뀌는 것을 구분하지 못한다.** 추측으로 표시하면 DB 가 멀쩡할 때 장애라고 말하거나 그 반대가 된다.
>
> **대신 화면은 결과를 말한다** — 캐시되는 것은 백엔드 목록뿐이고(쿼리 히스토리·queryId 조회는 즉시 멈춘다), `expireAfterWrite` 가 지나면 **stale 폴백 없이 라우팅이 실패한다**. 현 배포는 10분이므로 Gateway DB 장애는 10분 뒤 라우팅 장애가 된다. 운영자가 행동해야 하는 것은 이쪽이다.
>
> **풀리는 조건**: Gateway 가 캐시 적중/DB 도달 여부를 노출하는 엔드포인트나 메트릭을 제공하면. 그때까지는 만들지 않는다 — 없는 신호를 지어내는 것보다 모른다고 쓰는 편이 낫다.

**⚠️ FR-GW-04 AC 축소 근거(당시)**: `databaseCache`(기본 `enabled: false`)가 캐시하는 것은 문서 원문 기준 *"only the list of backend Trino clusters used for query routing"* 뿐이다. 쿼리 히스토리 기록과 queryId→backend 조회는 캐시되지 않는다. 또한 `expireAfterWrite`(기본 `1h`) 만료 후에도 DB가 죽어 있으면 **stale 폴백 없이 요청이 실패한다**. 따라서 "DB 다운 시 전 기능 정상"은 성립하지 않으며, AC는 **"신규 쿼리 라우팅이 계속되는지"** 로 한정한다.

---

### FR-SLO (P1) — SLO / Error Budget

**출처**: Datadog SLO 모델

| ID        | 내용                                               | AC                |
| --------- | -------------------------------------------------- | ----------------- |
| FR-SLO-01 | SLO 정의 (가용성, p95 쿼리 지연, 최대 큐 대기시간) | 3개 이상 SLO 등록 |
| FR-SLO-02 | 롤링 윈도우 기준 SLO 달성률 표시                   | 7일/30일 표시     |
| FR-SLO-03 | **Error budget 잔량 및 소진율** 표시               | 백분율 표시       |
| FR-SLO-04 | Error budget 소진 임계 도달 시 알림                | 임계 초과 시 발송 |

**[NEEDS-HUMAN-DECISION]** SLO 목표값. Phase 0 워크로드 특성화 결과 없이는 정할 수 없음.

---

### FR-OPA (P2) — OPA 정책 상태 가시성 (**TMS 고유**)

**배경**: OPA는 쿼리 경로에 동기 개입하며 **fail-closed**다. 모든 쿼리가 PDP 호출을 유발하고, Trino가 PDP에 도달 못 하거나 PDP가 에러나면 쿼리를 거부한다. → **OPA 다운 = 전체 차단.**

| ID        | 내용                                                     | AC                    |
| --------- | -------------------------------------------------------- | --------------------- |
| FR-OPA-01 | 코디네이터별 OPA 사이드카 헬스 상태                      | 각 사이드카 상태 표시 |
| FR-OPA-02 | 현재 로드된 정책 번들 버전/Git 커밋 해시                 | 버전 표시             |
| FR-OPA-03 | 코디네이터 간 번들 버전 불일치 경고                      | 불일치 시 경고        |
| FR-OPA-04 | Decision log 기반 인가 거부 조회 (누가/무엇을/언제 거부) | 검색 동작             |
| FR-OPA-05 | 인가 판정 지연 p95 표시                                  | 값 표시               |

~~**[VERIFY]** OPA decision log 포맷 및 수집 방식~~
**✅ Bolt 0 해소** — `TRINO_VERIFIED.md` §T3-3.

- **FR-OPA-04는 OPA 서버의 decision log로 구현한다.** `decision_logs.console: true` → stdout → Promtail/Filebeat → Loki/OpenSearch (SETUP S7). TMS는 **딥링크만** 제공한다 (비목표 준수).
- 엔트리 필드: `decision_id`, `path`, `input`, `result`, `timestamp`(RFC3339), `labels`, `metrics`, `bundles`, `requested_by`.
- 관련 설정: `decision_logs.service`, `decision_logs.reporting.min_delay_seconds` / `max_delay_seconds` / `max_decisions_per_second`, `decision_logs.mask_decision`(기본 `data.system.log.mask`), `decision_logs.drop_decision`(기본 `/system/log/drop`).
- **⚠️ `input` 필드에 SQL 전문과 사용자 식별정보가 그대로 들어간다. `mask_decision` 정책을 반드시 함께 설계할 것.**
- **⚠️ Trino 측 `opa.log-requests` / `opa.log-responses` 는 켜지 말 것.** 전 쿼리의 요청·응답 본문 전체가 `io.trino.plugin.opa.OpaHttpClient` 로거에 DEBUG로 쏟아진다. 공식 문서 경고: *"enabling these options produces very large amounts of log data."*

**Trino → OPA 요청 구조 (확정)**: 최상위 `context` + `action`. `context.identity.{user,groups}`, `context.softwareStack.trinoVersion`, `action.{operation,resource,targetResource,grantee}`. **적용되지 않는 필드는 null이 아니라 아예 생략된다** — Rego 작성 시 주의.

**FR-OPA-05(인가 지연) 관련 — 성능 설정 확인 사실**: `opa.policy.batched-uri` 미설정 시 Trino는 **리소스마다 개별 요청**을 보낸다. 컬럼 수가 많은 테이블에서 인가 지연이 선형 증가하므로 5만 사용자 규모에서 배치 설정은 사실상 필수다. 컬럼 마스킹 배치는 별도 property `opa.policy.batch-column-masking-uri` 이며 **`opa.policy.column-masking-uri` 와 동시 사용 금지**(동시 설정 시 배치 쪽이 덮어씀).

---

### FR-LOGLEVEL (P2) — 런타임 로그 레벨 변경 — **Bolt 0 개정: 축소 존치**

~~**[VERIFY]** Trino 477 OSS에 런타임 로그 레벨 변경 API가 존재하는가? 미지원 확인 시 본 요구사항 전체 폐기.~~
**✅ Bolt 0 해소 — 사전 가정이 틀렸다. OSS Trino 477에 존재한다.** 근거: `TRINO_VERIFIED.md` §T1-3.

| 질문 | 답 |
| --- | --- |
| 재시작 없이 변경 가능? | **가능.** Trino 477 `Server.java` 가 `LogJmxModule` 을 무조건 등록 → MBean **`io.airlift.log:name=Logging`** 의 `setLevel(loggerName, newLevel)` / `setRootLevel(newLevel)` / `getAllLevels()` |
| REST API로 가능? | **불가.** `/v1/jmx`(airlift `MBeanResource`)는 `@GET` 4개뿐. 쓰기 메서드 없음 |
| 공식 문서에 있나? | **없음.** `/docs/477/admin/logging.html`에 언급 전무 → **문서화되지 않은 내부 API. 버전업으로 사라질 수 있다** |
| 재시작 후 유지? | **안 됨.** JVM 인메모리 상태만 변경 |
| 전 노드 일괄? | **안 됨.** 노드별 개별 호출 (클러스터당 13회) |

**개정된 요구사항**

| ID       | 내용                                     | AC                |
| -------- | ---------------------------------------- | ----------------- |
| FR-LL-01 | 로거별 레벨 변경 (ERROR/WARN/INFO/DEBUG) | 재시작 없이 반영. **전 노드 순차 적용이며 부분 실패가 가능하다 — 노드별 성공/실패를 개별 표시할 것** |
| FR-LL-02 | 변경한 로거만 목록에 표시 + 기본값 리셋  | `getAllLevels()`로 조회. "리셋"은 TMS가 변경 전 값을 기억해 되돌리는 방식 |
| FR-LL-03 | 관리자 권한 필수 + 감사 기록             | 비관리자 403      |
| FR-LL-04 | DEBUG 레벨은 **자동 만료(기본 1시간)**   | 만료 후 자동 복원 |
| ~~FR-LL-05~~ | ~~재시작 후에도 유지~~ | **삭제.** OSS에서 재현 불가. 영속화가 필요하면 Ansible로 `log.properties`를 갱신하는 별개 작업이며, 그것은 "재시작 없는 변경"이 아니다 |

**FR-LL-04 근거 (중요도 상승)**: MBean 변경은 재시작 없이는 원복되지 않는다. DEBUG를 켠 채 잊으면 디스크가 가득 차고 성능이 죽는다. **자동 만료가 유일한 안전판이다.**

**[NEEDS-HUMAN-DECISION] D-2 — 구현 방식**: TMS 백엔드는 Python(FastAPI)이며 **Python은 JMX/RMI를 네이티브로 말하지 못한다.**

| 선택지 | 비용 |
| --- | --- |
| (A) 전 노드에 **Jolokia** JVM 에이전트 → HTTP로 MBean 쓰기 | 신규 에이전트 의존성 + 공격면 증가 (JMX 쓰기가 HTTP로 열림) |
| (B) JVM 헬퍼 프로세스를 TMS가 호출 | 신규 컴포넌트 + 배포/운영 부담 |
| (C) **기능 드롭.** 로그 레벨 변경은 Ansible + 재시작(FR-CLUSTER-OPS 안전 시퀀스)으로만 제공 | "재시작 없는 변경" 포기 |

> **권고: (C) 드롭 검토.** 문서화되지 않은 내부 API + 신규 인프라 의존성 + 노드별 개별 호출 + 재시작 시 소실 — R4 항목치고 비용 대비 가치가 낮다. **인간 결정 전까지 구현에 착수하지 않는다.**
>
> **주 유즈케이스 참고**: OPA 인가 디버깅(`io.trino.plugin.opa.OpaHttpClient`를 한 시간만 DEBUG로)이 이 기능의 대표 사례다. FR-OPA-04를 OPA decision log로 구현하면 이 필요 자체가 상당 부분 사라진다.

---

### FR-PORTAL (P0) — 통합 포털

| ID       | 내용                                           | AC                  |
| -------- | ---------------------------------------------- | ------------------- |
| FR-PT-01 | ~~SSO 인증 (LDAP/AD 연동)~~ → **R1은 로컬 계정 + 임시 비밀번호 (D-007)**. AD 연동은 이후로 이월 | 로그인 동작 |
| FR-PT-02 | Grafana / Gateway UI / Trino UI / Superset / **기존 쿼리 히스토리 시스템** 링크 허브 | 각 링크 이동        |
| FR-PT-03 | 세션 타임아웃 + 비활성 타임아웃                | 만료 시 재인증      |
| FR-PT-04 | 역할 기반 화면 노출 (조회자/운영자/관리자)     | 권한 밖 화면 미노출 |

---

## 4. 비기능 요구사항

| ID          | 항목                               | 목표                           |
| ----------- | ---------------------------------- | ------------------------------ |
| NFR-PERF-01 | 히스토리 검색 응답                 | p95 < 2초 (90일 데이터)        |
| NFR-PERF-02 | 실시간 화면 갱신 주기              | 5초                            |
| NFR-PERF-03 | TMS가 Trino 코디네이터에 주는 부하 | CPU 1% 미만                    |
| NFR-SEC-01  | 전 구간 TLS                        | 필수                           |
| NFR-SEC-02  | 자격증명 분리 저장                 | `config.secret.yaml` gitignore |
| NFR-SEC-03  | 파괴적 액션은 관리자 역할 한정     | 필수                           |
| NFR-OPS-01  | TMS 자체 헬스체크 엔드포인트       | `/health`                      |
| NFR-OPS-02  | TMS 배포 방식                      | systemd 유닛 (기존 패턴 계승)  |

---

## 5. 릴리스 계획

> **주의**: 아래 §5는 v0.1 원안이다. **실제 릴리스 계획은 부록 B(v0.2 갱신)를 따른다.**

| 릴리스 | 포함                                         | 목표                              |
| ------ | -------------------------------------------- | --------------------------------- |
| R1     | FR-PORTAL, ~~FR-QUERY-HISTORY~~, FR-AUDIT-ACTION | "누가 무엇을 했나"에 답할 수 있다 |
| R2     | FR-CLUSTER-HEALTH, FR-WORKLOAD               | "지금 건강한가"에 답할 수 있다    |
| R3     | FR-FLEET, FR-FLEET-DRIFT, FR-GATEWAY         | "안전하게 늘리고 줄일 수 있다"    |
| R4     | FR-SLO, FR-OPA, FR-LOGLEVEL                  | "약속을 지키고 있는가"            |

---

## 6. 리스크

| 리스크                                    | 영향       | 완화                                              |
| ----------------------------------------- | ---------- | ------------------------------------------------- |
| EventListener가 코디네이터 성능 저하 유발 | 높음       | 비동기+버퍼+백프레셔, 부하 테스트 필수            |
| 5만 사용자 이벤트량이 저장소 용량 초과    | 높음       | Phase 0 특성화로 이벤트량 추정 후 저장소 선정     |
| FR-LOGLEVEL이 OSS 미지원                  | 중간       | 검증 후 폐기 결정                                 |
| TMS가 파괴적 액션으로 프로덕션 장애 유발  | **치명적** | 읽기 전용 우선, 승인 절차, 감사, 단계적 권한 부여 |
| 에이전트가 비목표 기능을 임의 추가        | 중간       | 비목표 목록 명문화, 리뷰 게이트                   |

---

# 부록 A — v0.2 추가 요구사항 (사용자 제시 항목 반영)

> 본 부록은 사용자가 제시한 8개 항목을 검증·판정한 결과 추가된 요구사항이다.
> 판정 근거는 `BACKLOG.md` 참조. **SETUP/DELEGATE/REJECT 판정 항목은 요구사항으로 등재하지 않는다.**

## FR-QUERY-LIVE (P0) — 실행 중 쿼리 실시간 모니터링

**배경**: EventListener의 `QueryCompletedEvent`는 **완료된** 쿼리만 제공한다. "지금 실행 중"은 코디네이터를 실시간 조회해야 하므로 데이터 소스가 다르다.

| ID       | 내용                                                               | AC              |
| -------- | ------------------------------------------------------------------ | --------------- |
| FR-QL-01 | 클러스터별 실행 중 쿼리 목록 (사용자, 경과시간, 상태, 리소스 그룹) | 5초 이내 갱신   |
| FR-QL-02 | 진행 중 쿼리 수 / 대기 쿼리 수 집계                                | 클러스터별 표시 |
| FR-QL-03 | 장시간 실행 쿼리 강조 (임계값 설정)                                | 임계 초과 강조  |
| FR-QL-04 | 쿼리 kill 기능 (관리자 한정, 감사 기록)                            | reason 필수     |

**✅ Bolt 0 — 데이터 소스 및 kill API 확정** (`TRINO_VERIFIED.md` §T1-5)

| 메서드 | 경로 | 용도 | 접근제어 |
| --- | --- | --- | --- |
| GET | `/v1/query?state=<...>` | 실행 중 쿼리 목록 (`List<BasicQueryInfo>`) | 응답을 `filterQueries()`로 필터 |
| GET | `/v1/query/{queryId}` | 상세 | `checkCanViewQueryOwnedBy` |
| **PUT** | **`/v1/query/{queryId}/killed`** | **kill (본문 = 실패 메시지)** | `checkCanKillQueryOwnedBy` |
| DELETE | `/v1/query/{queryId}` | 취소 (메시지 슬롯 없음) | `checkCanKillQueryOwnedBy` |

> **FR-QL-04는 `PUT /v1/query/{queryId}/killed` 로 구현한다.** 본문에 메시지를 받으므로 **입력받은 `reason`을 그대로 전달하면 Trino가 사용자에게 반환하는 실패 메시지에 사유가 남는다.** `DELETE`는 사유를 남길 수 없어 부적합하다.
> OPA 정책에 코디네이터 대상 **`KillQueryOwnedBy`** 허용 규칙이 필요하다.

**SQL 대안**: `system.runtime.queries` 컬럼은 `query_id`, `state`, `user`, `source`, `query`, `resource_group_id`, `queued_time_ms`, `analysis_time_ms`, `planning_time_ms`, `created`, `started`, `last_heartbeat`, `end`, `error_type`, `error_code`. **CPU time·메모리 컬럼이 없다** → FR-QL-01의 리소스 소비 표시가 필요하면 `/v1/query`(`BasicQueryStats`)를 써야 한다. kill 프로시저는 `CALL system.runtime.kill_query(query_id => '...', message => '...')`.

**설계 주의**: 코디네이터 폴링이 부하를 유발할 수 있다. NFR-PERF-03(코디네이터 CPU 1% 미만) 준수. 폴링 주기와 캐시 전략을 명시할 것.

## FR-ROUTING-VIEW (P1) — 라우팅 규칙/그룹 조회

| ID       | 내용                                              | AC        |
| -------- | ------------------------------------------------- | --------- |
| FR-RV-01 | 현재 라우팅 규칙 목록 조회 (읽기 전용)            | 규칙 표시 |
| FR-RV-02 | Routing group별 소속 클러스터 및 헬스             | 그룹 트리 |
| FR-RV-03 | 최근 라우팅 결정 샘플 (어떤 쿼리가 어느 그룹으로) | 샘플 조회 |

## FR-ROUTING-SVC (P2) — External Routing Service

**배경**: Gateway 라우팅 규칙은 stateless이므로 "사용량 기반" 라우팅이 불가하다. 라우팅 규칙은 별도 커스텀 서비스로 구현해 URL로 연결할 수 있다.

| ID       | 내용                                           | AC                       |
| -------- | ---------------------------------------------- | ------------------------ |
| FR-RS-01 | HTTP POST로 요청 정보 수신 → routingGroup 반환 | 규격 준수 응답           |
| FR-RS-02 | 사용량 기반 분기 (사용자별 최근 소비량 참조)   | 상태 저장소 연동         |
| FR-RS-03 | SQL 텍스트 휴리스틱 기반 복잡도 추정 분기      | 추정 근거 로깅           |
| FR-RS-04 | 서비스 장애 시 Gateway 기본 동작으로 폴백      | 서비스 다운 시 쿼리 정상 |

**✅ Bolt 0 — 연동 규격 확정** (`TRINO_VERIFIED.md` §T2-6)

활성화: `routingRules.rulesEngineEnabled: true` + `routingRules.rulesType: EXTERNAL` + `rulesExternalConfiguration.urlPath` (`excludeHeaders` 로 전달 제외 헤더 지정).

**요청** (Gateway → 서비스, POST): `excludeHeaders` 외 전 헤더 + `remoteUser`, `method`, `requestURI`, `queryString`, `session`, `remoteAddr`, `remoteHost`, `parameterMap`, 그리고 `analyzeRequest: true` 시 `trinoRequestUser` / `trinoQueryProperties`.

**응답** (HTTP 200 + JSON):

```json
{ "routingGroup": "group-name", "errors": ["..."], "externalHeaders": { "header": "value" } }
```

- `routingGroup`은 **하나만** 반환 가능.
- **`errors`가 null이 아니면 Gateway가 기본 그룹으로 라우팅한다** → FR-RS-04의 정상 경로.

**⚠️ 미해소 G-3 (R2 착수 전 필수)**: **서비스가 무응답/타임아웃일 때의 Gateway 동작은 문서에 없다.** `errors` 응답 시의 폴백만 문서화되어 있다. **NFR-ISOLATION 직결 사항이므로 실환경 실험으로 확인한 뒤에 이 기능에 착수한다.** 무응답 시 Gateway가 요청을 블로킹한다면 이 설계 자체가 성립하지 않는다.

**절대 규칙**: 이 서비스는 쿼리 경로에 **동기적으로** 들어간다. OPA와 동일한 위험 구조다.

- 타임아웃을 엄격히 설정하고, 실패 시 **defaultRoutingGroup으로 폴백**해야 한다.
- 이 서비스가 죽어서 쿼리가 막히면 NFR-ISOLATION 위반이다.
- **복잡도 추정은 "추정"임을 명시한다.** 실행 전에는 플랜·통계가 없어 정확한 복잡도 산정이 불가하다. 오분류를 허용하는 설계여야 한다.

**[DEFER]** 성능 기반 동적 가중치가 필요해질 경우, 구현 위치는 본 서비스가 아니라 **커스텀 Router Provider**다 (`StochasticRoutingManager` 상속). 외부 서비스 방식은 단독 클러스터 그룹을 요구해 그룹 내 자동 failover와 least-loaded를 상실한다.

## FR-CLUSTER-OPS (P1) — 클러스터 설정 변경 및 재시작

| ID       | 내용                                      | AC                    |
| -------- | ----------------------------------------- | --------------------- |
| FR-CO-01 | 클러스터 설정 조회 및 변경 (Ansible 경유) | 변경 반영 확인        |
| FR-CO-02 | 컴포넌트 재시작 — **안전 시퀀스 강제**    | 시퀀스 위반 불가      |
| FR-CO-03 | 재시작 진행 상태 및 결과 표시             | 단계별 표시           |
| FR-CO-04 | 변경 사유 필수 + 감사 기록                | reason 없이 실행 불가 |

**FR-CO-02 안전 시퀀스 (건너뛰기 불가)**

1. 대상 클러스터를 Gateway routing group에서 비활성화
2. 신규 쿼리 유입 중단 확인
3. 실행 중 쿼리 완료 대기 (타임아웃 설정)
4. 재시작
5. 헬스 정상 확인 후 routing group 재활성화

> **이 시퀀스를 건너뛰는 재시작 버튼은 구현하지 않는다.** 코디네이터 재시작은 in-flight 쿼리를 전멸시킨다.

## FR-CATALOG (P2) — 카탈로그 관리

| ID       | 내용                                                     | AC                |
| -------- | -------------------------------------------------------- | ----------------- |
| FR-CT-01 | 카탈로그 목록 및 커넥터 조회                             | 목록 표시         |
| FR-CT-02 | 카탈로그 등록 (CREATE CATALOG)                           | 등록 후 조회 가능 |
| FR-CT-03 | 카탈로그 제거 (DROP CATALOG) + **재시작 필요 경고 표시** | 경고 노출         |
| FR-CT-04 | 자격증명은 `'${ENV:VAR}'` 환경변수 참조로 전달. 평문 금지 | 평문 입력 차단    |
| ~~FR-CT-05~~ | ~~카탈로그 변경 (ALTER CATALOG)~~ | **요구사항으로 등재하지 않는다.** Trino 477에 `ALTER CATALOG` 부재 (`TRINO_VERIFIED.md` §T1-6). 변경 = DROP+CREATE이며 Hive/Iceberg에서는 재시작을 수반하므로 "변경 버튼"은 무중단으로 오인된다 |

**중대 제약 (UI에 반드시 표시)**

- `catalog.management=dynamic` 필요. **이 기능은 experimental이며 보안 영향이 있다.**
- **Hive, Iceberg, Delta Lake, Hudi 커넥터는 DROP 시 리소스가 완전히 해제되지 않아, 제대로 정리하려면 코디네이터와 워커를 재시작해야 한다.** 우리 주력이 Hive/Iceberg이므로 **"무중단 카탈로그 제거는 불가"** 를 명시할 것.
- DROP은 실행 중 쿼리를 중단시키지 않으나 신규 쿼리에는 사용 불가가 된다.
- `catalog.store=file`이면 Trino 프로세스가 카탈로그 디렉토리 쓰기 권한을 가져야 한다.

**[NEEDS-HUMAN-DECISION]** `catalog.store` 선택 — `file`(쓰기 권한 필요, 골든 이미지 읽기전용 전략과 충돌 가능) vs `memory`(재시작 시 소실). 운영 정책 결정 필요. **R4 항목이므로 지금 결정하지 않아도 R1 착수에 지장 없다.**

~~**[VERIFY]** Trino 477에서 `catalog.management`, `catalog.store` 지원 값 및 ALTER CATALOG 지원 여부~~
**✅ Bolt 0 해소** — `TRINO_VERIFIED.md` §T1-6.

| property | 허용값 | 기본값 |
| --- | --- | --- |
| `catalog.management` | `static`, `dynamic` | `static` |
| `catalog.store` | `file`, `memory` | `file` |
| `catalog.prune.update-interval` | duration | `5s` (최소 `1s`) |
| `catalog.config-dir` | string | `etc/catalog/` |
| `catalog.disabled-catalogs` | 콤마 구분 | — |
| `catalog.read-only` | string | `false` |

**`ALTER CATALOG`는 존재하지 않는다.**

**공식 문서 원문 경고 (UI에 그대로 노출)**
1. *"This feature is experimental only. Because of the security implications the syntax might change and be backward incompatible."*
2. DROP 시 리소스 미해제 커넥터: **Hive, Iceberg, Delta Lake, Hudi** (HDFS/S3/GCS/Azure를 읽는 모든 커넥터)
3. *"The complete `CREATE CATALOG` query is logged, and visible in the Web UI. This includes any sensitive properties, like passwords and other credentials."*

**FR-CT-04 구현 방법 (확정)**: `CREATE CATALOG example USING postgresql WITH ("connection-password" = '${ENV:POSTGRES_PASSWORD}')` 형태로 환경변수를 참조한다. **해당 환경변수는 클러스터 전 노드에 secret으로 설정되어 있어야 하며**, 코디네이터에 없으면 쿼리가 실패한다.

## FR-BENCHMARK (P1) — 성능 테스트 하네스

**높은 재사용성**: Phase 0 워크로드 특성화, 설정 변경 검증, 업그레이드 회귀 검증, 증설 효과 측정, **클러스터 간 성능 편차 규명**에 모두 사용된다.

| ID       | 내용                                                                 | AC                  |
| -------- | -------------------------------------------------------------------- | ------------------- |
| FR-BM-01 | 표준 쿼리 세트 실행 및 결과 수집                                     | 결과 리포트 생성    |
| FR-BM-02 | 컴포넌트별 CPU/Network/Disk 사용량 동시 수집                         | 시계열 수집         |
| FR-BM-03 | 실행 간 비교 (before/after, 클러스터 간)                             | 차이 표시           |
| FR-BM-04 | **프로덕션 보호** — 대상 클러스터를 routing group에서 제외한 뒤 실행 | 미제외 시 실행 거부 |
| FR-BM-05 | 실제 프로덕션 쿼리 샘플 기반 세트 생성 (EventListener 데이터 활용)   | 샘플 추출 동작      |

**FR-BM-04는 타협 불가.** 프로덕션 트래픽을 받는 클러스터에 벤치마크를 돌리면 그 자체가 장애다.

## FR-PROVISION (P3) — 클러스터 셋업 자동화

| ID       | 내용                              | AC                     |
| -------- | --------------------------------- | ---------------------- |
| FR-PV-01 | 클러스터 단위 셋업 (워커 수 지정) | 셋업 완료 후 헬스 정상 |
| FR-PV-02 | Gateway 등록 및 이중화 기본 적용  | 기본값이 이중화        |
| FR-PV-03 | 골든 이미지 버전 지정             | 지정 버전 배포 확인    |
| FR-PV-04 | 셋업 진행 상태 및 롤백            | 실패 시 정리           |

> **범위 경고**: 항목 3(셋업 자동화)은 사실상 미니 Cloudera Manager 구축이다. R1에 넣으면 나머지가 전부 밀린다. R4 이후 배치.

## FR-UPGRADE (P3) — 버전 업그레이드

| ID       | 내용                                                                      | AC                 |
| -------- | ------------------------------------------------------------------------- | ------------------ |
| FR-UP-01 | **Blue/Green 방식만 지원.** in-place 금지                                 | in-place 경로 부재 |
| FR-UP-02 | 신규 클러스터 생성 → 검증 → routing group 전환 → 구 클러스터 drain → 폐기 | 단계별 진행        |
| FR-UP-03 | 전환 전 FR-BENCHMARK 자동 실행 및 회귀 확인                               | 성능 회귀 시 경고  |
| FR-UP-04 | 롤백 (routing group을 구 클러스터로 복귀)                                 | 롤백 동작          |

**근거**: 코디네이터 HA가 없으므로 in-place 업그레이드는 필연적 다운타임 + in-flight 쿼리 전멸을 부른다. Blue/Green이 유일하게 안전한 경로이며, 이는 "확장 단위 = 클러스터" 원칙과 일치한다.

## FR-LOG-DEEPLINK (P1) — 로그 시스템 컨텍스트 딥링크

**배경**: 로그 수집·인덱싱·검색은 자체 구현하지 않는다 (Loki/OpenSearch 위임). TMS의 가치는 **컨텍스트를 자동으로 채운 링크** 제공이다.

| ID       | 내용                                                         | AC                       |
| -------- | ------------------------------------------------------------ | ------------------------ |
| FR-LD-01 | 쿼리 상세 → 해당 쿼리 ID/시간대/노드로 필터된 로그 링크 생성 | 링크 클릭 시 필터 적용됨 |
| ~~FR-LD-02~~ | ~~노드 상세 → 해당 노드 로그 링크~~ → **R3 FR-FLEET으로 이관 (2026-08-06 승인)**. R1에 노드 상세 화면 자체가 없어 진입점이 없다 | — |
| FR-LD-03 | 헬스 이상 → 발생 시각 기준 로그 링크                         | 동일                     |

**⚠️ FR-QUERY-HISTORY 제외에 따른 데이터 소스 변경 (2026-08-06)**

FR-LD-01의 "쿼리 상세"는 원래 FR-QUERY-HISTORY 화면을 전제했다. 그 요구사항이 R1에서 빠졌으므로 **R1의 딥링크 진입점은 아래로 한정된다.**

| 진입점 | R1 가용 여부 | 소스 |
| --- | --- | --- |
| **실행 중** 쿼리 상세 | ✅ 가능 | FR-QUERY-LIVE (`/v1/query`) |
| **완료된** 쿼리 상세 | ❌ **R1 불가** | TMS에 완료 쿼리 데이터가 없다 → **기존 히스토리 프로젝트 소관** |
| 노드 상세 | ✅ 가능 | FR-CLUSTER-HEALTH / `system.runtime.nodes` |
| 헬스 이상 | ✅ 가능 | FR-CLUSTER-HEALTH |

> **설계 방침**: 딥링크 URL 생성기를 **쿼리 데이터 소스와 분리된 순수 함수**로 만든다 — 입력은 `(queryId, 시각범위, 노드)` 뿐이고 그 값이 어디서 왔는지 알지 못한다. 그래야 통합 시점에 기존 히스토리 시스템 화면에서도 **그대로 재사용**할 수 있다.
> **FR-PT-02(링크 허브)에 기존 쿼리 히스토리 시스템을 등록**해, 완료 쿼리 조회 동선을 R1에서도 끊기지 않게 한다.

---

## 부록 B — 릴리스 계획 (v0.2 갱신)

| 릴리스  | 포함                                                                                                    | 목표                                             |
| ------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| **R1**  | FR-PORTAL, ~~FR-QUERY-HISTORY~~(**제외 — 별도 프로젝트**), **FR-QUERY-LIVE**, FR-CLUSTER-HEALTH, FR-AUDIT-ACTION, **FR-LOG-DEEPLINK** | "지금 무슨 일이 일어나고 있고, 누가 무엇을 했나" |
| **R2**  | FR-WORKLOAD, **FR-ROUTING-VIEW**, FR-GATEWAY, FR-SLO, **FR-BENCHMARK**                                  | "성능과 워크로드를 측정·비교할 수 있다"          |
| **R3**  | FR-FLEET, **FR-CLUSTER-OPS**, FR-FLEET-DRIFT                                                            | "안전하게 조작할 수 있다"                        |
| **R4**  | **FR-CATALOG**, FR-OPA, FR-LOGLEVEL, **FR-ROUTING-SVC**                                                 | "세밀하게 제어할 수 있다"                        |
| **R5**  | **FR-PROVISION**, **FR-UPGRADE**                                                                        | "클러스터를 찍어낼 수 있다"                      |
| **R6+** | AIOps (`AIOPS.md`)                                                                                      | "스스로 운영한다"                                |

**변경점**: FR-CLUSTER-HEALTH를 R2→R1로 승격. 헬스 판정 결과가 라우팅 제외·AIOps 탐지의 입력이 되므로 가장 먼저 필요하다. FR-BENCHMARK도 R2로 승격 — 클러스터 간 성능 편차 규명이 시급한 실제 과제이기 때문이다.
