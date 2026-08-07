# TMS Backlog — 전체 개발 항목 통합 점검

**버전**: 0.2 (사용자 제시 8개 항목 반영)
**판정 기준**: ① 이미 존재하는가 ② 엔진이 지원하는가 ③ 우리가 만들어야 하는가 ④ 만들지 말아야 하는가

---

## 0. 판정 요약

| 판정 | 의미 | 항목 수 |
|---|---|---|
| **SETUP** | 개발 아님. 설정으로 해결 | 4 |
| **BUILD** | 우리가 만들어야 함 | 12 |
| **DELEGATE** | 기존 OSS 도구에 위임 | 5 |
| **REJECT** | 만들지 않음 | 3 |
| **BLOCKED** | 선결 조건 해소 후 재판정 | 3 |

---

## 1. 사용자 제시 항목 → 판정 매핑

### 1. 클러스터 라우팅 관리

| # | 요구 | 판정 | 근거 / 조치 |
|---|---|---|---|
| 1-1a | 사용자 기반 라우팅 | **SETUP** | Gateway 라우팅 규칙 엔진이 지원. `analyzeRequest=true` 시 TrinoRequestUser 사용 가능. MVEL 조건/액션 YAML 작성 |
| 1-1b | 카탈로그/스키마/테이블 기반 라우팅 | ~~BLOCKED~~ → **SETUP (S3)** | **Bolt 0 해소**: charset 버그는 [trino-gateway#1032](https://github.com/trinodb/trino-gateway/issues/1032)이며 [PR #1054](https://github.com/trinodb/trino-gateway/pull/1054)로 수정되어 **Gateway 19 (2026-05-11)** 에 포함됐다. **조치는 개발이 아니라 Gateway ≥19 업그레이드.** `TrinoQueryProperties.getTables()`가 부분 수식 테이블을 완전 수식화해 주므로 규칙 작성이 용이하다 |
| 1-1c | 사용량 기반 라우팅 | **BUILD** | 라우팅 규칙은 stateless. 외부 상태 필요 → External Routing Service 개발 |
| 1-1d | 쿼리 복잡도 기반 라우팅 | **BUILD (제한적)** | 실행 전에는 플랜·통계가 없어 진짜 복잡도 산정 불가. SQL 텍스트 휴리스틱(조인 수, 서브쿼리 깊이, 대상 테이블 크기)만 가능. **"추정"임을 명시하고 오분류 허용 설계** |
| 1-2 | Cluster Pool 내 가중치 분배 (6:4) | **REJECT → SETUP으로 대체** | **목적 확인 완료**: "동일 스펙인데 느린 클러스터에 트래픽을 덜 주기"(Impala 경험). 카나리 아님. → `QueryCountBasedRouterProvider`가 running/queued 수 기반 least-loaded 라우팅으로 **동일 목적을 자동·동적으로 달성**. 정적 가중치는 열등(고정 상수화, 미신 상수, 성능 역전 시 오작동). **개발 불필요** |
| 1-2b | (참고) 기본 라우터 확인 | **SETUP (S1)** | **Bolt 0 확인**: 기본 `StochasticRoutingManager`의 선택 로직은 소스상 문자 그대로 `RANDOM.nextInt() % backends.size()` 다. `modules`에 `io.trino.gateway.ha.module.QueryCountBasedRouterProvider` 추가 + **`clusterStatsConfiguration.monitorType`을 `UI_API` 또는 `JDBC`로 변경(기본 `INFO_API`로는 통계가 안 모여 라우터가 무력)** + `backendState` 설정. 통계 주기는 `monitor.taskDelay`(기본 1분) |
| 1-2c | (조건부 잔여) 성능 기반 동적 가중치 | **DEFER** | least-loaded로 미해결 시에만. **구현 위치는 External Routing Service가 아니라 커스텀 Router Provider** — `StochasticRoutingManager` 상속, `provideAdhocBackend`/`provideBackendForRoutingGroup` 오버라이드, `updateBackEndStats`로 ClusterStats 수신. 외부 서비스 방식은 단독 클러스터 그룹이 필요해 그룹 내 자동 failover와 least-loaded를 상실 |
| 1-3 | 비정상 클러스터 라우팅 제외 | **SETUP** | Gateway가 비정상 클러스터를 자동 제외. 개발 불필요 |

> **핵심 설계 결론**: 1-1c/1-1d 때문에 **External Routing Service**가 TMS의 신규 컴포넌트로 추가된다. Gateway 라우팅 규칙은 별도 커스텀 서비스로 구현해 URL로 연결할 수 있고, 이 서비스가 동적 규칙 변경과 임의 로직을 담당한다. 즉 Gateway를 포크하지 않고 확장 가능하다.

---

### 2. 클러스터 상태 체크 및 모니터링

| # | 요구 | 판정 | 근거 / 조치 |
|---|---|---|---|
| 2-1 | 컴포넌트별 상태 체크 (Coordinator/Worker/Gateway/Catalog) | **BUILD** | FR-CLUSTER-HEALTH로 흡수. **"쿼리 수행 가능한가"를 판정하는 합성 헬스**가 핵심 |
| 2-2 | CPU/Network/Disk 사용량 관리·모니터링 | **DELEGATE** | node_exporter + Prometheus + Grafana. **TMS에 차트 자체 구현 금지** |

> **2-1의 중요성**: 개별 프로세스가 살아 있어도 클러스터가 쿼리를 못 받을 수 있다(카탈로그 로드 실패, 워커 미조인, OPA 다운). **"프로세스 생존"과 "쿼리 수행 가능"은 다른 개념**이며, 후자를 판정하는 합성 헬스가 TMS의 차별점이다. 이 판정 결과가 곧 1-3(라우팅 제외)의 입력이 된다.

---

### 3. 클러스터 셋업 자동화

| # | 요구 | 판정 | 근거 / 조치 |
|---|---|---|---|
| 3-1a | 클러스터 단위 셋업 (워커 수 지정) | **BUILD** | Ansible playbook + 골든 이미지. TMS는 실행 트리거·진행 추적 |
| 3-1b | Gateway 옵션 및 이중화 셋업 | **BUILD** | 기본값을 이중화로 강제 |
| 3-2 | 버전 패치 및 업그레이드 | **BUILD (고위험)** | **Blue/Green만 허용.** in-place 업그레이드 금지 |

> **경고**: 항목 3은 사실상 미니 Cloudera Manager 구축이다. 범위가 크므로 R3 이후로 배치한다. R1에 넣으면 나머지가 전부 밀린다.
>
> **업그레이드 전략 확정**: 코디네이터 HA가 없으므로 in-place 업그레이드는 필연적 다운타임 + 쿼리 전멸을 부른다. **신규 클러스터를 목표 버전으로 띄우고 → Gateway routing group에 추가 → 기존 클러스터 비활성화 → drain → 폐기**가 유일하게 안전한 경로다. 이는 앞서 정의한 "확장 단위 = 클러스터" 원칙과 정확히 일치한다.

---

### 4. 클러스터 성능 테스트

| # | 요구 | 판정 | 근거 / 조치 |
|---|---|---|---|
| 4-1 | 표준 성능 테스트 실행 및 결과 추출 | **BUILD** | 벤치마크 하네스. 쿼리 성능 + 컴포넌트별 리소스 사용량 |

> **높은 가치.** 이 기능은 Phase 0 워크로드 특성화, 설정 변경 검증, 업그레이드 회귀 검증, 증설 효과 측정에 모두 재사용된다. **다만 프로덕션 클러스터에 벤치마크를 돌리면 안 된다.** 대상 클러스터를 routing group에서 먼저 제외한 뒤 실행하는 안전장치를 반드시 포함한다.
> 표준 셋: TPC-DS/TPC-H 일부 + **실제 프로덕션 쿼리 샘플**(EventListener 데이터에서 추출). 후자가 훨씬 유의미하다.

---

### 5. 카탈로그 관리

| # | 요구 | 판정 | 근거 / 조치 |
|---|---|---|---|
| 5-1a | 카탈로그 등록 | **BUILD (확정)** | `catalog.management=dynamic` 시 `CREATE CATALOG … USING … WITH (…)`. 새 워커는 코디네이터에서 현재 카탈로그 설정을 수신. **Bolt 0 검증 완료** |
| 5-1b | 카탈로그 변경 | ~~BLOCKED~~ → **REJECT** | **Bolt 0 해소: Trino 477에 `ALTER CATALOG`가 존재하지 않는다** (477 문서 트리에 `create-catalog` / `drop-catalog` / `show-catalogs` 만 있음). 변경 = DROP+CREATE이고, Hive/Iceberg는 DROP 시 재시작이 필요하므로 "변경 기능"을 UI로 주면 무중단으로 오인된다 |
| 5-1c | 카탈로그 제거 | **BUILD (경고 필수)** | **무중단 불가** |

> **중대 제약 (반드시 인지)**
> - dynamic catalog management는 **experimental이며 보안 영향**이 있다.
> - **Hive, Iceberg, Delta Lake, Hudi 커넥터는 DROP 시 리소스가 완전히 해제되지 않아, 제대로 정리하려면 코디네이터와 워커를 재시작해야 한다.** 우리 주력이 Hive/Iceberg이므로 **"카탈로그 제거 = 재시작 필요"를 UI에 명시**해야 한다.
> - DROP은 실행 중인 쿼리를 중단시키지 않지만 신규 쿼리에는 사용 불가가 된다.
> - `catalog.store=file`이면 **Trino 프로세스가 카탈로그 디렉토리 쓰기 권한 필요** → 골든 이미지의 읽기전용 마운트 전략과 충돌 가능. `[NEEDS-HUMAN-DECISION]`
> - **자격증명 평문 전달 금지.** 시크릿 매니저 사용 권장.
>
> ~~**`[VERIFY]`**: Trino 477에서 `catalog.management`, `catalog.store` 정확한 값과 ALTER CATALOG 지원 여부~~
> **✅ Bolt 0 해소** — `TRINO_VERIFIED.md` §T1-6 참조. `catalog.management`=`static`\|`dynamic`(기본 `static`), `catalog.store`=`file`\|`memory`(기본 `file`), 그 외 `catalog.prune.update-interval`(5s), `catalog.config-dir`, `catalog.disabled-catalogs`, `catalog.read-only`. **`ALTER CATALOG`는 없다.**
> **추가 확인 사실**: `CREATE CATALOG`는 `'${ENV:VAR}'` 환경변수 참조를 지원한다(전 노드에 secret 설정 필요) → FR-CT-04(평문 금지)를 이 방식으로 구현한다. 반대로 **`CREATE CATALOG` 쿼리 전문이 Web UI에 그대로 노출**되므로 평문 입력은 절대 금지다.

---

### 6. 클러스터 관리

| # | 요구 | 판정 | 근거 / 조치 |
|---|---|---|---|
| 6-1 | 설정 수정 및 적용 | **BUILD** | Ansible 기반. **변경 사유 필수 + 감사** |
| 6-2 | 클러스터/컴포넌트 재시작 | **BUILD (고위험)** | 코디네이터 재시작은 **in-flight 쿼리 전멸**. Gateway 비활성화 → drain → 재시작 순서 강제 |
| 6-3 | Worker 추가/제거 | **BUILD** | 제거 시 **graceful shutdown 선행 필수** |

> **6-2 안전 시퀀스 (위반 시 장애)**
> 1. 대상 클러스터를 Gateway routing group에서 비활성화
> 2. 신규 쿼리 유입 중단 확인
> 3. 실행 중 쿼리 완료 대기 (타임아웃 설정)
> 4. 재시작
> 5. 헬스 정상 확인 후 routing group 재활성화
>
> **이 시퀀스를 건너뛰는 재시작 버튼은 만들지 않는다.**

---

### 7. 클러스터 로그 관리

| # | 요구 | 판정 | 근거 / 조치 |
|---|---|---|---|
| 7-1 | 컴포넌트별 로그 관리 및 검색 | **DELEGATE** | Loki 또는 OpenSearch + Promtail/Filebeat. TMS는 **딥링크만** 제공 |

> **강한 권고**: 로그 수집·인덱싱·검색을 자체 구현하는 것은 이 프로젝트 최대의 함정이다. 성숙한 OSS가 존재하며, 자체 구현 시 수개월을 소모하고 결과물은 열등하다.
> **TMS가 제공할 가치**: 쿼리 ID / 노드 / 시간대를 자동으로 채운 **컨텍스트 딥링크**. 예: 실패 쿼리 상세 화면 → "이 쿼리 시점 이 노드의 로그 보기" 버튼 → Loki 쿼리 URL 생성. 이것만으로 운영자 체감은 충분하다.

---

### 8. 쿼리 모니터링 및 로그 관리

| # | 요구 | 판정 | 근거 / 조치 |
|---|---|---|---|
| 8-1 | 클러스터별 실행 중 쿼리 모니터링 | **BUILD** | FR-QUERY-LIVE 신설 (기존 FR-QUERY-HISTORY는 완료 쿼리 대상) |
| 8-2 | 쿼리 수행 로그 관리 | **BUILD** | FR-QUERY-HISTORY로 흡수 |

> **구분 필요**: EventListener의 `QueryCompletedEvent`는 **완료된** 쿼리만 준다. "지금 실행 중"은 코디네이터를 실시간 조회해야 한다. 두 데이터 소스가 다르므로 요구사항도 분리한다.

---

## 2. 통합 기능 목록 (최종)

### R1 — 가시성 확보

| ID | 기능 | 출처 |
|---|---|---|
| FR-PORTAL | SSO 포털 + 도구 딥링크 허브 | 기존 |
| ~~FR-QUERY-HISTORY~~ | ~~완료 쿼리 히스토리/감사~~ → **R1 제외. 별도 프로젝트로 이미 구현됨. 추후 통합** (2026-08-06 인간 결정) | 기존 + 사용자 8-2 |
| FR-QUERY-LIVE | 실행 중 쿼리 실시간 모니터링 | **사용자 8-1** |
| FR-CLUSTER-HEALTH | 합성 헬스 체크 + 조치 조언 | 기존 + **사용자 2-1** |
| FR-AUDIT-ACTION | 운영 액션 감사 (사유 필수) | 기존 |
| FR-LOG-DEEPLINK | 로그 시스템 컨텍스트 딥링크 | **사용자 7-1 (축소)** |

### R2 — 워크로드 및 라우팅 제어

| ID | 기능 | 출처 |
|---|---|---|
| FR-WORKLOAD | 리소스 그룹 관리 뷰 (**데이터 소스 = Trino.** Gateway 19가 리소스 그룹 기능을 제거함) | 기존 |
| FR-ROUTING-VIEW | 라우팅 규칙/그룹 조회 | **사용자 1-1** |
| FR-ROUTING-SVC | External Routing Service (사용량·복잡도) | **사용자 1-1c/1-1d** |
| FR-GATEWAY | Gateway/Routing Group 콘솔 | 기존 |
| FR-SLO | SLO / Error Budget | 기존 |

### R3 — 운영 액션

| ID | 기능 | 출처 |
|---|---|---|
| FR-FLEET | Fleet 인벤토리 + graceful shutdown | 기존 + **사용자 6-3** |
| FR-CLUSTER-OPS | 설정 변경·재시작 (안전 시퀀스 강제) | **사용자 6-1/6-2** |
| FR-FLEET-DRIFT | Config drift 추적 | 기존 |
| FR-CATALOG | 카탈로그 등록/제거 | **사용자 5-1** |
| FR-BENCHMARK | 성능 테스트 하네스 | **사용자 4-1** |

### R4 — 프로비저닝 및 확장

| ID | 기능 | 출처 |
|---|---|---|
| FR-PROVISION | 클러스터 단위 셋업 자동화 | **사용자 3-1** |
| FR-UPGRADE | Blue/Green 버전 업그레이드 | **사용자 3-2** |
| FR-OPA | OPA 정책 상태 가시성 | 기존 |
| FR-LOGLEVEL | 런타임 로그 레벨 (**축소 존치** — JMX MBean 경유, 재시작 후 미유지) | 기존 |

### R5+ — AIOps (별도 문서 `AIOPS.md` 참조)

---

## 3. 선결 조건 (Blocker) — **Bolt 0 판정 반영 (2026-08-04)**

> 판정 근거는 `BOLT_0_RESULT.md` §2, 기술적 사실은 `TRINO_VERIFIED.md` 참조.

| # | 조건 | 상태 | 판정 |
|---|---|---|---|
| ~~B1~~ | ~~Gateway charset 이슈 해소~~ | **해소 (조건부)** | 업스트림 수정 완료 — [#1032](https://github.com/trinodb/trino-gateway/issues/1032) → [PR #1054](https://github.com/trinodb/trino-gateway/pull/1054) → **Gateway 19 (2026-05-11)** 포함. **조치 = Gateway ≥19 업그레이드.** 업그레이드 시 파괴적 변경 2건 동반: ① 리소스 그룹 관리 기능 전면 제거([#656](https://github.com/trinodb/trino-gateway/issues/656)) ② `addXForwardedHeaders` → `forwardedHeadersEnabled` 개명([#1005](https://github.com/trinodb/trino-gateway/pull/1005)) |
| ~~B2~~ | ~~`catalog.management` 동작 검증~~ | **해소** | `dynamic` 동작 확인. **단 `ALTER CATALOG` 부재** → 5-1b를 REJECT로 재판정. `catalog.store` 선택은 `[NEEDS-HUMAN-DECISION]` 유지(R4) |
| ~~B3~~ | ~~가중치 라우팅 목적 확인~~ | **해소** | 목적=느린 클러스터 트래픽 감소 → least-loaded 라우터로 대체. **근본 원인 규명이 우선 과제** — 절차는 `BOLT_0_RESULT.md` §5에 체크리스트로 확정 |
| ~~B4~~ | ~~히스토리 저장소 선정~~ | **R1 범위에서 이월** | **2026-08-06**: FR-QUERY-HISTORY가 별도 프로젝트로 이미 구현되어 R1에서 제외됨 → **B4는 더 이상 R1을 막지 않는다.** 두 프로젝트 통합 작업 시점으로 이월. `WORKLOAD_PROFILE.md`는 FR-SLO(R2) 목표값 근거로 여전히 필요 |
| ~~B5~~ | ~~런타임 로그레벨 API 지원 여부~~ | **해소 — 사전 가정이 틀림** | **OSS Trino 477에 존재한다.** REST가 아니라 JMX MBean `io.airlift.log:name=Logging`(`setLevel`/`setRootLevel`). `Server.java`가 `LogJmxModule`을 무조건 등록. **→ FR-LOGLEVEL 폐기하지 않고 축소 존치.** 구현 방식은 D-2로 인간 결정 대기 |
| **B6** | **운영 Gateway 버전 및 설정 확인** | **부분 해소 (2026-08-07)** | **버전 19 확인** → Gateway 19의 리소스 그룹 제거(FR-WORKLOAD 데이터 소스 = Trino)와 charset 버그 수정(B1)이 모두 적용된 버전이다. **`databaseCache` 활성 확인** → `expireAfterWrite` 실제 값은 회신 대기(§T2-4: `1h` 기본, 만료 후 DB 다운이면 라우팅 실패). **잔여**: 백엔드 목록 등록 방식·API 인증·`proxyTo` 도달성. D-008로 클러스터 관리를 Gateway에 위임했으므로 R2 FR-GW-01 착수 전 필수. **R1은 막지 않는다** |

---

## 4. 명시적 비목표 (변경 없음 + 추가)

| 비목표 | 이유 |
|---|---|
| 웹 SQL 에디터 | Superset |
| 메트릭 차트 자체 구현 | Grafana |
| 알림 엔진 자체 구현 | Alertmanager |
| **로그 수집·인덱싱·검색 자체 구현** | **Loki/OpenSearch (신규 추가)** |
| 권한 관리 UI | OPA + Git |
| 데이터 카탈로그/데이터 프로덕트 | 범위 밖 |
| 쿼리 실행 프록시 | NFR-ISOLATION 위반 |
