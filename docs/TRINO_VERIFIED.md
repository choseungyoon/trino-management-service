# TRINO_VERIFIED — 검증 완료 사실

> **소유자**: `trino-expert`
> **목적**: 공식 문서 / 릴리스 태그 소스로 **확인된 사실만** 기록한다. 추측·전언·기억은 기록하지 않는다.
> **작성 시점**: 2026-08-04 (Bolt 0)
> **검증 대상 버전**: Trino **477** (문서 및 GitHub 태그 `477`), airlift **361** (Trino 477 `pom.xml`의 `dep.airlift.version`)

---

## 0. 이 문서를 읽는 법

| 표기 | 의미 |
|---|---|
| **확인** | Trino 477 공식 문서 또는 GitHub 태그 `477` 소스에서 직접 확인 |
| **확인(소스)** | 공식 문서에는 없으나 태그 `477` 소스에서 확인. **문서화되지 않은 내부 API이므로 버전 간 변경 위험이 있다** |
| **확인 불가** | 검증하지 못함. 추측으로 채우지 않음 |
| **실환경 확인 필요** | 문서상 사실은 확정했으나 우리 환경(인증 설정·네트워크·버전)에서의 동작은 미확인 |

**규칙**: 이 문서에 없는 config property / API 경로 / SPI 시그니처는 코드에 넣지 않는다.

### 검증 방법의 한계 (반드시 인지)

1. **Trino 477**: `https://trino.io/docs/477/...` 및 GitHub 태그 `477` 소스로 검증했다. 버전 고정이 확실하다.
2. **Trino Gateway**: 본 프로젝트가 **현재 운영 중인 Gateway 버전이 문서화되어 있지 않다.** 아래 §2의 검증은 `trinodb/trino-gateway` **`main` 브랜치 문서/소스** 기준이며, 릴리스 노트로 버전별 차이를 교차 확인한 항목만 버전을 명시했다. **운영 Gateway 버전 확정은 미해결 과제다 (§5 G-1).**
3. **OPA**: Trino 측 플러그인은 477 기준, OPA 서버 측은 openpolicyagent.org 최신 문서 기준이다.

---

## 1. Trino 477 API / SPI

### T1-1. EventListener SPI — **확인**

**설정** (문서: `/docs/477/develop/event-listener.html`)

| 파일 | 항목 |
|---|---|
| `etc/event-listener.properties` | `event-listener.name=<EventListenerFactory.getName() 반환값>` + 구현체별 property |
| `etc/config.properties` | `event-listener.config-files=etc/event-listener.properties,etc/event-listener-second.properties` (복수 리스너 등록 시) |

**인터페이스 시그니처** (소스: `core/trino-spi/.../io/trino/spi/eventlistener/EventListener.java` @477) — **확인**

```java
default void queryCreated(QueryCreatedEvent queryCreatedEvent) {}
default void queryCompleted(QueryCompletedEvent queryCompletedEvent) {}
@Deprecated(forRemoval = true)
default void splitCompleted(SplitCompletedEvent splitCompletedEvent)   // 기본 구현이 UnsupportedOperationException
default boolean requiresAnonymizedPlan()                                // 기본 false
default void shutdown() {}
```

> 모든 메서드가 `default`이므로 **`queryCompleted`만 구현하면 된다.** `splitCompleted`는 removal 예정이므로 **구현하지 않는다.**

**`QueryCompletedEvent` 최상위 필드** — **확인**

| 타입 | 필드 |
|---|---|
| `QueryMetadata` | `metadata` |
| `QueryStatistics` | `statistics` |
| `QueryContext` | `context` |
| `QueryIOMetadata` | `ioMetadata` |
| `Optional<QueryFailureInfo>` | `failureInfo` |
| `List<TrinoWarning>` | `warnings` |
| `Instant` | `createTime`, `executionStartTime`, `endTime` |

**FR-QUERY-HISTORY 스키마 설계에 직접 쓰이는 하위 필드** — **확인**

- `QueryMetadata`: `queryId`, `transactionId`, `encoding`, `query`, `updateType`, `preparedQuery`, `queryState`, `uri`, `tables`(`List<TableInfo>`), `routines`, `plan`, `jsonPlan`, `payloadProvider`
- `QueryContext`: `user`, `originalUser`, `originalRoles`, `principal`, `enabledRoles`, `groups`, `traceToken`, `remoteClientAddress`, `userAgent`, `clientInfo`, `clientTags`, `clientCapabilities`, `source`, `timezone`, `catalog`, `schema`, **`resourceGroupId`**, `sessionProperties`, `resourceEstimates`, `serverAddress`, `serverVersion`, `environment`, `queryType`, `retryPolicy`
- `QueryStatistics`: `cpuTime`, `failedCpuTime`, `wallTime`, `queuedTime`, `scheduledTime`, `analysisTime`, `planningTime`, `planningCpuTime`, `executionTime`, `physicalInputBytes`, `physicalInputRows`, `processedInputBytes`, `processedInputRows`, `internalNetworkBytes`, `outputBytes`, `outputRows`, `writtenBytes`, `writtenRows`, `spilledBytes`, **`peakUserMemoryBytes`**, `peakTaskUserMemory`, `peakTaskTotalMemory`, `cumulativeMemory`, `completedSplits`, `complete`, `stageGcStatistics`, `taskStatistics` 외
- `QueryFailureInfo`: `errorCode`, `failureType`, `failureMessage`, `failureTask`, `failureHost`, `failuresJson`
- `QueryIOMetadata`: `inputs`(`List<QueryInputMetadata>`), `output`(`Optional<QueryOutputMetadata>`)

> **FR-QH-05(리소스 소비 표시)에 필요한 필드가 전부 존재한다.** `resourceGroupId`가 `QueryContext`에 있으므로 **FR-WL-05(리소스 그룹 ↔ 쿼리 히스토리 조인)도 EventListener 데이터만으로 성립한다.**
>
> **`QueryStatistics.taskStatistics` / `operatorSummariesProvider` / `QueryMetadata.plan`·`jsonPlan`은 쿼리당 수 MB에 달할 수 있다.** 히스토리 저장소에 통째로 넣으면 용량이 폭증한다. 저장 필드 화이트리스트를 명시적으로 정의할 것.

**NFR-ISOLATION 관련 — 확인**: `queryCompleted`는 코디네이터 프로세스 내에서 호출된다. SPI에 비동기 실행·버퍼링·백프레셔 기능은 **없다.** 전적으로 구현체 책임이다. (CLAUDE.md 절대 규칙 2 그대로 유지)

---

### T1-2. Graceful shutdown — **확인**

문서: `/docs/477/admin/graceful-shutdown.html` + 소스 `io/trino/server/ServerInfoResource.java` @477

| 항목 | 값 |
|---|---|
| 경로 | `PUT /v1/info/state` (워커 HTTP 포트) |
| 페이로드 | `"SHUTTING_DOWN"` — **JSON 문자열이므로 큰따옴표 포함** |
| 헤더 | `Content-Type: application/json`, `X-Trino-User: <권한 있는 사용자>` |
| 보안 등급 | `@ResourceSecurity(MANAGEMENT_WRITE)` — **확인(소스)** |
| 필요 권한 | 해당 사용자가 **"system information" 쓰기** 권한 보유 |
| 유예시간 property | `shutdown.grace-period` (기본 `2m`) |

**동일 클래스의 조회용 엔드포인트** — **확인(소스)**

| 경로 | 메서드 | 보안 |
|---|---|---|
| `/v1/info` | GET | `PUBLIC` |
| `/v1/info/state` | GET | `PUBLIC` |
| `/v1/info/coordinator` | GET | `PUBLIC` |

**종료 시퀀스** — **확인** (문서 명시)

1. `SHUTTING_DOWN` 상태 진입
2. `shutdown.grace-period` 만큼 대기 → 코디네이터가 인지하고 신규 task 전송 중단
3. 활성 task 전부 완료까지 블록
4. **grace-period 만큼 한 번 더 대기**
5. 프로세스 종료

> **워커 1대 축소에 최소 `2 × shutdown.grace-period` + 실행 중 task 완료 시간이 소요된다.** FR-FL-03의 타임아웃 기본값은 이 사실을 반영해야 한다 (기본 grace-period 기준 최소 4분).

**접근제어 제약** — **확인** (문서 명시)
- `default` built-in system access control은 graceful shutdown을 **허용하지 않는다.**
- `allow-all` 또는 `file` system access control + system information rules 필요.
- **"이 설정은 모든 워커에 존재해야 한다."** (문서 원문)
- OPA 사용 시의 동등 처리는 §3 T3-4 참조.

---

### T1-3. 런타임 로그 레벨 변경 — **부분 지원. 확인(소스)**

> **BOLT_0.md T1-3 특별 지침에 대한 답: "OSS에 동등 API가 없다"는 가정은 틀렸다. 단, "REST API"는 없다.**

**확인된 사실**

1. Trino 477 `io/trino/server/Server.java` 의 모듈 목록에 **`new LogJmxModule()`이 무조건 포함**된다 (조건부 아님, 코디네이터·워커 공통). — **확인(소스)**
2. airlift 361 `LogJmxModule`은 `LoggingMBean`을 **`io.airlift.log:name=Logging`** 이름으로 JMX export 한다. — **확인(소스)**
3. `LoggingMBean`의 `@Managed` 메서드 — **확인(소스, airlift 361)**

   | 메서드 | 용도 |
   |---|---|
   | `String getLevel(String loggerName)` | 개별 로거 레벨 조회 |
   | `void setLevel(String loggerName, String newLevel)` | **개별 로거 레벨 런타임 변경** |
   | `String getRootLevel()` | 루트 레벨 조회 |
   | `void setRootLevel(String newLevel)` | 루트 레벨 런타임 변경 |
   | `Map<String,String> getAllLevels()` | 전체 로거 레벨 조회 |

   `newLevel`은 `Level.valueOf(newLevel.toUpperCase(Locale.US))` 로 파싱된다 → 허용값은 `DEBUG` / `INFO` / `WARN` / `ERROR`.

4. **HTTP로는 쓰기가 불가능하다.** Trino 477은 airlift `MBeanResource`를 `/v1/jmx` 로 노출하지만, 이 리소스의 JAX-RS 메서드는 **`@GET` 4개뿐이다** (`@POST`/`@PUT` 없음). — **확인(소스, airlift 361 `jmx-http/.../MBeanResource.java`)**

**결론 요약**

