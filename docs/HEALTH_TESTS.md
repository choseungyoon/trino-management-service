# HEALTH_TESTS — FR-CLUSTER-HEALTH 테스트 카탈로그

> **Bolt 1 산출물 (U3)** · 작성 2026-08-06 · 상태: **인간 승인 대기**
> 출처 모델: Cloudera Manager Health Test (`MARKET_RESEARCH.md`)

---

## 1. 핵심 개념 — "프로세스 생존"과 "쿼리 수행 가능"은 다르다

`BACKLOG.md` 2-1의 판정 그대로다. **개별 프로세스가 전부 살아 있어도 클러스터가 쿼리를 못 받을 수 있다.** TMS의 차별점은 후자를 판정하는 **합성 헬스**다.

```
프로세스 생존  →  코디네이터 HTTP 200
쿼리 수행 가능  →  코디네이터 살아 있음 AND 기동 완료 AND 워커 충분 AND 메모리 여유 AND 실패율 정상
```

**이 판정 결과가 곧 R2 FR-ROUTING(라우팅 제외)과 R3 FR-CLUSTER-OPS(안전 시퀀스)의 입력이 된다.**

---

## 2. 상태 정의 (FR-CH-01)

| 상태 | 의미 | 색 |
|---|---|---|
| `GOOD` | 정상 | 초록 |
| `CONCERNING` | 동작하지만 악화 중. 조치 검토 | 노랑 |
| `BAD` | 쿼리 수행에 실질적 지장 | 빨강 |
| `UNKNOWN` | **판정 불가** (수집 실패, stale) | 회색 |

> **`UNKNOWN`을 `GOOD`으로 표시하지 않는다.** NFR-DEGRADE의 핵심이며 `frontend-dev` 필수 준수 항목이다.
> **stale 판정**: 스냅샷의 `collected_at` 이 `collector.stale_threshold_seconds`(기본 30초)를 넘으면 모든 테스트를 `UNKNOWN`으로 강등하고 UI에 "N초 전 데이터" 배지를 띄운다.

---

## 3. 테스트 카탈로그

각 테스트는 **판정식 + 임계값 + 조치 조언(FR-CH-02)** 을 반드시 갖는다. **조치 조언 없는 테스트는 머지하지 않는다.**

> **⚠️ 접근제어 전제 (2026-08-06 확인)**: 운영 환경은 `access-control.name=file` + `rules.json` 이다.
> - **H-01, H-02** → `/v1/info`(PUBLIC). `rules.json` 내용과 **무관하게 항상 동작**
> - **H-03 ~ H-07** → `/v1/jmx/mbean`(`MANAGEMENT_READ`). **`rules.json` 에 `system_information` 규칙이 없으면 전부 403** (기본값 = 전부 거부)
> - **H-08** → Gateway API. 접근제어와 무관
>
> 근거: `TRINO_VERIFIED.md` §T3-6, 조치: `ARCHITECTURE.md` §6-3-1
> **구현 시 요구사항**: JMX 수집이 403이면 해당 테스트를 `BAD`가 아니라 **`UNKNOWN`** 으로 표기하고, 조언에 *"TMS 서비스 계정에 system_information read 권한이 없다. rules.json 확인 필요."* 를 넣는다. **권한 문제를 클러스터 장애로 오인시키지 않는다.**

### H-01 · 코디네이터 응답성

| 항목 | 값 |
|---|---|
| 소스 | `GET /v1/info` — **`@ResourceSecurity(PUBLIC)`. 인증·인가 모두 불필요** (§T1-2) |
| 판정 | 200 응답 → `GOOD` / 타임아웃·5xx → `BAD` / 수집 실패 → `UNKNOWN` |
| 조언(BAD) | "코디네이터가 응답하지 않는다. systemd 유닛 상태와 코디네이터 로그를 확인하라. 이 클러스터는 신규 쿼리를 받지 못한다." + 로그 딥링크 |

> **H-01/H-02는 접근제어 설정과 무관하게 항상 동작한다.** `PUBLIC` 리소스이므로 basic auth도, OPA 규칙도 필요 없다.
> **이것이 마지막 보루다** — OPA가 죽어 `MANAGEMENT_READ` 호출이 전부 막혀도(§ARCHITECTURE §4) "코디네이터가 살아 있는가"만큼은 계속 답할 수 있다. **최소한의 시야를 잃지 않는 설계 자산이므로, 이 두 테스트를 인증이 필요한 경로로 바꾸지 않는다.**

### H-02 · 기동 완료 여부

