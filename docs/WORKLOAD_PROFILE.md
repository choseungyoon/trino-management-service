# WORKLOAD_PROFILE — 워크로드 특성화

**상태**: **초안 (템플릿). 데이터 미수집.**
**작성일**: 2026-08-04 (Bolt 0 Task 5)
**담당**: 수집은 **인간(플랫폼팀)**, 분석은 `trino-expert`
**차단하고 있는 것**: **B4(히스토리 저장소 선정)**, **FR-SLO 목표값**, `monitor.taskDelay` 튜닝, `queryHistoryHoursRetention` 설정

---

## 0. 왜 이 문서가 필요한가

> **"5만 사용자"는 사이징 기준이 될 수 없다.** 5만 명이 하루 1건씩 도는 것과 500명이 하루 100건씩 도는 것은 완전히 다른 시스템이다.
>
> 저장소 선정(B4)과 SLO 목표값은 **이 데이터 없이 결정할 수 없다.** 추정으로 정하면 R1 중반에 저장소를 갈아엎게 된다.

**이 문서를 채우기 전까지 FR-QUERY-HISTORY 구현에 착수하지 않는다.**

---

## 1. 수집 항목과 데이터 소스

| # | 항목 | 1차 소스 | 얻을 수 있나 | 값 |
|---|---|---|---|---|
| W1 | 일일 / 시간당 쿼리 수 | **Gateway DB `query_history`** | ✅ 직접 | *(미수집)* |
| W2 | 피크 동시 실행 쿼리 수 | **JMX `RunningQueries` 폴링** | ✅ (수집 시작 필요) | *(미수집)* |
| W3 | p50 / p95 쿼리 실행시간 | **JMX `ExecutionTime.FiveMinutes.P50`** 또는 임시 EventListener | ⚠️ 아래 §3 참조 | *(미수집)* |
| W4 | BI 툴 주도 vs 애드혹 비율 | **Gateway DB `query_history.source`** | ✅ 직접 | *(미수집)* |
| W5 | 평균 / 최대 결과셋 크기 | 임시 EventListener (`QueryStatistics.outputBytes` / `outputRows`) | ❌ Gateway DB로는 불가 | *(미수집)* |
| W6 | 사용자별 쿼리 분포 (상위 편중도) | **Gateway DB `query_history.user_name`** | ✅ 직접 | *(미수집)* |
| W7 | 클러스터별 쿼리 분포 | **Gateway DB `query_history.backend_url`** | ✅ 직접 | *(미수집)* — **Task 7 근본원인 규명의 입력이기도 하다** |
| W8 | 평균 이벤트 1건의 직렬화 크기 | 임시 EventListener 샘플링 | ❌ 실측 필요 | *(미수집)* |

---

## 2. Gateway DB에서 즉시 얻을 수 있는 것

### 스키마 (검증됨 — PostgreSQL, migration V1~V4 적용 후)

출처: `trinodb/trino-gateway` `gateway-ha/src/main/resources/postgresql/V1~V4__*.sql`

```
query_history
  query_id       VARCHAR(256) PRIMARY KEY
  query_text     text            -- V4 이전에는 VARCHAR(256)이라 잘려 있다
  created        bigint          -- epoch milliseconds. System.currentTimeMillis()
  backend_url    VARCHAR(256)
  user_name      VARCHAR(256)
  source         VARCHAR(256)    -- X-Trino-Source 헤더
  routing_group  VARCHAR(255)    -- V2에서 추가
  external_url   VARCHAR(255)    -- V3에서 추가
INDEX query_history_created_idx ON (created)
```

> **⚠️ 반드시 인지할 한계 (검증됨)**
> - **`created`는 쿼리 "제출" 시각이다.** Gateway 프록시 핸들러에서 `System.currentTimeMillis()`로 기록한다.
> - **종료 시각·실행시간·상태(성공/실패)·결과 크기 컬럼이 없다.** → **Gateway DB만으로 W3(실행시간)과 W5(결과셋 크기)는 산출 불가.**
> - `dataStore.queryHistoryHoursRetention` 설정만큼만 보존된다. **보존 기간을 먼저 확인할 것.** 짧으면 확보 가능한 표본 구간이 그만큼이다.
> - `dataStore.queryHistoryEnabled: false` 면 테이블이 비어 있다.

### 수집 쿼리 (Gateway PostgreSQL에서 실행)

> **읽기 전용 계정으로 실행할 것.** 운영 Gateway DB다.

**W1 — 시간대별 쿼리 수 (최근 30일)**

```sql
SELECT date_trunc('hour', to_timestamp(created / 1000)) AS hour,
       count(*) AS query_count
FROM query_history
WHERE created >= (extract(epoch FROM now()) - 30*86400) * 1000
GROUP BY 1
ORDER BY 1;
```