| 질문 | 답 |
|---|---|
| OSS Trino 477에서 재시작 없이 로그 레벨을 바꿀 수 있는가? | **가능하다.** JMX MBean `io.airlift.log:name=Logging` 의 `setLevel` / `setRootLevel` 오퍼레이션 |
| REST API로 가능한가? | **불가능하다.** `/v1/jmx`는 읽기 전용 |
| 공식 문서에 있는가? | **없다.** `/docs/477/admin/logging.html`에는 `log.properties`만 기술되어 있고 런타임 변경 언급이 전혀 없다 |
| 변경이 재시작 후에도 유지되는가? | **유지되지 않는다.** MBean은 JVM 인메모리 상태만 바꾼다. 재시작 시 `log.properties` 값으로 복귀한다 |
| 전 노드 일괄 적용이 되는가? | **되지 않는다.** 노드별로 개별 호출해야 한다 (코디네이터 1 + 워커 12 = 13회/클러스터) |

**FR-LOGLEVEL에 대한 함의 (판정은 `BOLT_0_RESULT.md` §2 참조)**

- 호출에는 **JMX/RMI 연결이 필요**하다 → 전 노드에 `jmx.rmiregistry.port` / `jmx.rmiserver.port` + `jvm.config`의 `-Dcom.sun.management.jmxremote.rmi.port` 설정이 있어야 한다 (§T1-7).
- **TMS 백엔드는 Python(FastAPI)이다. Python은 JMX/RMI를 네이티브로 말하지 못한다.** 전 노드에 Jolokia 같은 JVM 에이전트를 붙이거나 JVM 헬퍼 프로세스를 두어야 한다 → **신규 인프라 의존성**.
- SEP의 "재시작 후에도 유지" 특성은 **재현 불가**하다. 영속화하려면 Ansible로 `log.properties`를 함께 갱신해야 하며, 그것은 별개의 작업이다.
- **문서화되지 않은 내부 API**다. airlift 버전업으로 사라지거나 이름이 바뀔 수 있다. CLAUDE.md 절대 규칙 1의 취지상 이 사실을 UI/설계 문서에 명시해야 한다.

---

### T1-4. 리소스 그룹 상태 조회 — **확인**

**방법 A — REST (권장, 1차 소스)** — **확인(소스)** `io/trino/server/ResourceGroupStateInfoResource.java` @477

| 항목 | 값 |
|---|---|
| 경로 | `GET /v1/resourceGroupState/{resourceGroupId}` (경로 정규식 `{resourceGroupId: .+}` → `global.pipeline.job` 같은 점 포함 ID 그대로 사용 가능) |
| 응답 | `ResourceGroupInfo` (JSON) |
| 보안 | `@ResourceSecurity(MANAGEMENT_READ)` |
| 부가 | `@Encoded` — 경로 세그먼트를 URL 디코딩하지 않는다 |

**방법 B — JMX** — **부분 확인**

- 리소스 그룹 JSON에 `"jmxExport": true` 를 설정하면 해당 그룹이 JMX로 export 된다. — **확인** (`/docs/477/admin/resource-groups.html`)
- 소스상 export 호출은 `exporter.exportWithGeneratedName(group, InternalResourceGroup.class, group.getId().toString())` 이며, Trino는 `PrefixObjectNameGeneratorModule("io.trino")`를 사용한다. — **확인(소스)**
- **최종 ObjectName 문자열은 확인 불가.** 생성 규칙을 소스로 역산하는 것은 추측이 된다. **실환경에서 `GET /v1/jmx/mbean` 목록을 받아 확정할 것** (아래 T1-7).

**리소스 그룹 설정 property** — **확인**

| property | 값 |
|---|---|
| `resource-groups.configuration-manager` | `file` \| `db` |
| `resource-groups.config-file` | file 매니저용 경로 |
| `resource-groups.config-db-url` / `-user` / `-password` | db 매니저용 |
| `resource-groups.refresh-interval` | 기본 `1s` |
| `resource-groups.max-refresh-interval` | 기본 `1h` |
| `resource-groups.exact-match-selector-enabled` | 기본 `false` |

> **FR-WORKLOAD 관련 중대 변경**: Trino Gateway 19에서 **Gateway의 리소스 그룹 관리 기능이 전부 제거**되었다 (§2 T2-3). 리소스 그룹은 **Trino 쪽에서만** 관리·조회한다.

---

### T1-5. 실행 중 쿼리 조회 및 kill — **확인**

**REST** — **확인(소스)** `io/trino/server/QueryResource.java` @477

| 메서드 | 경로 | 용도 | 접근제어 |
|---|---|---|---|
| GET | `/v1/query?state=<...>` | 전체 쿼리 목록 (`List<BasicQueryInfo>`), `state` 복수 필터 가능 | 응답을 `filterQueries()`로 필터링 |
| GET | `/v1/query/{queryId}?pruned=false` | 쿼리 상세 | `checkCanViewQueryOwnedBy` |
| DELETE | `/v1/query/{queryId}` | **쿼리 취소** | `checkCanKillQueryOwnedBy` |
| PUT | `/v1/query/{queryId}/killed` | **쿼리 kill** (본문 = 실패 메시지) | `checkCanKillQueryOwnedBy` |
| PUT | `/v1/query/{queryId}/preempted` | 쿼리 preempt (본문 = 실패 메시지) | `checkCanKillQueryOwnedBy` |

> **FR-QL-04(kill + reason 필수)와 궁합이 좋다.** `PUT /v1/query/{queryId}/killed`는 본문에 메시지를 받으므로, **TMS가 입력받은 `reason`을 그대로 전달**하면 Trino가 사용자에게 반환하는 실패 메시지에 사유가 남는다. `DELETE`(취소)에는 메시지 슬롯이 없으므로 **FR-QL-04는 `PUT .../killed`를 사용한다.**

**SQL** — **확인**

- `system.runtime.queries` 컬럼 (소스 `QuerySystemTable.java` @477): `query_id`, `state`, `user`, `source`, `query`, `resource_group_id`(`array(varchar)`), `queued_time_ms`, `analysis_time_ms`, `planning_time_ms`, `created`, `started`, `last_heartbeat`, `end`, `error_type`, `error_code`
- `system.runtime.nodes` 컬럼 (소스 `NodeSystemTable.java` @477): `node_id`, `http_uri`, `node_version`, `coordinator`, `state`
- 프로시저: `CALL system.runtime.kill_query(query_id => '...', message => '...')` — **확인**
- 기타 `system.runtime` 테이블: `tasks`, `transactions`, `optimizer_rule_stats` — **확인**

> `system.runtime.queries`에는 CPU time·메모리 사용량 컬럼이 **없다.** 실행 중 쿼리의 리소스 소비가 필요하면 `/v1/query`의 `BasicQueryInfo`(→ `BasicQueryStats`)를 써야 한다.

**`state` 파라미터 유효값 — 확인(소스)** `io/trino/execution/QueryState.java` @477

`QUEUED`, `WAITING_FOR_RESOURCES`, `DISPATCHING`, `PLANNING`, `STARTING`, `RUNNING`, `FINISHING`, `FINISHED`, `FAILED` (9종)
종료 상태(`isDone() == true`)는 `FINISHED`, `FAILED` 둘뿐이다. `@QueryParam("state") Set<String>` 이므로 `?state=A&state=B` 형태로 복수 지정한다.

**`BasicQueryInfo` 필드 — 확인(소스)** `io/trino/server/BasicQueryInfo.java` @477

`queryId`, `session`, **`resourceGroupId`**, `state`, `scheduled`, `self`, `query`, `updateType`, `preparedQuery`, `queryStats`, `errorType`, `errorCode`, `queryType`, `retryPolicy`

**`BasicQueryStats` 필드 — 확인(소스)** `io/trino/server/BasicQueryStats.java` @477

`createTime`, `endTime`, `queuedTime`, **`elapsedTime`**, `executionTime`, `planningTime`, `analysisTime`, `finishingTime`, `physicalInputReadTime`, **`totalCpuTime`**, `failedCpuTime`, `totalScheduledTime`, `failedScheduledTime`, `failedTasks`, `totalDrivers`/`queuedDrivers`/`runningDrivers`/`completedDrivers`/`blockedDrivers`, `processedInputPositions`, `physicalInputDataSize`, `physicalWrittenDataSize`, `internalNetworkInputDataSize`, `spilledDataSize`, `cumulativeUserMemory`, `failedCumulativeUserMemory`, `userMemoryReservation`, `totalMemoryReservation`, **`peakUserMemoryReservation`**, `peakTotalMemoryReservation`, `fullyBlocked`, `blockedReasons`, **`progressPercentage`**, `runningPercentage`

> **FR-QUERY-LIVE(FR-QL-01/02/03)에 필요한 필드가 전부 존재한다.** 사용자·경과시간·상태·리소스그룹·진행률·CPU·피크메모리 모두 `/v1/query` 한 번으로 얻는다.
> **⚠️ `query` 는 SQL 전문이다.** 동시 실행 쿼리가 많으면 응답이 수 MB가 될 수 있다 — 목록 스냅샷 저장 시 절단 정책 필요 (`ARCHITECTURE.md` §3-1).

**⭐ 실제 응답 형식 — 로컬 Trino 477 실증 (2026-08-06)**

단일 노드 Trino 477(HTTPS + PASSWORD 인증 + `file` 접근제어)을 띄워 실행 중 쿼리의 `/v1/query` 응답을 그대로 확인했다.

| 필드 | 실제 값 | 타입 |
|---|---|---|
| `elapsedTime` | `'10.93s'` | **문자열** |
| `queuedTime` | `'537.42us'` | **문자열** (마이크로초 단위 실사용 확인) |
| `totalCpuTime` | `'47.99s'` | 문자열 |
| `physicalInputDataSize` | `'0B'` | **문자열, 바이트 + `B`** |
| `peakUserMemoryReservation` | `'10488440B'` | **문자열, 바이트 + `B`** — `'10MB'` 가 **아니다** |
| `progressPercentage` | `0.0` | 숫자 |
| `runningDrivers` | `8` | 정수 |
| `fullyBlocked` | `False` | 불리언 |
| `createTime` | `'2026-08-06T12:22:50.202230Z'` | ISO 문자열 |
| `resourceGroupId` | `['global']` | 배열 |
| `session.user` / `session.source` | `'analyst'` / `'superset'` | 문자열 |

최상위 키 (실측): `query`, `queryId`, `queryStats`, `queryType`, `resourceGroupId`, `retryPolicy`, `scheduled`, `self`, `session`, `state`