| 항목 | 값 |
|---|---|
| 소스 | `GET /v1/info` 응답의 기동 상태 필드 |
| 판정 | 기동 완료 → `GOOD` / 기동 중 → `CONCERNING` |
| 조언(CONCERNING) | "코디네이터가 아직 기동 중이다. 카탈로그 로딩이 끝나지 않았을 수 있다. 수 분 후에도 지속되면 카탈로그 설정 오류를 의심하라." |

> **⚠️ 확인 불가**: `ServerInfo` 의 기동 상태 필드명을 Bolt 0에서 확정하지 않았다. **Bolt 2 착수 시 `GET /v1/info` 실응답으로 필드명을 확정한다.** 확정 전까지 이 테스트는 구현하지 않는다.

### H-03 · 워커 등록 수

| 항목 | 값 |
|---|---|
| 소스 | `trino.failuredetector:name=HeartbeatFailureDetector:ActiveCount` (§T1-7) |
| 기준 | `config.yaml` 의 `expected_workers` |
| 판정 | `active >= expected` → `GOOD` / `expected×0.8 <= active < expected` → `CONCERNING` / `active < expected×0.8` → `BAD` |
| 조언(BAD) | "워커 {expected}대 중 {active}대만 등록되어 있다. 미조인 워커의 systemd 상태와 discovery 설정을 확인하라. 클러스터 용량이 {pct}%로 떨어져 있다." |

> **`ActiveCount`가 코디네이터를 포함하는지 미확인.** Bolt 2에서 실측해 기준을 보정한다. 보정 전에는 임계값을 보수적으로 둔다.
> **노드 단위 식별(어느 워커가 빠졌는가)은 R1 범위 밖**이다 — R3 FR-FLEET 소관. R1은 "몇 대가 빠졌는가"까지만 답한다.

### H-04 · 힙 사용률

| 항목 | 값 |
|---|---|
| 소스 | `java.lang:type=Memory:HeapMemoryUsage` 의 `used` / `max` (§T1-7) |
| 판정 | `< 80%` → `GOOD` / `80~90%` → `CONCERNING` / `> 90%` → `BAD` |
| 조언(BAD) | "코디네이터 힙이 {pct}%다. GC 부하로 전체 쿼리가 느려진다. 동시 실행 쿼리 수를 확인하고, 지속되면 리소스 그룹 동시성 제한 또는 힙 증설을 검토하라." |
| 임계값 | FR-CH-05로 조정 가능 |

### H-05 · 쿼리 실패율

| 항목 | 값 |
|---|---|
| 소스 | `FailedQueries.FiveMinute.Count` / `StartedQueries.FiveMinute.Count` (§T1-7) |
| 판정 | `< 5%` → `GOOD` / `5~20%` → `CONCERNING` / `> 20%` → `BAD` |
| 조언 | "최근 5분 쿼리 실패율 {pct}%. 사용자 오류(문법)와 시스템 오류를 구분하려면 H-06을 함께 보라." + 로그 딥링크 |

> **분모가 0이면 `UNKNOWN`이다.** `GOOD`이 아니다 — 쿼리가 아예 안 들어오는 것 자체가 이상 신호일 수 있다.

### H-06 · 내부(시스템) 실패

| 항목 | 값 |
|---|---|
| 소스 | `InternalFailures.FiveMinute.Count` (§T1-7) |
| 판정 | `0` → `GOOD` / `1~5` → `CONCERNING` / `> 5` → `BAD` |
| 조언(BAD) | "최근 5분간 내부 오류 {n}건. 사용자 SQL 문제가 아니라 **엔진/인프라 문제**다. 코디네이터·워커 로그를 확인하라." + 로그 딥링크 |

> **H-05와 분리한 이유**: 사용자 문법 오류가 아무리 많아도 클러스터는 건강하다. 내부 실패는 1건도 정상이 아니다. **증상이 아니라 원인 계층이 다르다.**

### H-07 · OOM kill

| 항목 | 값 |
|---|---|
| 소스 | `trino.memory:name=ClusterMemoryManager:QueriesKilledDueToOutOfMemory` (§T1-7, **누적값**) |
| 판정 | 직전 스냅샷 대비 증가량. `0` → `GOOD` / `1~3` → `CONCERNING` / `> 3` → `BAD` |
| 조언(BAD) | "메모리 부족으로 쿼리 {n}건이 강제 종료됐다. 대용량 쿼리가 클러스터를 압박하고 있다. FR-QUERY-LIVE에서 메모리 상위 쿼리를 확인하라." |

> **누적 카운터이므로 반드시 증분으로 판정한다.** 절대값으로 판정하면 재시작 이후 영원히 `BAD`가 된다.

### H-08 · Gateway 백엔드 등록 상태 *(선택적)*