**W1' — 일별 총량 및 피크 시간대 요약**

```sql
WITH hourly AS (
  SELECT date_trunc('hour', to_timestamp(created / 1000)) AS hour, count(*) AS c
  FROM query_history
  WHERE created >= (extract(epoch FROM now()) - 30*86400) * 1000
  GROUP BY 1
)
SELECT date_trunc('day', hour) AS day,
       sum(c)  AS daily_queries,
       max(c)  AS peak_hour_queries,
       round(avg(c)) AS avg_hour_queries
FROM hourly GROUP BY 1 ORDER BY 1;
```

**W4 — source별 분포 (BI 툴 vs 애드혹 판별)**

```sql
SELECT coalesce(source, '(null)') AS source,
       count(*) AS query_count,
       round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct
FROM query_history
WHERE created >= (extract(epoch FROM now()) - 30*86400) * 1000
GROUP BY 1 ORDER BY 2 DESC;
```

> 판별 기준: Superset은 보통 `superset`, JDBC/CLI는 `trino-jdbc` / `trino-cli`, Python 클라이언트는 `trino-python-client`. **`source`가 null인 비율이 높으면 그 자체가 발견 사항이다** (클라이언트가 헤더를 안 보냄 → 라우팅 규칙에서도 source를 못 쓴다).

**W6 — 사용자별 편중도 (상위 소수가 대부분을 차지하는가)**

```sql
WITH per_user AS (
  SELECT user_name, count(*) AS c
  FROM query_history
  WHERE created >= (extract(epoch FROM now()) - 30*86400) * 1000
  GROUP BY 1
), ranked AS (
  SELECT user_name, c,
         row_number() OVER (ORDER BY c DESC) AS rn,
         sum(c) OVER () AS total,
         count(*) OVER () AS user_count
  FROM per_user
)
SELECT user_count                                              AS distinct_users,
       max(total)                                              AS total_queries,
       round(100.0 * sum(c) FILTER (WHERE rn <= 10)  / max(total), 2) AS top10_pct,
       round(100.0 * sum(c) FILTER (WHERE rn <= 100) / max(total), 2) AS top100_pct
FROM ranked GROUP BY user_count;
```

> **`distinct_users` 값이 "5만 사용자"의 현실 검증이다.** 최근 30일 실제 쿼리 사용자 수가 5만과 얼마나 다른지 확인하라. 이 숫자가 사이징의 진짜 기준이다.

**W7 — 클러스터별 분포 (Task 7 입력)**

```sql
SELECT backend_url,
       routing_group,
       count(*) AS query_count,
       round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct
FROM query_history
WHERE created >= (extract(epoch FROM now()) - 7*86400) * 1000
GROUP BY 1, 2 ORDER BY 3 DESC;
```

> **기본 라우터가 난수 분배이므로 이 값은 50:50에 가까워야 한다** (TRINO_VERIFIED §T2-1). 크게 벗어나면 라우팅 규칙이나 헬스체크 이슈가 이미 있다는 뜻이다.
> **S1 적용 후 이 쿼리를 다시 돌려 변화를 측정한다.** 느린 클러스터의 비율이 떨어져야 정상이다.

---

## 3. Gateway DB로 안 되는 것 — 보완 수집

### W2 (동시 실행) · W3 (실행시간) — JMX 폴링

두 클러스터 코디네이터에서 아래를 주기 수집한다 (SETUP **S6** 로 커버됨).

| 항목 | ObjectName:Attribute |
|---|---|
| 실행/대기 쿼리 수 | `trino.execution:name=QueryManager:RunningQueries` |
| 실행시간 P50 | `trino.execution:name=QueryManager:ExecutionTime.FiveMinutes.P50` |
| 시작 쿼리 수 | `trino.execution:name=QueryManager:StartedQueries.FiveMinute.Count` |
| 실패 쿼리 수 | `trino.execution:name=QueryManager:FailedQueries.FiveMinute.Count` |

수집 경로: **`GET /v1/jmx/mbean/{objectName}`** (HTTP, `MANAGEMENT_READ` 권한 필요 — TRINO_VERIFIED §T1-7). RMI 불필요.

> **⚠️ P95는 이 경로로 직접 안 나온다.** 문서에 명시된 것은 `ExecutionTime.FiveMinutes.P50` 과 `WallInputBytesRate.FiveMinutes.P90` 이다. **실제 사용 가능한 분위수 속성은 `GET /v1/jmx/mbean/trino.execution:name=QueryManager` 응답에서 직접 확인할 것** (미확인 사항 G-5/G-6와 함께 처리).
>
> **대안 (더 정확함)**: `system.runtime.queries` 를 주기 폴링해 종료 쿼리의 `created`/`started`/`end` 로 분포를 직접 계산한다. 컬럼은 검증됨 (TRINO_VERIFIED §T1-5). 단 폴링 주기보다 짧게 살았다 죽는 쿼리는 놓친다.