> **`DataSize` 가 사람이 읽는 단위가 아니라 항상 바이트 문자열이라는 소스 판독이 실물로 확인됐다.** 숫자로 가정했다면 모든 메모리·바이트 필드가 `None` 이 되어 화면이 비었을 것이다.
> **응답 크기 실측**: 쿼리 1건에 **2,222 bytes** (SQL 48자 기준). 운영 환경 실측치 3,493 bytes 와 같은 자릿수다.
>
> **FR-FL-02(워커 등록/조인 여부)**: `system.runtime.nodes`가 1차 소스라는 REQUIREMENTS.md의 설계 판단은 유효하다. 단 컬럼이 5개뿐이므로, 인벤토리의 나머지 필드는 Ansible inventory와 `/v1/info` 조합으로 채워야 한다.

---

### T1-6. `catalog.management` / `catalog.store` / ALTER CATALOG — **확인**

문서: `/docs/477/admin/properties-catalog.html`, `/docs/477/sql/create-catalog.html`, `/docs/477/sql/drop-catalog.html`

| property | 허용값 | 기본값 | 비고 |
|---|---|---|---|
| `catalog.management` | `static`, `dynamic` | `static` | `dynamic` 시 CREATE/DROP CATALOG 사용 가능. **신규 워커는 코디네이터에서 현재 카탈로그 설정을 수신** |
| `catalog.store` | `file`, `memory` | `file` | `dynamic` 필요. `file`은 **코디네이터의 카탈로그 디렉토리에 Trino 프로세스 쓰기 권한 필요**, `memory`는 기동 시 기존 파일 무시 |
| `catalog.prune.update-interval` | duration | `5s` (최소 `1s`) | `dynamic` 필요 |
| `catalog.config-dir` | string | `etc/catalog/` | |
| `catalog.disabled-catalogs` | 콤마 구분 문자열 | — | 기동 시 무시할 카탈로그 |
| `catalog.read-only` | string | `false` | `catalog.store=file` 필요. true면 DROP으로 기존 파일 삭제 불가, 동일 이름 신규 파일 작성 불가 |

**ALTER CATALOG — 존재하지 않는다. 확인**

Trino 477 문서 소스 트리(`docs/src/main/sphinx/sql/`)에 카탈로그 관련 문서는 **`create-catalog.md`, `drop-catalog.md`, `show-catalogs.md` 뿐이며 `alter-catalog.md`는 없다.**
→ **카탈로그 "변경"은 DROP + CREATE 로만 가능하다.**

**공식 문서에 명시된 경고 (UI에 그대로 노출할 것)** — **확인**

1. `catalog.management` 항목: *"This feature is experimental only. Because of the security implications the syntax might change and be backward incompatible."*
2. `DROP CATALOG` 및 `catalog.management` 항목: *"Some connectors are known not to release all resources when dropping a catalog… HDFS, S3, GCS, or Azure를 읽는 모든 커넥터, 즉 **Hive, Iceberg, Delta Lake, Hudi***"
3. `CREATE CATALOG` 항목: *"The complete `CREATE CATALOG` query is logged, and visible in the Web UI. This includes any sensitive properties, like passwords and other credentials."*
4. `DROP CATALOG`: 실행 중 쿼리는 중단시키지 않으나 신규 쿼리에는 사용 불가.

> **자격증명 처리 방법 — 확인**: `CREATE CATALOG` 는 `'${ENV:POSTGRES_USER}'` 형태의 환경변수 참조를 지원하며, 해당 환경변수는 **클러스터 모든 노드에 secret으로 설정**되어 있어야 한다. 참조하는 환경변수가 코디네이터에 없으면 쿼리가 실패한다. → **FR-CT-04(평문 전달 금지)는 이 메커니즘으로 구현 가능하다.**

---

### T1-7. JMX 노출 방식 및 주요 MBean — **확인**

**JMX/RMI 활성화** (`config.properties`) — **확인**

```
jmx.rmiregistry.port=9080
jmx.rmiserver.port=9081
```

추가로 `jvm.config` 에 `-Dcom.sun.management.jmxremote.rmi.port=9081`.

**HTTP를 통한 JMX 읽기 — 확인(소스). TMS 설계상 가장 중요한 발견 중 하나**

Trino 477 `ServerSecurityModule`은 airlift `MBeanResource`를 `MANAGEMENT_READ` 리소스로 바인딩한다.

| 메서드 | 경로 | 반환 |
|---|---|---|
| GET | `/v1/jmx/mbean` | 전체 MBean 목록 |
| GET | `/v1/jmx/mbean/{objectName}` | 해당 MBean 전체 속성 |
| GET | `/v1/jmx/mbean/{objectName}/{attributeName}` | 단일 속성 |

> **TMS(Python)는 RMI 없이 순수 HTTP로 모든 JMX 지표를 읽을 수 있다.** FR-CLUSTER-HEALTH, FR-WORKLOAD, FR-QUERY-LIVE의 지표 수집 경로가 여기서 확정된다.
> **단 쓰기는 불가**하다 (`@GET`만 존재) → T1-3의 `setLevel`은 이 경로로 호출할 수 없다.
> `MBeanResource`는 airlift 소속 클래스이며 Trino 공식 문서에 기술되어 있지 않다. **문서화되지 않은 경로임을 인지하고 사용할 것.**

**공식 문서에 명시된 MBean 이름** — **확인** (`/docs/477/admin/jmx.html`)

> ## ⛔ 정정 (2026-08-06) — **이 문서 페이지의 MBean 이름 하나는 477에서 존재하지 않는다**
>
> 실환경 V1 검증에서 `trino.failuredetector:name=HeartbeatFailureDetector` 가 **HTTP 500** 을 반환했다.
> 원인 (소스 확인):
> - `FailureDetectorModule` 은 Trino 477의 `Server.java` / `CoordinatorModule` / `ServerMainModule` **어디에도 설치되지 않는다.** `WorkerModule` 만 `FailureDetector` 를 참조하며 `NoOpFailureDetector` 를 바인딩한다 (JMX export 없음).
> - 코디네이터의 노드 관리는 **`io.trino.node` 패키지**로 대체됐다. `NodeManagerModule` 이 `newExporter(binder).export(CoordinatorNodeManager.class).withGeneratedName()` 로 export 한다.
> - airlift `MBeanResource.getMBean` 은 `throws JMException` 만 선언하고 예외를 매핑하지 않는다 → **존재하지 않는 ObjectName은 404가 아니라 500**이다.
>
> **→ `/docs/477/admin/jmx.html` 은 이 항목에서 코드보다 뒤처져 있다.**
> **→ 교훈: MBean 이름은 문서로 확정하지 않는다. `GET /v1/jmx/mbean` 열거 또는 소스로 확인한다.** 나머지 MBean(`java.lang:type=Memory`, `trino.execution:name=QueryManager`, `trino.memory:name=ClusterMemoryManager`)은 **실환경에서 200 확인됨**.

| 용도 | ObjectName:Attribute |
|---|---|
| 힙 사용량 | `java.lang:type=Memory:HeapMemoryUsage.used` ✅ 실환경 확인 |
| 스레드 수 | `java.lang:type=Threading:ThreadCount` |
| ~~활성 노드 수~~ | ~~`trino.failuredetector:name=HeartbeatFailureDetector:ActiveCount`~~ **477에 존재하지 않음 (500)** → 아래 §T1-7-1 |
| 여유 분산 메모리 | `trino.memory:type=ClusterMemoryPool:name=general:FreeDistributedBytes` |
| OOM kill 누적 | `trino.memory:name=ClusterMemoryManager:QueriesKilledDueToOutOfMemory` |
| 실행/대기 쿼리 수 | `trino.execution:name=QueryManager:RunningQueries` |
| 시작 쿼리 수 | `trino.execution:name=QueryManager:StartedQueries.FiveMinute.Count` |
| 실패 쿼리(전체) | `trino.execution:name=QueryManager:FailedQueries.FiveMinute.Count` |
| 실패 쿼리(내부) | `trino.execution:name=QueryManager:InternalFailures.FiveMinute.Count` |
| 실패 쿼리(외부) | `trino.execution:name=QueryManager:ExternalFailures.FiveMinute.Count` |
| 실패 쿼리(사용자) | `trino.execution:name=QueryManager:UserErrorFailures.FiveMinute.Count` |
| 실행 지연 P50 | `trino.execution:name=QueryManager:ExecutionTime.FiveMinutes.P50` |
| 입력 데이터율 P90 | `trino.execution:name=QueryManager:WallInputBytesRate.FiveMinutes.P90` |
| task 입력 바이트 | `trino.execution:name=SqlTaskManager:InputDataSize.FiveMinute.Count` |
| task 입력 행 | `trino.execution:name=SqlTaskManager:InputPositions.FiveMinute.Count` |
| 커넥터별 | `trino.plugin*` 접두사 |

> 문서 원문: *"A small subset of the available metrics are described below."* — **이 목록은 전체가 아니다.** 실제 사용 가능한 MBean 전량은 `GET /v1/jmx/mbean` 으로 실환경에서 열거할 것.

#### T1-7-1. 노드 수 조회의 477 정답 — `CoordinatorNodeManager` — **확인(소스). 실환경 값 확인 대기**

`io/trino/node/NodeManagerModule.java` @477 (코디네이터 분기):

```java
binder.bind(CoordinatorNodeManager.class).in(Scopes.SINGLETON);
binder.bind(InternalNodeManager.class).to(CoordinatorNodeManager.class).in(Scopes.SINGLETON);
newExporter(binder).export(CoordinatorNodeManager.class).withGeneratedName();
```

`io/trino/node/CoordinatorNodeManager.java` @477 의 `@Managed` 속성:

| 속성 | 의미 |
|---|---|
| `ActiveNodeCount` | 활성 노드 수 |
| `InactiveNodeCount` | 비활성(도달 불가) 노드 수 |
| `DrainingNodeCount` | drain 진행 중 |
| `DrainedNodeCount` | drain 완료 |
| `ShuttingDownNodeCount` | 종료 중 |

**ObjectName**: **`trino.node:name=CoordinatorNodeManager`** — ✅ **실환경 200 확인 (2026-08-06)**

**실측값 (2026-08-06, 워커 12대 클러스터)**

| 항목 | 값 | 의미 |
|---|---|---|
| `ActiveNodeCount` | **13** | `expected_workers(12) + 1` → **코디네이터가 포함된다** ✅ 확정 |

> **H-03 판정식 확정**: `active_workers = ActiveNodeCount - 1`
> 코디네이터는 우리가 그 코디네이터에 질의해 응답을 받은 이상 항상 활성이다. 따라서 상수 1을 빼는 것이 안전하다.
> 이 사실을 코드에 매직넘버로 넣지 않고 `config.yaml` 의 `coordinator_counted_in_active_nodes: true`(검증된 기본값)로 둔다 — 버전업으로 바뀌면 설정 한 줄로 대응한다.

**⭐ 다섯 집합은 서로 배타적이다 — 확인(소스)**