| 항목 | 값 |
|---|---|
| 소스 | Gateway `GET /gateway/backend/all` (§T2-3) |
| 전제 | `gateway.enabled: true` (B6 해소 후) |
| 판정 | 이 클러스터가 목록에 있고 `active: true` → `GOOD` / 목록에 있으나 비활성 → `CONCERNING` / 목록에 없음 → `BAD` / Gateway 미가용 → `UNKNOWN` |
| 조언(CONCERNING) | "이 클러스터가 Gateway에서 비활성 상태다. 의도한 것이라면(작업 중) 무시하라. 아니라면 신규 쿼리가 이 클러스터로 오지 않는다." |

> **`gateway.enabled: false` 이면 이 테스트는 카탈로그에서 아예 제외한다.** 항상 `UNKNOWN`인 테스트를 보여주면 노이즈다.

---

## 4. Roll-up 규칙 (FR-CH-04)

```
클러스터 전체 상태 = 활성화된 개별 테스트 중 가장 나쁜 상태
                     (BAD > CONCERNING > UNKNOWN > GOOD)
```

- **`UNKNOWN`이 `GOOD`보다 나쁘다.** 모른다는 것은 괜찮다는 뜻이 아니다.
- FR-CH-03: 개별 테스트를 비활성화하면 roll-up 계산에서 **제외**된다.
- FR-CH-04: roll-up 자체를 개별 테스트와 **별도로** 비활성화할 수 있다 — 개별 지표가 나빠도 전체는 정상으로 두는 운영 판단을 허용한다.
- 활성/비활성 변경은 **관리자 한정 + `reason` 필수 + 감사 기록**(FR-AA-01). 헬스 테스트를 끄는 것은 시야를 좁히는 행위이므로 흔적을 남긴다.

---

## 5. 상태 전이 이벤트 (FR-CH-07)

**상태가 바뀌는 순간에만** 기록한다. 매 폴링마다 기록하지 않는다.

```
health_event(cluster, test_id, from_state, to_state, observed_value, threshold, advice, occurred_at)
```

- 저장소: TMS PostgreSQL (D-004)
- Alertmanager 연동은 **R1 범위 밖**이다 — TMS는 이벤트를 남기고, 알림 발송은 `sre-agent`가 Prometheus/Alertmanager로 구성한다 (비목표: 알림 엔진 자체 구현 금지)
- **플래핑 억제**: 동일 테스트가 `stabilization_polls`(기본 3회) 연속 같은 상태여야 전이를 확정한다. 한 번 튄 값으로 이벤트를 만들지 않는다

---

## 6. R1 범위에서 제외한 테스트

`REQUIREMENTS.md` FR-CLUSTER-HEALTH의 "최소 health test 목록" 중 아래는 R1에서 구현하지 않는다. 근거를 남긴다.

| 테스트 | 제외 사유 | 이관 |
|---|---|---|
| GC pause 시간 | 대응 MBean 이름 미검증. `TRINO_VERIFIED.md`에 없다 | 검증 후 추가 |
| 리소스 그룹 큐 뎁스 | `/v1/resourceGroupState/{id}` 는 검증됐으나 **어떤 그룹을 볼지가 리소스 그룹 설정에 의존** | R2 FR-WORKLOAD |
| 디스크 여유공간 | node_exporter 소관. TMS가 노드에 붙지 않는다 | S6 / Grafana |
| systemd 유닛 상태 | TMS는 R1에서 노드에 에이전트를 두지 않는다 | R3 FR-FLEET |
| OPA 사이드카 응답성 | R4 FR-OPA | R4 |
| **FR-CH-06 반복 크래시 감지** | systemd `Restart=` 이력이 필요하다. 노드 접근 전제 | R3 FR-FLEET |

> **FR-CH-06(반복 크래시 감지)을 R1에서 뺀 것은 요구사항 축소다.** 인간 승인 대상이다.
> 부분적 대안: H-01/H-02가 짧은 간격으로 `BAD`↔`GOOD`을 반복하면 그 자체가 반복 크래시 신호다. **상태 전이 이벤트 이력으로 사후 확인은 가능**하나, 자동 플래그는 R3로 미룬다.

---

## 7. 구현 계약

```
HealthTest:
  id: str                  # "H-01"
  name: str
  enabled: bool
  evaluate(snapshot) -> HealthResult

HealthResult:
  state: GOOD | CONCERNING | BAD | UNKNOWN
  observed_value: Any
  threshold: Any
  advice: str              # BAD/CONCERNING이면 필수 — 빈 문자열 금지
```

**`reviewer` 체크 항목**: `state in (BAD, CONCERNING)` 인데 `advice`가 비어 있으면 반려.