### W5 (결과셋 크기) · W8 (이벤트 크기) — 임시 EventListener

**이것이 B4 결정의 핵심 입력이다. 우회로가 없다.**

> **CLAUDE.md 절대 규칙**: Bolt 0은 코드 작성 금지다. 아래는 **Bolt 1 이후 수행할 절차의 명세**이며, 지금 구현하지 않는다.

방식: 최소 기능의 EventListener를 **1개 클러스터에, 24~48시간 한정**으로 붙여 `QueryCompletedEvent`를 **파일로 append** 한다. 저장소 구축 없이 크기와 분포만 실측한다.

- 반드시 **비동기 + 바운디드 큐 + 큐 만석 시 드롭**으로 만든다 (NFR-ISOLATION). 실측용 코드라도 예외 없다.
- 기록 대상: `QueryStatistics.outputBytes`, `outputRows`, `cpuTime`, `wallTime`, `peakUserMemoryBytes` + **직렬화 후 바이트 길이**
- **`taskStatistics`, `operatorSummaries`, `plan`, `jsonPlan` 은 제외한 상태와 포함한 상태를 각각 측정한다.** 둘의 차이가 저장소 용량 결정을 좌우한다 (TRINO_VERIFIED §T1-1).
- 실측 후 즉시 제거한다.

---

## 4. 산출 — 저장소 사이징 (B4 결정용)

수집이 끝나면 아래를 채운다.

```
일일 쿼리 수 (D)                = ______ 건/일      ← W1
평균 이벤트 크기 (S)            = ______ KB         ← W8 (화이트리스트 적용 후)
보존 기간 (R)                   = 90 일             ← FR-QH-07 기본값

원시 용량 = D × S × R           = ______ GB
인덱스 오버헤드 포함 (× 1.5~3)  = ______ GB
```

**판정 가이드 (권고이며, 실제 결정은 인간)**

| 조건 | 권고 |
|---|---|
| 일일 쿼리 100만 건 미만 **AND** 90일 용량 500 GB 미만 **AND** 검색이 정형(사용자/기간/상태/클러스터) | **PostgreSQL.** 기존 자산·운영 경험 재활용. 파티셔닝 + 적절한 인덱스로 NFR-PERF-01(p95 < 2초) 달성 가능 |
| 위를 초과 **OR** 쿼리 텍스트 전문검색이 요건 | **Elasticsearch/OpenSearch.** 단 신규 클러스터 운영 부담 발생 |

> **주의**: S7(로그 수집)에서 OpenSearch를 도입한다면 히스토리 저장소도 같은 것을 쓰는 편이 운영 부담 면에서 유리하다. **S7의 선택(Loki vs OpenSearch)과 B4를 함께 결정하라.**
> **주의**: `query_text`를 전문 저장할 것인가도 용량에 직결된다. 5만 사용자 규모에서 SQL 전문 보관은 감사 요건상 필요할 수 있으나, 길이 상한을 두는 것을 검토하라.

---

## 5. SLO 목표값 (FR-SLO 입력)

W2/W3 수집 후 아래를 채운다. **현재 실측값을 모르는 상태에서 목표값을 정하면 그것은 목표가 아니라 희망이다.**

| SLO | 현재 실측 | 목표 | 근거 |
|---|---|---|---|
| 쿼리 성공률 (가용성) | *(미수집)* | | 실패 쿼리 비율 = `FailedQueries` / `StartedQueries` |
| p95 쿼리 실행시간 | *(미수집)* | | W3 |
| 최대 큐 대기시간 | *(미수집)* | | `system.runtime.queries.queued_time_ms` |

---

## 6. 수집 체크리스트

- [ ] Gateway DB **읽기 전용 계정** 확보
- [ ] `dataStore.queryHistoryEnabled` / `queryHistoryHoursRetention` 현재값 확인 (→ 확보 가능한 표본 구간 결정)
- [ ] Gateway DB migration 버전 확인 (**V4 미적용이면 `query_text`가 256자로 잘려 있다**)
- [ ] W1 / W1' / W4 / W6 / W7 쿼리 실행 및 결과 기록
- [ ] S6(Prometheus/Grafana) 구축 → W2 / W3 수집 시작, **최소 1주 관측**
- [ ] `GET /v1/jmx/mbean/trino.execution:name=QueryManager` 응답으로 실제 분위수 속성 확정
- [ ] (Bolt 1 이후) 임시 EventListener로 W5 / W8 실측
- [ ] §4 사이징 표 작성 → **D-1(B4) 인간 결정**
- [ ] §5 SLO 표 작성 → FR-SLO 목표값 결정