`CoordinatorNodeManager` 는 각 노드의 상태에 대해 `switch` 로 분류하므로 **노드 하나는 정확히 한 집합에만 들어간다.**

```java
switch (remoteNodeState.getState()) {
    case ACTIVE -> activeNodesBuilder.add(node);
    case INACTIVE -> inactiveNodesBuilder.add(node);
    case DRAINING -> drainingNodesBuilder.add(node);
    case DRAINED -> drainedNodesBuilder.add(node);
    case SHUTTING_DOWN -> shuttingDownNodesBuilder.add(node);
    case INVALID -> invalidNodesBuilder.add(node);   // ← @Managed 카운터 없음
    case GONE -> goneNodesBuilder.add(node);         // ← @Managed 카운터 없음
}
```

> **H-03 판정식이 이 사실 위에 서 있다.** 집합이 겹치면 `expected - active - planned` 가 음수가 되거나 중복 차감된다.
> **`INVALID` / `GONE` 노드는 5개 카운터 어디에도 나타나지 않는다.** 따라서 `expected - active_workers - planned` 의 잔여분이 곧 **"완전히 사라진 노드"** 이며, 이것이 정확히 H-03이 잡아야 할 대상이다.

**이것은 손실이 아니라 개선이다.** 구 `ActiveCount` 는 숫자 하나뿐이었으나, 위 5개는 **"몇 대가 빠졌는가"와 "왜 빠졌는가"(장애 vs 계획된 drain)를 구분**한다.
> - **H-03 개선**: drain 중인 노드를 장애로 오판하지 않는다
> - **R3 FR-FLEET 직결**: graceful shutdown 진행 상황(`DrainingNodeCount` → `DrainedNodeCount`)을 폴링으로 추적할 수 있다. FR-FL-03의 drain 완료 확인 수단이 확보됐다

**JMX connector (SQL 조회)** — **확인** (`/docs/477/connector/jmx.html`)

| 항목 | 값 |
|---|---|
| 스키마 | `jmx.current` (전 노드 실시간 MBean), `jmx.history` (스냅샷 + timestamp 컬럼) |
| `jmx.dump-tables` | 주기 샘플링할 MBean 콤마 구분 목록 |
| `jmx.dump-period` | 기본 `10s` |
| `jmx.max-entries` | 기본 `86400` |

MBean 이름은 쿼리에서 큰따옴표로 감싸고, 설정 파일에서는 콤마를 이스케이프한다.

**OpenMetrics `/metrics`** — **확인(소스). G-6 해소 (2026-08-06)**

| 항목 | 값 |
|---|---|
| 경로 | **`GET /metrics`** (airlift `MetricsResource`, `@Path("/metrics")`) |
| 형식 | OpenMetrics (`@Produces(OPENMETRICS_CONTENT_TYPE)`) |
| 필터 | **`?name[]=<지표명>`** 복수 지정 가능 (`@QueryParam("name[]") List<String> filter`) |
| 보안 | **`MANAGEMENT_READ`** — Trino 477 `ServerSecurityModule` 이 `MBeanResource` 와 **동일하게** `managementReadResource(MetricsResource.class)` 로 바인딩 |

> **`/metrics` 와 `/v1/jmx/mbean` 은 권한 요건이 완전히 같다** (둘 다 `checkCanReadSystemInformation`). 어느 쪽을 쓰든 `system_information: read` 가 필요하다.
> **⛔ 검토 결과: 채택하지 않는다 (2026-08-06 실측).** `/metrics` 는 요청 수를 줄이지만 CPU 는 늘린다 — 전체 10.5 CPU ms(1MB), `?name[]=` 로 2개만 필터해도 5.4 CPU ms 로 MBean 4종 개별 조회(5.6 CPU ms, 76KB)보다 이득이 없다. 필터링해도 서버가 **전체 MBean 레지스트리를 순회**해 지표를 만들기 때문이다. 응답 크기만 줄고 CPU 는 그대로다.
> **속성 단위 조회(`/v1/jmx/mbean/{obj}/{attr}`)도 반박됐다** — `QueryManager` 전체 1회 1.52 CPU ms vs 필요한 4개 속성 개별 3.11 CPU ms(+104%). **요청당 고정비용(TLS·인증·접근제어)이 페이로드 비용을 압도한다.** 상세: `docs/PERF_MEASUREMENT.md` §4

---

## 2. Trino Gateway

> **모든 항목의 전제**: 아래는 `trinodb/trino-gateway` `main` 브랜치 문서/소스 기준이다. **운영 중인 Gateway 버전이 확정되지 않았다 (§5 G-1).** 버전이 낮으면 아래 사실 중 일부가 성립하지 않는다.
> 참고 릴리스: **19** (2026-05-11), **20** (2026-06-25)

### T2-1. 기본 라우터 — **확인**

- 기본 라우터는 **`StochasticRoutingManager`** 이며, 소스상 동작은 문자 그대로 **`RANDOM.nextInt() % backends.size()`** 다. 부하를 전혀 고려하지 않는다. — **확인(소스)** `StochasticRoutingManager.java`
- 문서 원문: *"The default router selects the cluster randomly to route the queries."* — **확인**

> **BOLT_0.md T2-1의 가설이 맞다.** 현재 "느린 클러스터에도 절반이 간다"는 것은 설정 미비의 직접적 결과다.
> **단, 현재 운영 Gateway의 `modules` 설정에 무엇이 들어 있는지는 확인 불가** — 실환경 config 확인 필요 (§5 G-1).

### T2-2. `QueryCountBasedRouterProvider` 활성화 — **확인**

```yaml
backendState:
  username: <username>
  password: <password>
  ssl: <false|true>
  xForwardedProtoHeader: <false|true>

clusterStatsConfiguration:
  monitorType: UI_API      # 통계 수집을 위해 UI_API 또는 JDBC 필요

modules:
  - io.trino.gateway.ha.module.QueryCountBasedRouterProvider
```

- 동작: 클러스터가 보고한 `ClusterStats`(running / queued 쿼리 수)를 기준으로 **사용자별로** 가장 덜 바쁜 백엔드를 고른다. — **확인**
- **통계에는 healthy 클러스터만 포함된다.** — **확인**
- 통계 갱신 주기: `monitor.taskDelay` (기본 1분) — **확인**

**`clusterStatsConfiguration.monitorType` 허용값 — 확인**: `INFO_API`(기본), `METRICS`, `JDBC`, `JMX`, `UI_API`, `NOOP`

> **주의: 기본값 `INFO_API`로는 `QueryCountBasedRouterProvider`가 제 역할을 못 한다.** 문서가 `UI_API` 또는 `JDBC`를 요구한다. S1 적용 시 `monitorType` 변경이 반드시 함께 가야 한다.
> **주의: `taskDelay` 기본 1분은 짧은 쿼리가 많은 워크로드에서 통계가 낡는다.** 워크로드 특성화(WORKLOAD_PROFILE.md) 결과로 조정할 것.

**커스텀 라우터 확장점** — **확인** (BACKLOG 1-2c의 DEFER 판정 근거 재확인)
- Provider 모듈: `RouterBaseModule` 상속 → `modules`에 등록
- 라우터: `StochasticRoutingManager` 상속 → `provideAdhocBackend` / `provideBackendForRoutingGroup` 오버라이드, `updateBackEndStats(List<ClusterStats>)` 로 통계 수신

### T2-3. Gateway REST API — **확인** (문서 `gateway-api.md`)

| 용도 | 메서드 | 경로 |
|---|---|---|
| 클러스터 추가 | POST | `/gateway/backend/modify/add` |
| 클러스터 수정 | POST | `/gateway/backend/modify/update` |
| 클러스터 삭제 | POST | `/gateway/backend/modify/delete` |
| 전체 조회 | GET | `/gateway/backend/all` |
| 활성 조회 | GET | `/gateway/backend/active` |
| **비활성화** | POST | `/gateway/backend/deactivate/{name}` |
| **활성화** | POST | `/gateway/backend/activate/{name}` |
| 라우팅 규칙 갱신 | POST | `/webapp/updateRoutingRules` (ADMIN 역할, `rulesType: FILE` + 쓰기 가능 파일 필요) |
| liveness | GET | `/trino-gateway/livez` |
| readiness | GET | `/trino-gateway/readyz` (DB 최초 연결 + 첫 헬스체크 완료 시 200, 아니면 503) |
| Prometheus 지표 | GET | `/metrics` (OpenMetrics) |

백엔드 페이로드 필드: `name`, `proxyTo`, `active`, `routingGroup`, `externalUrl`

> **FR-CO-02 안전 시퀀스 1단계(routing group 비활성화)와 5단계(재활성화)는 `POST /gateway/backend/deactivate/{name}` / `activate/{name}` 로 구현된다.** 경로가 확정되었다.
> **FR-BM-04(프로덕션 보호)도 동일 API로 구현 가능하다.**

**⚠️ Gateway 19 파괴적 변경 — 확인** (릴리스 노트)

1. *"**Remove all resource group management functionality.** Existing resource group database tables are preserved and not dropped on upgrade… Resource groups are a Trino feature and must be managed through Trino directly."* (#656)
   → **FR-WORKLOAD은 Gateway가 아니라 Trino(§T1-4)를 데이터 소스로 삼아야 한다.**
2. 라우팅 설정 키 `addXForwardedHeaders` → **`forwardedHeadersEnabled`** 로 이름 변경 (#1005)
3. `/webapp/findQueryHistory` 엔드포인트 보안 강화 (#991) — 워크로드 특성화(Task 5)에서 이 API를 쓸 계획이라면 인증 요건을 확인할 것

### T2-4. `databaseCache` — **확인** (문서 `operation.md`)

```yaml
databaseCache:
  enabled: true
  expireAfterWrite: 1h
  refreshAfterWrite: 5s
```

| 키 | 기본값 | 의미 |
|---|---|---|
| `enabled` | **`false`** | 활성화 여부 |
| `expireAfterWrite` | `1h` | 최종 로드/갱신 후 캐시 유지 시간. **만료 후 DB가 죽어 있으면 요청이 실패한다** (fall back할 stale 값이 없음) |
| `refreshAfterWrite` | `5s` | 비동기 갱신 개시 시점. 갱신 중에도 기존 값을 계속 서빙 |

`expireAfterWrite` / `refreshAfterWrite` 를 `null`로 두면 각각 만료/갱신을 끌 수 있다.

**캐시 대상 — 확인**: 문서 원문 *"Currently only the list of backend Trino clusters used for query routing are being cached."*

> **DB 다운 시 실제 동작 (중요)**: 캐시되는 것은 **백엔드 클러스터 목록뿐**이다. 쿼리 히스토리 기록(§T2-7)과 queryId→backend DB 조회는 캐시되지 않는다. 따라서 `databaseCache`는 **"DB가 죽어도 신규 쿼리 라우팅은 계속된다"** 는 보장이지, **"모든 기능이 정상 동작한다"** 는 보장이 아니다. **FR-GW-04의 AC를 이 사실에 맞춰 축소해야 한다.**
> `expireAfterWrite: null` 로 두면 DB 장애 시간이 길어져도 라우팅이 살아남는다. **S8 적용 시 이 값을 검토할 것.**

### T2-5. `analyzeRequest=true` 시 사용 가능한 필드 — **확인** (문서 `routing-rules.md`)

`requestAnalyzerConfig` 설정 키:

| 키 | 기본값 | 의미 |
|---|---|---|
| `analyzeRequest` | `false` | `true`여야 `trinoQueryProperties` / `trinoRequestUser` 사용 가능 |
| `maxBodySize` | `1000000` (문자) | **쿼리마다 이 길이의 버퍼가 할당된다.** GC 과다 시 낮출 것. 본문이 이 값 이상이면 Gateway가 쿼리를 분석하지 않는다. 최대 `2^31-1` |
| `isClientsUseV2Format` | — | 상용 Trino 확장의 V2 요청 구조용. **우리는 OSS이므로 해당 없음** |
| `tokenUserField` | `email` | 사용자명으로 쓸 JWT 클레임 |
| `oauthTokenInfoUrl` | — | 토큰 교환 URL. 응답은 10분 캐시 |

**`trinoRequestUser`** — `getUser()` (실패 시 빈 `Optional`), `getUserInfo()` (`Optional<UserInfo>`, OIDC), `userExistsAndEquals("name")`.
사용자 추출 순서: `X-Trino-User` 헤더 → Basic 인증 → Bearer 토큰 → 쿠키.

**`trinoQueryProperties`** — `errorMessage()`, `isNewQuerySubmission()`(`v1/statement` POST 여부), `getQueryType()`(예: `ShowCreate`), `getResourceGroupQueryType()`(예: `SELECT`, `DATA_DEFINITION`), `getDefaultCatalog()`, `getDefaultSchema()`, `getCatalogs()`, `getSchemas()`, `getCatalogSchemas()`, `tablesContains(String)`, `getTables()`(`Set<QualifiedName>`, 완전 수식), `getBody()`.

> `getTables()` 는 부분 수식 테이블 참조를 기본 카탈로그/스키마로 완전 수식화해 준다 → **카탈로그/스키마 기반 라우팅(S3)에 그대로 쓸 수 있다.**

### T2-6. External Routing Service 연동 규격 — **확인** (문서 `routing-rules.md`)

**활성화**

```yaml
routingRules:
  rulesEngineEnabled: true
  rulesType: EXTERNAL      # FILE | EXTERNAL
rulesExternalConfiguration:
  urlPath: <외부 서비스 URL>
  excludeHeaders: [...]
```

(FILE 모드 키: `rulesConfigPath`, `rulesRefreshPeriod` — 기본 1분마다 재읽기)

**요청**: Gateway → 서비스로 **POST**. 본문에 `excludeHeaders`에 없는 모든 헤더 + `remoteUser`, `method`, `requestURI`, `queryString`, `session`, `remoteAddr`, `remoteHost`, `parameterMap`, 그리고 분석 활성 시 `trinoRequestUser` / `trinoQueryProperties`.

**응답**: HTTP 200 + JSON

```json
{
  "routingGroup": "group-name",
  "errors": ["..."],
  "externalHeaders": { "header-name": "value" }
}
```

- `routingGroup`은 **하나만** 반환 가능.
- **`errors`가 null이 아니면 Gateway는 기본 그룹으로 라우팅한다.** — **확인**
- `externalHeaders`는 Trino 전달 전 요청 헤더를 수정한다.

> **FR-RS-04(장애 시 폴백)의 근거가 확정되었다.** 서비스가 200 + `errors` 채워 응답하면 Gateway가 알아서 `defaultRoutingGroup`으로 보낸다. **다만 서비스가 응답하지 않을 때(타임아웃/커넥션 거부)의 Gateway 동작은 확인 불가** — 실환경 검증 필요 (§5 G-3). NFR-ISOLATION의 핵심 리스크이므로 R2 착수 전 반드시 확인할 것.

### T2-7. 세션 어피니티 / LB 교체 — **확인(소스). 결론: 어피니티 자체가 불필요할 수 있다**

**Gateway 내부 sticky 라우팅** — **확인** (문서 `routing-logic.md`)

1. **queryId 기반 (기본, 항상 켜짐)**: 응답에서 queryId를 추출해 백엔드에 매핑. 이후 `v1/statement/executing/{queryid}/{nonce}/{counter}` 같은 URI에서 queryId를 파싱해 같은 클러스터로 보낸다.
2. **쿠키 기반 (OAuth2 전용)**: `gatewayCookieConfiguration.enabled: true` + `cookieSigningSecret`. `/oauth2` 로 시작하는 경로에만 쿠키를 붙인다. **`v1/*` 등 Trino 엔드포인트에는 쿠키를 붙이지 않는다.**
   - 문서 원문: *"If you load balance request across multiple Trino Gateway instances, ensure each instance has the same `cookieSigningSecret`."*
   - 관련 키: `oauth2GatewayCookieConfiguration.routingPaths` / `.deletePaths` / `.lifetime`(기본 10분)

**Gateway 2대 앞단 LB에 세션 어피니티가 필요한가 — 확인(소스)**

`BaseRoutingManager`의 queryId→backend 매핑은 **로컬 Guava `LoadingCache`** 이며, **캐시 미스 시 `queryHistoryManager.getBackendForQueryId(queryId)` 로 공유 DB를 조회**하고, 그래도 없으면 `searchAllBackendForQuery(queryId)` 로 전 백엔드를 뒤진다.
(`HaQueryHistoryManager.getBackendForQueryId` → `dao.findBackendUrlByQueryId`)

→ **Gateway A가 시작한 쿼리의 후속 폴링이 Gateway B로 가도, B는 공유 PostgreSQL에서 백엔드를 찾아낸다. 따라서 Trino 클라이언트 프로토콜을 위한 LB 세션 어피니티는 원리상 필요 없다.**

**단, 전제 조건이 있다 — 확인(소스)**

| 전제 | 근거 |
|---|---|
| `dataStore.queryHistoryEnabled` 가 **true** (기본값) | `HaQueryHistoryManager.submitQueryDetail()` 은 이 값이 false면 **DB에 아무것도 쓰지 않는다** → 크로스 게이트웨이 조회가 전 백엔드 브루트포스로 전락 |
| `dataStore.queryHistoryHoursRetention` 이 최장 실행 쿼리보다 김 | 보존 기간이 지나면 매핑이 사라진다 |
| **PostgreSQL이 살아 있음** | `databaseCache`는 **백엔드 목록만** 캐시한다 (§T2-4). queryId 조회는 캐시되지 않는다 → **DB 다운 + 캐시 미스 = 전 백엔드 브루트포스** |
| OAuth2 사용 시 두 Gateway가 **동일한 `cookieSigningSecret`** 보유 | 문서 명시 |

> **S4 재해석**: "IP HASH → 세션 어피니티" 는 **필수 개선이 아니라 선택지**다. 진짜 문제는 IP HASH가 **소수의 프록시/BI 서버 IP에서 오는 대량 트래픽을 한쪽 Gateway로 몰아버리는 것**이며, 이는 어피니티로도 해결되지 않는다. 위 전제만 충족되면 **어피니티 없는 라운드로빈/least-conn이 더 낫다.**
> 이 판단의 전제는 **S5(PostgreSQL 분리 + HA)** 다. 현재 PostgreSQL이 Gateway VM1에 co-located 되어 있으므로 **VM1이 죽으면 두 Gateway 모두 DB를 잃는다.** S5 → S4 순서를 지킬 것.

---

## 3. OPA

### T3-1. Trino 477 OPA access control 설정 property — **확인 (전체 목록)**

문서: `/docs/477/security/opa-access-control.html`. `etc/access-control.properties`:

| property | 필수 | 설명 |
|---|---|---|
| `access-control.name=opa` | ✅ | 플러그인 선택 |
| `opa.policy.uri` | ✅ | OPA 엔드포인트. 예 `https://opa.example.com/v1/data/trino/allow` |
| `opa.policy.row-filters-uri` | | 행 필터 조회 URI. 미설정 시 행 필터링 없음 |
| `opa.policy.column-masking-uri` | | 컬럼 마스크 조회 URI. 미설정 시 마스킹 없음 |
| `opa.policy.batch-column-masking-uri` | | **컬럼 마스크 배치 조회.** `opa.policy.column-masking-uri` 와 **동시 사용 금지** |
| `opa.policy.batched-uri` | | 배치 가능한 인가 질의의 배치 모드 활성화 |
| `opa.log-requests` | | 기본 `false`. URI·헤더·**본문 전체**를 OPA 전송 전 로깅 |
| `opa.log-responses` | | 기본 `false`. URI·상태코드·헤더·**본문 전체** 로깅 |
| `opa.allow-permission-management-operations` | | 기본 `false` |
| `opa.http-client.*` | | HTTP 클라이언트 설정 (예: `opa.http-client.http-proxy`) |

**위 목록이 477 문서의 전체다.** 여기 없는 `opa.*` property는 존재하지 않는다.

### T3-2. Batch 관련 property 정확한 이름 — **확인**

> **BOLT_0.md T3-2의 우려는 해소되었다.** 477에는 **두 개**의 배치 property가 있으며 이름이 서로 다르다.

| property | 대상 |
|---|---|
| `opa.policy.batched-uri` | 일반 인가 질의 배치 (리소스 목록을 한 요청에) |
| `opa.policy.batch-column-masking-uri` | **컬럼 마스킹 배치** |

**중대 제약 — 확인** (문서 원문)
- `opa.policy.batch-column-masking-uri` 는 `opa.policy.column-masking-uri` 와 **동시에 설정하면 안 된다.**
- 둘 다 설정되면 **`batch-column-masking-uri` 가 `column-masking-uri` 를 덮어쓴다.**
- 배치 정책은 **허용된 항목의 인덱스 리스트**를 반환해야 한다. 배치 요청의 나머지 필드는 비배치 엔드포인트와 동일하다.
- `opa.policy.batched-uri` 미설정 시 Trino는 **리소스마다 개별 요청**을 보낸다. → **컬럼 수가 많은 테이블에서 인가 지연이 선형 증가한다.** 5만 사용자 규모에서 배치 설정은 사실상 필수다.

### T3-3. OPA decision log — **확인 (Trino 측 / OPA 측 구분 필요)**

**Trino 측 (요청/응답 로깅)** — **확인**
- `opa.log-requests` / `opa.log-responses` 를 켜면 **`DEBUG` 레벨**로 로거 **`io.trino.plugin.opa.OpaHttpClient`** 에 기록된다.
- **`log.properties` 에 이 클래스를 명시해야 실제로 로그가 남는다.** — 문서 명시
- 문서 경고: *"enabling these options produces very large amounts of log data."*

> **FR-OPA-04(인가 거부 조회)를 Trino 로그로 구현하면 안 된다.** 전 쿼리의 요청·응답 본문 전체가 DEBUG로 쏟아진다. **OPA 측 decision log를 써야 한다.**
> 참고: 이 항목은 T1-3(런타임 로그레벨)의 대표적 유즈케이스다 — "OPA 디버깅을 위해 한 시간만 DEBUG로".

**OPA 서버 측 (decision log)** — **확인** (openpolicyagent.org 관리 문서)

| 설정 키 | 의미 |
|---|---|
| `decision_logs.console: true` | 원격 서버 없이 **콘솔(stdout)로 결정 로그 출력** → Promtail/Filebeat로 수집 |
| `decision_logs.service` | 원격 HTTP 엔드포인트로 업로드 (gzip JSON 배열 POST, 2xx 기대) |
| `decision_logs.reporting.min_delay_seconds` / `max_delay_seconds` | 업로드 간격 |
| `decision_logs.reporting.max_decisions_per_second` | 레이트 제한 |
| `decision_logs.mask_decision` | 기본 `data.system.log.mask`. JSON Pointer로 민감 필드 마스킹 |
| `decision_logs.drop_decision` | 기본 `/system/log/drop`. 결정 필터링 |

**엔트리 필드**: `decision_id`, `path`, `input`, `result`, `timestamp`(RFC3339), `labels`, `metrics`, `bundles`, `requested_by`

> **권장 수집 방식**: `decision_logs.console: true` → 기존 로그 수집 파이프라인(S7: Loki/OpenSearch)으로 흘린다. TMS는 **딥링크만** 제공한다 (FR-LOG-DEEPLINK 원칙, 비목표 준수).
> **`input` 필드에 SQL과 사용자 식별정보가 그대로 들어간다.** `mask_decision` 정책을 반드시 함께 설계할 것.

**Trino → OPA 요청 구조** — **확인**

최상위 `context` + `action`.
- `context.identity`: `user`, `groups`
- `context.softwareStack.trinoVersion`
- `action.operation` (예: `SelectFromColumns`), `action.resource`, `action.targetResource`, `action.grantee`
- **적용되지 않는 필드는 `action` 객체에서 아예 생략된다** (null이 아니라 부재). Rego 정책 작성 시 주의.

**항상 허용 / 항상 차단되는 오퍼레이션** — **확인**
- `opa.allow-permission-management-operations` 로 제어(기본 false, OPA 호출 없이 즉시 거부): `GrantSchemaPrivilege`, `DenySchemaPrivilege`, `RevokeSchemaPrivilege`, `GrantTablePrivilege`, `DenyTablePrivilege`, `RevokeTablePrivilege`, `CreateRole`, `DropRole`, `GrantRoles`, `RevokeRoles`
- 설정과 무관하게 **항상 허용**: `ShowRoles`, `ShowCurrentRoles`, `ShowRoleGrants`

### T3-4. 워커 노드 대상 인가 (graceful shutdown 권한) — **확인(소스)**

**핵심 질문**: OPA access control로 graceful shutdown을 인가할 수 있는가?
**답: 가능하다.** Trino 477 `plugin/trino-opa/.../OpaAccessControl.java` 는 다음을 구현한다. — **확인(소스)**

| SPI 메서드 | OPA로 전송되는 `action.operation` |
|---|---|
| `checkCanWriteSystemInformation` | **`WriteSystemInformation`** ← **graceful shutdown 인가에 사용됨** |
| `checkCanReadSystemInformation` | `ReadSystemInformation` |
| `checkCanExecuteQuery` | `ExecuteQuery` |
| `checkCanViewQueryOwnedBy` | `ViewQueryOwnedBy` |
| `checkCanKillQueryOwnedBy` | **`KillQueryOwnedBy`** ← **FR-QL-04 쿼리 kill 인가에 사용됨** |
| `filterViewQueryOwnedBy` | `FilterViewQueryOwnedBy` |

**따라서 필요한 조치**

1. **모든 워커에 `etc/access-control.properties`(OPA 설정)를 배포해야 한다.** graceful shutdown 문서 원문: *"These configuration must be present on all workers."*
2. Rego 정책에 **TMS 서비스 계정에 대한 `WriteSystemInformation` 허용 규칙**을 추가한다 (플랫폼팀 Git 관리).
3. FR-QL-04(쿼리 kill)를 위해 **코디네이터 정책에 `KillQueryOwnedBy` 허용 규칙**을 추가한다.
4. `/v1/jmx/mbean`(§T1-7), `/v1/resourceGroupState/...`(§T1-4), `/v1/query`(§T1-5) 는 모두 `MANAGEMENT_READ` 이므로 **`ReadSystemInformation` 허용도 필요**하다.

> **⚠️ 신규 실패 모드**: 워커가 OPA에 의존하게 된다. **워커의 OPA가 죽으면 graceful shutdown이 거부된다.** 워커 OPA 사이드카 헬스를 FR-OPA-01의 감시 대상에 포함할 것.
> **⚠️ 문서 갭**: `/docs/477/admin/graceful-shutdown.html` 은 `allow-all` 과 `file` 만 언급하고 **OPA를 언급하지 않는다.** 위 결론은 소스(플러그인이 SPI 메서드를 구현함)에 근거한 것이므로 **실환경 검증이 필요하다** (§5 G-4).

---

### T3-5. 인증(authentication) vs 인가(authorization) — **확인. TMS 설계의 전제**

> **2026-08-06 추가.** "TMS는 데이터를 읽지 않으니 basic auth만으로 충분하지 않은가"라는 질문에 답하기 위해 검증했다.
> **핵심: Trino의 system access control은 하나뿐이며, 데이터 권한과 관리 권한을 함께 관장한다. "데이터 전용 OPA"라는 분리는 Trino에 존재하지 않는다.**

#### (1) 관리 엔드포인트는 반드시 system access control을 거친다 — **확인(소스)**

`io/trino/server/security/ResourceSecurityDynamicFeature.java` @477

```java
case MANAGEMENT_READ:
case MANAGEMENT_WRITE:
    context.register(new ManagementAuthenticationFilter(fixedManagementUser, fixedManagementUserForHttps, authenticationFilter));
    context.register(new ManagementAuthorizationFilter(accessControl, sessionContextFactory, accessType == MANAGEMENT_READ));
```

`ManagementAuthorizationFilter.filter()` 내부:

```java
Identity identity = sessionContextFactory.extractAuthorizedIdentity(authenticatedIdentity(request), request.getHeaders());
if (read) { accessControl.checkCanReadSystemInformation(identity); }
else      { accessControl.checkCanWriteSystemInformation(identity); }
// AccessDeniedException → ForbiddenException("Management only resource")
```

**따라서**: `MANAGEMENT_READ` 리소스(`/v1/jmx/mbean`, `/v1/query`, `/v1/resourceGroupState/…`)는 **인증을 통과해도 `checkCanReadSystemInformation` 인가를 다시 거친다.** `access-control.name=opa` 면 이 호출이 곧 **OPA 질의**다.

#### (2) 접근제어 구현별 동작 — **확인** (`/docs/477/security/built-in-system-access-control.html`)

| `access-control.name` | 동작 (문서 원문 요약) |
|---|---|
| **`default`** (설정 파일 없을 때 기본) | *"All operations are permitted, **except for user impersonation and triggering graceful shutdown**."* |
| `allow-all` | 전부 허용 |
| `read-only` | 읽기만 허용 |
| `file` | 파일 규칙 |
| `opa` | OPA가 판정 |
| `ranger` | Apache Ranger |

**`default` 기준 TMS R1 영향 (매우 중요)**

| TMS 호출 | `default`에서 | 근거 |
|---|---|---|
| `GET /v1/jmx/mbean/…` (`MANAGEMENT_READ`) | ✅ **허용** | impersonation·shutdown 외 전부 허용 |
| `GET /v1/query`, `GET /v1/query/{id}` | ✅ **허용** | 동일 |
| `PUT /v1/query/{id}/killed` | ✅ **허용** | 동일 |
| `GET /v1/info`, `/v1/info/state` | ✅ **인증조차 불필요** | `@ResourceSecurity(PUBLIC)` (§T1-2) |
| `PUT /v1/info/state` (graceful shutdown, R3) | ❌ **거부** | 문서 명시 |
| **X-Trino-User로 최종 사용자 가장** | ❌ **거부** | 문서 명시 |

> **→ 현재 접근제어가 `default`(또는 미설정)라면, TMS R1은 basic auth만으로 전부 동작한다.** OPA 규칙 추가가 필요 없다.
> **→ `access-control.name=opa` 로 전환하는 순간, 위 전부가 Rego 규칙을 요구한다.**

#### (3) impersonation 발동 조건 — **확인(소스)**

`io/trino/server/HttpRequestSessionContextFactory.java` @477

```java
// only check impersonation if authenticated user is not the same as the explicitly set user
if (!authenticatedIdentity.getUser().equals(originalIdentity.getUser())) {
    accessControl.checkCanImpersonateUser(authenticatedIdentity, originalIdentity.getUser());
}
```

- **인증된 사용자 == `X-Trino-User` 이면 impersonation 검사가 아예 일어나지 않는다.**
- 다르면 `checkCanImpersonateUser` 호출 → OPA 플러그인은 `action.operation = "ImpersonateUser"` 를 전송한다 (`OpaAccessControl.java` 확인).
- `checkCanSetUser` 는 **OPA 플러그인에서 빈 구현(no-op)** 이다. 인가에 관여하지 않는다.

> **⚠️ 설계 함의 1 (TMS)**: **TMS는 `X-Trino-User` 를 보내지 않는다.** basic auth 서비스 계정으로만 호출하면 인증 사용자 == 세션 사용자가 되어 impersonation 경로를 타지 않는다.
> **⚠️ 설계 함의 2 (데이터 권한 계획)**: "고정 basic auth 계정 + 사용자별 `X-Trino-User`" 방식은 **정의상 impersonation이다.** `default` 접근제어에서는 **금지되어 동작하지 않는다.** OPA(또는 `file`) 접근제어를 붙이고 Rego에 `ImpersonateUser` 허용 규칙을 넣어야 비로소 동작한다.

#### (3-1) ⚠️ **basic auth 는 HTTPS 에서만 동작한다** — **확인(소스 + 로컬 실증)**

`io/trino/server/security/AuthenticationFilter.java` @477

```java
if (request.getSecurityContext().isSecure()) {
    authenticators = this.authenticators;                      // 설정한 PASSWORD 등
}
else if (insecureAuthenticationOverHttpAllowed) {
    authenticators = ImmutableList.of(insecureAuthenticator);  // ← insecure 만
}
else {
    throw new ForbiddenException("Authentication over HTTP is not enabled");
}
```

**`http-server.authentication.allow-insecure-over-http=true`(기본값)는 "HTTP에서 PASSWORD 인증을 허용"이 아니다.** "HTTP에서는 insecure 인증기만 쓴다"는 뜻이며, `InsecureAuthenticator` 는 **비밀번호가 붙은 basic auth 를 거부**한다:

```
401  Password not allowed for insecure authentication
```

> **TMS 영향**: 프로덕션은 HTTPS(`:8443`)이므로 정상이다. 다만 **HTTP 코디네이터를 상대로 TMS를 붙이면 401 로 실패**하며 메시지가 원인을 짐작하기 어렵다. 로컬 검증 중 실제로 겪었고 TLS 활성화로 해소했다.
> **부가 요구사항**: `http-server.authentication.type=PASSWORD` 설정 시 `internal-communication.shared-secret` 이 **필수**다. 없으면 기동이 실패한다(Guice 오류).

#### (4) `management.user` — 존재하나 해결책이 아니다 — **확인(소스)**

`io/trino/server/security/SecurityConfig.java` @477

| property | 설명 |
|---|---|
| `management.user` | 관리 엔드포인트에 고정 사용자 식별자를 부여 |
| `management.user.https-enabled` | HTTPS 요청에도 적용할지 (기본 false → **HTTPS에서는 기본적으로 무효**) |

**인증만 우회하고 인가는 우회하지 않는다.** `ManagementAuthorizationFilter` 는 무조건 실행되므로 `checkCanReadSystemInformation` 은 그대로 호출된다.
→ **OPA 규칙 회피 수단이 아니다.** 게다가 인증을 건너뛰므로 보안상 권장하지 않는다. **TMS는 사용하지 않는다.**

#### (5) `GET /v1/query` 의 목록 필터링 비용 — **확인(소스)**

`QueryResource.getAllQueryInfo()` 는 응답을 `filterQueries()` 로 거르며, 이는 `AccessControl.filterViewQueryOwnedBy` 를 호출한다.
`OpaAccessControl.filterViewQueryOwnedBy` 는 `opaHighLevelClient.parallelFilterFromOpa(queryOwners, …)` 를 쓴다.

- 호출 수 = **쿼리 수가 아니라 실행 중 쿼리의 distinct 소유자 수** (`Collection<Identity> queryOwners`)
- `opa.policy.batched-uri` 미설정 시 **소유자 1명당 OPA 요청 1건**, 병렬 전송
- `OpaBatchAccessControl`(배치 모드)이면 1건으로 합쳐진다

> **⚠️ TMS 폴링 부하 함의**: TMS가 5초마다 `/v1/query` 를 호출하면, 그때마다 distinct 사용자 수만큼 OPA 질의가 발생한다. 동시 실행 사용자 50명이면 **초당 약 10건의 추가 OPA 부하**가 클러스터당 발생한다.
> → OPA 접근제어를 도입한다면 **`opa.policy.batched-uri` 설정을 사실상 필수로 본다** (§T3-2와 동일 결론).

#### (6) 엔드포인트별 `@ResourceSecurity` 등급 — **확인(소스). 등급이 다르면 규칙도 다르다**

| 엔드포인트 | 등급 | 인가 호출 |
|---|---|---|
| `GET /v1/info`, `/v1/info/state`, `/v1/info/coordinator` | `PUBLIC` | **없음** |
| **`/v1/query` 전체** (목록·상세·`DELETE`·`killed`·`preempted`) | **`AUTHENTICATED_USER`** | 클래스 레벨 `@ResourceSecurity(AUTHENTICATED_USER)`. 개별 메서드가 `filterViewQueryOwnedBy` / `checkCanViewQueryOwnedBy` / `checkCanKillQueryOwnedBy` 호출 |
| `/v1/jmx/mbean/…` | **`MANAGEMENT_READ`** | `checkCanReadSystemInformation` |
| `/v1/resourceGroupState/…` | `MANAGEMENT_READ` | `checkCanReadSystemInformation` |
| `PUT /v1/info/state` | `MANAGEMENT_WRITE` | `checkCanWriteSystemInformation` |

> **`/v1/query` 는 `MANAGEMENT_READ` 가 아니다.** 인증만 되면 리소스에 진입하고, 그 안에서 **쿼리 단위 인가**가 걸린다. 이 차이가 `file` 접근제어에서 결정적 결과를 낳는다(아래).

---

### T3-6. `file` 접근제어(`access-control.name=file`)에서의 TMS 동작 — **확인. 우리 환경의 실제 조건**

> **2026-08-06 확인**: 운영 환경은 `access-control.name=file` + `security.config-file`(`rules.json`) 을 사용한다.
> 출처: `/docs/477/security/file-system-access-control.html`

**설정 형식** — **확인**

```properties
access-control.name=file
security.config-file=etc/rules.json
```

(HTTP 엔드포인트에서 로드하려면 `security.config-file=http://…` + `security.json-pointer=/data`)

#### (1) 규칙 섹션이 **없을 때**의 기본값 — 섹션마다 정반대다

| 규칙 섹션 | 섹션이 없을 때 | 문서 원문 |
|---|---|---|
| **`system_information`** | **전부 거부** | *"If no rules are specified, all access to system information is denied. If no rule matches, system access is denied."* |
| **`queries`** | **전부 허용** | *"If no rules are specified, all users are allowed to execute queries, and to view or kill queries owned by any user."* |
| **`impersonation`** | `principal` 규칙도 없으면 **거부** / `principal` 규칙만 있으면 **허용** | *"If neither impersonation nor principal rules are defined, impersonation is not allowed."* |

> **이 비대칭이 핵심이다.** `queries` 는 관대(기본 허용), `system_information` 은 엄격(기본 거부)이다.

#### (2) 규칙과 무관하게 항상 public인 엔드포인트 — **확인** (문서 명시)

`GET /v1/info` · `GET /v1/info/state` · `GET /v1/status`

> **H-01/H-02는 `rules.json` 내용과 무관하게 항상 동작한다.**

#### (3) TMS R1 호출별 결과 (`rules.json` 에 해당 섹션이 없다고 가정)

| TMS 호출 | 등급 | 결과 | FR |
|---|---|---|---|
| `GET /v1/info`, `/v1/info/state` | PUBLIC | ✅ **허용** | H-01, H-02 |
| `GET /v1/query` 목록 | AUTHENTICATED_USER | ✅ **허용** (`queries` 기본 허용) | FR-QL-01/02/03 |
| `GET /v1/query/{id}` | AUTHENTICATED_USER | ✅ **허용** | FR-QL-01 |
| `PUT /v1/query/{id}/killed` | AUTHENTICATED_USER | ✅ **허용** (`kill` 포함) | FR-QL-04 |
| **`GET /v1/jmx/mbean/…`** | **MANAGEMENT_READ** | ❌ **거부** | **H-03~H-07 전부** |
| `PUT /v1/info/state` (R3) | MANAGEMENT_WRITE | ❌ **거부** | FR-FL-03 |

> **→ FR-QUERY-LIVE는 조치 없이 동작한다.**
> **→ FR-CLUSTER-HEALTH의 JMX 기반 테스트(H-03~H-07)는 `rules.json` 에 `system_information` 규칙을 추가해야 동작한다.**

#### (3-1) ⭐ 조용한 필터링 — **로컬 Trino 477 로 실증 (2026-08-06)**

운영 환경과 동일한 구조의 `rules.json` 을 로컬 Trino 477에 적용하고, **같은 클러스터·같은 순간에 계정만 바꿔** 호출했다.

```jsonc
"queries": [
  { "user": "tms-svc",            "allow": ["view", "kill"] },
  { "user": "prometheus_scraper", "allow": [] },
  { "allow": ["execute", "view", "kill"] }
]
```

| 계정 | `GET /v1/query` | JMX `RunningQueries` |
|---|---|---|
| `tms-svc` | **1건** (HTTP 200) | 1 |
| `prometheus_scraper` | **0건 (HTTP 200)** — 403이 **아니다** | 1 |

> **거부가 오류가 아니라 빈 배열로 나타난다는 것이 실물로 확인됐다.** 두 응답 모두 HTTP 200이며, 바디만 다르다. 교차검증 없이는 "한가한 클러스터"와 구별할 방법이 없다.
> **H-09 교차검증이 이 상황을 정확히 잡았다** — `collection_error` 설정 + `advice` 제시, 그리고 **진짜 유휴(`RunningQueries=0`)일 때는 정상 통과**하는 것까지 함께 확인했다.

#### (3-2) kill 경로 — **실증 (2026-08-06)**

`PUT /v1/query/{id}/killed` 에 본문을 실어 호출한 결과:

| 항목 | 결과 |
|---|---|
| 응답 | 200 |
| 쿼리 상태 | `FAILED`, `errorCode = ADMINISTRATIVELY_KILLED` |
| **사용자에게 반환된 메시지** | `Query killed. Message: Killed by TMS. actor=syhcho, reason=…, request_id=…` |
| 권한 없는 계정의 kill | `403` → `TrinoForbidden(transient=False)` 로 분류됨 |

> **운영자가 입력한 사유가 쿼리를 실행한 사용자에게 그대로 도달한다는 설계 가정이 실증됐다** (`AUDIT_MODEL.md` §4-2). Trino가 `Query killed. Message: ` 접두사를 붙인다.

#### (4) 필요한 `rules.json` 추가분 (필드명 전부 문서 확인)

`system_information` 규칙 필드: `role`(선택), `user`(선택), `allow`(필수, 값 `read` / `write`)

```jsonc
{
  "system_information": [
    { "user": "tms-svc", "allow": ["read"] }        // R1: JMX 조회
    // R3에서 graceful shutdown이 필요해지면 ["read", "write"]
  ]
}
```

> **⚠️ `system_information` 섹션을 새로 추가하는 순간, 그 목록에 없는 모든 사용자는 시스템 정보 접근이 거부된다** (기본값이 "전부 거부"이므로 원래도 거부 상태였다 — 즉 **추가로 잃는 것은 없다**). 다만 기존에 이 섹션이 **이미 있다면** TMS 규칙을 추가할 뿐 기존 항목을 건드리지 않는다.
> **⚠️ `queries` 섹션이 이미 존재한다면** 기본 허용이 적용되지 않는다. 그 경우 TMS 서비스 계정에 `view`(+ FR-QL-04용 `kill`) 허용 규칙이 필요하다. **`rules.json` 실물 확인 필요.**
> **⚠️ 워커 배포**: 문서 원문 — *"Access control must be configured on the coordinator. Authorization for operations on specific worker nodes, such a triggering graceful shutdown, must also be configured on all workers."* R3 착수 시 `rules.json` 을 전 워커에 배포해야 한다.
> **`management.user` 는 우회 수단이 아니다** — 문서 원문: *"When this is configured, system information rules must still be set to authorize this user."*

#### (5) `file` 접근제어는 컬럼 마스킹·행 필터를 **이미 지원한다** — **확인**

`tables` 규칙에 `filter` / `filter_environment` / `columns[].mask` / `mask_environment` 필드가 존재한다. 조건부 마스킹(`IF`, `CASE`)도 가능하다.

> **함의 (TMS 범위 밖, 참고)**: "테이블·컬럼·행 마스킹"만이 목적이라면 **`file` 접근제어로도 달성 가능**하다. OPA의 이점은 마스킹 기능 자체가 아니라 **정책의 동적 갱신·중앙화·기존 내부 OPA 자산 재사용**이다. 선택 기준을 그쪽에 두는 것이 정확하다.
> **`file` 접근제어의 impersonation**: `impersonation` 규칙(`original_user`, `original_role`, `new_user`)으로 처리 가능하다. 즉 "고정 계정 + `X-Trino-User`" 방식은 **OPA 없이 `rules.json` 만으로도 성립한다.**

---

### T3-7. `QueryManager` 쿼리 카운터의 의미 — **실측 확인 (로컬 Trino 477, 2026-08-07)**

`trino.execution:name=QueryManager` 의 누적 카운터는 서로 다른 생애 단계를 센다. **`FailedQueries` 는 `StartedQueries` 의 부분집합이 아니다.**

| 카운터 | 증가 시점 |
|---|---|
| `SubmittedQueries` | 코디네이터가 쿼리를 접수할 때 |
| `StartedQueries` | **실행이 시작될 때** |
| `CompletedQueries` | 종료 상태에 도달할 때 (성공·실패·취소 모두) |
| `FailedQueries` | 종료 상태가 실패일 때 |

**실측 방법**: 분석(analysis) 단계에서 실패하는 쿼리(`SELECT * FROM does_not_exist.a.b`) 10건을 클라이언트 프로토콜로 종료까지 구동한 뒤 누적 카운터 델타를 측정했다.

```
submitted +12   completed +12   failed +11   started +1
                                             ^^^^^^^^^^ (배경 트래픽 1건뿐)
```

즉 **실행 전에 거부된 쿼리는 `Failed` 와 `Completed` 를 올리지만 `Started` 는 올리지 않는다.** 같은 서버에서 누적값이 `Failed=73 > Started=64` 인 상태도 관측했다.

> **⛔ 함의**: 실패율의 분모로 `StartedQueries` 를 쓰면 **100%를 초과한다**. 실제로 UI에 `120.5%` 가 표시됐다. 카탈로그 오설정이나 권한 거부처럼 "실행 전 실패"가 많은 상황 — 즉 헬스 체크가 가장 필요한 순간 — 에 오차가 최대가 된다. **분모는 `CompletedQueries` 를 쓴다** (H-05). 정의상 `Failed ⊆ Completed` 이므로 100%를 넘을 수 없다.

**주의**: `POST /v1/statement` 는 쿼리를 `QUEUED` 로 만들 뿐이다. 클라이언트가 `nextUri` 를 따라가야 실제로 디스패치되어 카운터에 반영된다. 카운터 실험 시 반드시 종료까지 구동할 것.

---

## 4. 검증 결과가 무효화 / 변경한 요구사항

| 요구사항 | 검증 결과에 따른 변경 | 근거 |
|---|---|---|
| **FR-LOGLEVEL** | **폐기 아님. 축소 존치.** REST API 없음 → JMX MBean 경유. **FR-LL "재시작 후 유지" 특성 삭제.** 구현 방식은 인간 결정 필요 | §T1-3 |
| **FR-WORKLOAD** | 데이터 소스를 **Gateway → Trino** 로 변경. Gateway 19가 리소스 그룹 관리 기능을 전부 제거 | §T2-3, §T1-4 |
| **FR-CATALOG** | **FR-CT에 "변경(ALTER)" 기능을 넣지 않는다.** 477에 `ALTER CATALOG`가 없음. 카탈로그 변경 = DROP+CREATE = Hive/Iceberg에서는 재시작 필요 | §T1-6 |
| **FR-GW-04** | AC 축소. `databaseCache`는 **백엔드 목록만** 캐시. "DB 다운 시 전 기능 정상"은 성립하지 않음 | §T2-4 |
| **FR-QL-04** | kill 구현을 `PUT /v1/query/{queryId}/killed` 로 확정 (본문에 reason 전달 가능). `DELETE`는 사유를 남길 수 없어 부적합 | §T1-5 |
| **FR-FL-03** | 타임아웃 기본값은 **최소 `2 × shutdown.grace-period`** 를 반영해야 함 (기본 설정 시 4분+) | §T1-2 |
| **S4 (LB 세션 어피니티)** | **필수 → 선택.** Gateway가 공유 DB로 크로스 게이트웨이 queryId 조회를 하므로 어피니티 불요. 단 S5 선행 필요 | §T2-7 |
| **FR-QH-01 (저장 스키마)** | `taskStatistics` / `operatorSummaries` / `plan` / `jsonPlan` 은 기본 저장 대상에서 제외. 화이트리스트 방식 | §T1-1 |

---

## 5. 미해소 항목 (확인 불가 / 실환경 확인 필요)

| # | 항목 | 왜 미해소인가 | 누가 | 언제까지 |
|---|---|---|---|---|
| **G-1** | **운영 중인 Trino Gateway 버전** | 프로젝트 문서 어디에도 기록이 없다. §2 전체가 이 값에 의존한다 | 인간(플랫폼팀) | **R1 착수 전** |
| **G-2** | 현재 Gateway `modules` / `clusterStatsConfiguration` / `requestAnalyzerConfig` / `databaseCache` 실제 설정값 | 실환경 config 파일 미확인 | 인간(플랫폼팀) | R1 착수 전 |
| **G-3** | External Routing Service가 **무응답/타임아웃**일 때 Gateway 동작 | 문서는 `errors` 필드 응답 시의 폴백만 기술. 무응답 시는 미기술 | trino-expert (실환경 실험) | **R2 착수 전 (NFR-ISOLATION 직결)** |
| **G-4** | OPA로 graceful shutdown 인가가 실제로 동작하는지 | 소스상 성립하나 공식 문서 미기술 | trino-expert (실환경 실험) | R3 착수 전 |
| **G-5** | 리소스 그룹 JMX MBean의 정확한 ObjectName 문자열 | 생성 규칙 역산은 추측이 됨 | trino-expert (`GET /v1/jmx/mbean` 열거) | R2 착수 전 |
| **G-6** | `JmxOpenMetricsModule` 의 OpenMetrics 노출 경로 | 소스에 모듈 등록만 확인, 경로 확인 불가 | trino-expert / 인프라 | S6 적용 시 |
| **G-7** | `/v1/jmx/mbean` 이 우리 인증 설정(OPA + TLS)에서 실제로 접근 가능한지 | `MANAGEMENT_READ` 바인딩은 확인. 실환경 인증 조합 미확인 | trino-expert (실환경 실험) | R1 착수 전 |
| **G-8** | Gateway 19의 `/webapp/findQueryHistory` 보안 강화 후 인증 요건 | 릴리스 노트에 변경 사실만 있고 상세 미기술 | trino-expert | Task 5 수행 시 |

---

## 6. 출처

**Trino 477**
- https://trino.io/docs/477/develop/event-listener.html
- https://trino.io/docs/477/admin/graceful-shutdown.html
- https://trino.io/docs/477/admin/logging.html
- https://trino.io/docs/477/admin/properties-catalog.html
- https://trino.io/docs/477/admin/resource-groups.html
- https://trino.io/docs/477/admin/jmx.html
- https://trino.io/docs/477/connector/jmx.html
- https://trino.io/docs/477/connector/system.html
- https://trino.io/docs/477/security/opa-access-control.html
- https://trino.io/docs/477/sql/create-catalog.html · `/sql/drop-catalog.html`

**Trino 소스 (태그 `477`)** — `github.com/trinodb/trino/blob/477/…`
- `core/trino-spi/src/main/java/io/trino/spi/eventlistener/{EventListener,QueryCompletedEvent,QueryMetadata,QueryStatistics,QueryContext,QueryIOMetadata,QueryFailureInfo}.java`
- `core/trino-main/src/main/java/io/trino/server/{Server,QueryResource,ServerInfoResource,ResourceGroupStateInfoResource}.java`
- `core/trino-main/src/main/java/io/trino/server/security/ServerSecurityModule.java`
- `core/trino-main/src/main/java/io/trino/connector/system/{QuerySystemTable,NodeSystemTable}.java`
- `core/trino-main/src/main/java/io/trino/execution/resourcegroups/InternalResourceGroupManager.java`
- `plugin/trino-opa/src/main/java/io/trino/plugin/opa/OpaAccessControl.java`
- `pom.xml` (`dep.airlift.version=361`)

**airlift 소스 (태그 `361`)** — `github.com/airlift/airlift/blob/361/…`
- `log-manager/src/main/java/io/airlift/log/{LogJmxModule,LoggingMBean}.java`
- `jmx-http/src/main/java/io/airlift/jmx/MBeanResource.java`

**Trino Gateway (`main` 브랜치 — §5 G-1 참조)** — `github.com/trinodb/trino-gateway`
- `docs/{installation,operation,routers,routing-logic,routing-rules,gateway-api,security,release-notes}.md`
- `gateway-ha/src/main/java/io/trino/gateway/ha/router/{BaseRoutingManager,StochasticRoutingManager,HaQueryHistoryManager}.java`
- 이슈/PR: [#1032](https://github.com/trinodb/trino-gateway/issues/1032), [#1054](https://github.com/trinodb/trino-gateway/pull/1054), [#1107](https://github.com/trinodb/trino-gateway/pull/1107), [#656](https://github.com/trinodb/trino-gateway/issues/656), [#1005](https://github.com/trinodb/trino-gateway/pull/1005)

**OPA**
- https://www.openpolicyagent.org/docs/management-decision-logs
