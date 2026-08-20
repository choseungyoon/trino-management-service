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

### T1-2-1. 노드 인벤토리 소스 — **실측 2026-08-09. 문서 가정 2건 정정**

로컬 Trino 477 상대 실측이다. **`REQUIREMENTS.md` 의 FR-FL-01 설계 근거 두 가지가 모두 틀렸다.**

| 소스 | 문서가 말한 것 | 실측 결과 |
|---|---|---|
| `GET /v1/node` | "decommission 노드가 남는 등 **신뢰성 문제**가 있어 보조 소스로만" | **404. 477 에 존재하지 않는다.** 보조 소스가 아니라 소스가 아니다 |
| `SELECT * FROM system.runtime.nodes` | "**1차 소스**로 사용한다" | `PERMISSION_DENIED: Access Denied: Cannot execute query`. **`ExecuteQuery` 권한이 필요**하며 TMS 서비스 계정은 갖고 있지 않다 |

```
GET /v1/node        → 404      (/v1/node/ , /v1/node/failed 도 동일)
GET /ui/api/node    → 401      (Web UI 전용. 관리 API 가 아니다)
```

**그래서 TMS 가 실제로 쓰는 소스** — 인증 없이, 쿼리 없이 노드별 사실을 얻는다.

| 필드 | 소스 |
|---|---|
| host / IP, role, 소속 클러스터 | **Ansible 인벤토리 파일** (요구사항이 지목한 정적 정보 소스) |
| nodeId, state, 버전, environment, uptime, coordinator 여부 | **각 노드의 `GET /v1/info`** — `PUBLIC` 이므로 **자격증명 없이 200** |

```
$ curl -s https://<node>:8443/v1/info      # 인증 헤더 전혀 없음
{"nodeId":"local-coordinator-1","state":"ACTIVE","nodeVersion":{"version":"477"},
 "environment":"tmslocal","coordinator":true,"coordinatorId":"h83jm",
 "starting":false,"uptime":"1.10d"}
```

**`trino.node:name=CoordinatorNodeManager` 는 개수만 준다** — 실측 속성 5개:
`ActiveNodeCount`, `InactiveNodeCount`, `ShuttingDownNodeCount`, `DrainingNodeCount`, `DrainedNodeCount`. **노드 식별자는 없다.**

> ⛔ **따라서 FR-FL-02(어느 워커가 discovery 에 조인하지 않았는가)는 `ExecuteQuery` 없이는 불가능하다.** TMS 는 "12대 중 11대가 조인했다"까지 말할 수 있고 **어느 한 대인지는 말할 수 없다.** 화면이 그 한계를 명시한다.
>
> `ExecuteQuery` 를 TMS 에 부여하면 TMS 가 프로덕션에서 SQL 을 실행할 수 있게 된다 — 필드 하나를 위해 침해 시 파급을 넓히는 것이므로 **플랫폼팀 결정 사항**이다 (`NEXT_STEPS.md` D-1).

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

**FR-LOGLEVEL에 대한 함의 (판정은 `archive/BOLT_0_RESULT.md` §2 참조)**

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

> **⛔ 2026-08-08 실측 정정 — 위 표의 "점 포함 ID 그대로 사용 가능" 은 사실이 아니다.**
>
> 경로 정규식 `{resourceGroupId: .+}` 이 점을 매칭하는 것은 맞지만, **조회는 실패한다.** 로컬 Trino 477 에 `global → adhoc → dashboard` 3단계를 구성하고 실제로 쿼리를 흘린 뒤 측정했다.
>
> | 요청 | 결과 |
> |---|---|
> | `GET /v1/resourceGroupState/global` | **200** |
> | `GET /v1/resourceGroupState/global.adhoc` | **404** |
> | `GET /v1/resourceGroupState/global.adhoc.dashboard` | **404** |
> | `GET /v1/resourceGroupState/global%2Eadhoc` (URL 인코딩) | **404** |
>
> **루트 그룹 이름만 받는다.** 소스의 정규식만 보고 역산한 가정이 실제 동작과 달랐던 사례다.
>
> **⛔ 게다가 응답은 root + 1단계까지만 내려준다.** `global` 응답의 `subGroups[0]`(= `adhoc`)에는 **`subGroups` 키 자체가 없다.** 그런데 같은 응답의 `runningQueries[].resourceGroupId` 는 `["global","adhoc","dashboard"]` 로 3단계를 가리킨다.
>
> **결론: 이 REST 엔드포인트만으로는 리소스 그룹 트리를 만들 수 없다.** 2단계 아래는 조회할 방법이 없다. FR-WORKLOAD / FR-GW-02 는 아래 JMX 경로를 써야 한다.

**방법 B — JMX** — ✅ **확인 완료 (2026-08-08 실측). Bolt 0 의 "ObjectName 확인 불가" 항목 해소**

- 리소스 그룹 JSON에 `"jmxExport": true` 를 설정하면 해당 그룹이 JMX로 export 된다. — **확인** (`/docs/477/admin/resource-groups.html`)
- **ObjectName 실측 확정** — `jmxExport: true` 를 준 4개 그룹 중 트래픽이 흐른 3개가 등록됐다.

```
trino.execution.resourcegroups:name=InternalResourceGroupManager
trino.execution.resourcegroups:type=InternalResourceGroup,name=global
trino.execution.resourcegroups:type=InternalResourceGroup,name=global.adhoc
trino.execution.resourcegroups:type=InternalResourceGroup,name=global.adhoc.dashboard
```

> **중첩 그룹은 각자 독립된 MBean 을 갖고, `name=` 에 전체 점 경로가 들어간다.** 부모 MBean 안에 중첩되지 않는다. 따라서 **`GET /v1/jmx/mbean` 을 열거해 `type=InternalResourceGroup` 을 필터링하면 전체 트리를 복원할 수 있다.** REST 의 깊이 제한을 우회하는 유일한 경로다.

**MBean 속성 31개** (`io.trino.execution.resourcegroups.InternalResourceGroup`) — 주요 항목:

| 속성 | 비고 |
|---|---|
| `RunningQueries`, `QueuedQueries`, `WaitingQueuedQueries` | 현재 상태 |
| `HardConcurrencyLimit`, `MaxQueuedQueries` | **읽기/쓰기 모두 가능** |
| `SoftConcurrencyLimit`, `SoftMemoryLimitBytes`, `SoftCpuLimitMillis`, `HardCpuLimitMillis` | 읽기 전용 |
| `CpuUsageMillis`, `MemoryUsageBytes`, `PhysicalInputDataUsageBytes` | 누적 사용량 |
| `StartedQueries.*`, `TimeBetweenStartsSec.*` | 1/5/15분 EWMA + TotalCount |

> **⛔ 두 가지 함정**
>
> 1. **리소스 그룹은 지연 생성된다.** 설정에 있어도 **쿼리가 한 번이라도 배정되기 전에는 MBean 도 없고 REST 응답의 `subGroups` 에도 없다.** 실측에서 `global.etl` 은 설정에 있었으나 트래픽이 없어 어디에도 나타나지 않았다. → **TMS 는 "설정되었으나 유휴인 그룹"을 알 수 없다.** 전체 목록이 필요하면 `resource-groups.json` 자체를 읽어야 한다.
> 2. **`HardConcurrencyLimit`·`MaxQueuedQueries` 쓰기는 런타임 한정으로 보인다.** 파일 기반 설정이 재시작 시 다시 적용되므로 JMX 로 바꾼 값은 사라질 가능성이 높다(**미검증**). R2 에서 이 쓰기를 기능으로 노출한다면 **"재시작하면 되돌아간다"는 점을 반드시 확인하고 화면에 명시해야 한다** — 비밀번호 해시 미반영과 같은 부류의 함정이다.

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

### T1-4-1. `db` 리소스 그룹 매니저 — **실측 2026-08-13 (로컬 Trino 477)**

D-010(파일 → DB 전환)의 선행 검증. **테이블 스키마는 업스트림 문서에 없다 — 아래가 유일한 근거다.**

#### ① `?currentSchema=` 를 존중한다 — **확인**

`resource-groups.config-db-url=jdbc:postgresql://…/db?currentSchema=trino_resource_groups` 로 기동하면 4개 테이블이 **전부 그 schema 에** 생성된다. `public` 오염 없음.

```
     table_schema      |            table_name
-----------------------+-----------------------------------
 trino_resource_groups | resource_groups
 trino_resource_groups | selectors
 trino_resource_groups | resource_groups_global_properties
 trino_resource_groups | exact_match_source_selectors
```

→ **다른 시스템과 DB 를 공유하면서 schema 로 격리할 수 있다.** Gateway 19 가 남기는 동명 테이블과의 충돌도 이걸로 피한다.

#### ② ⛔ **DB 가 닿지 않으면 코디네이터가 기동하지 못한다** — **확인**

```
INFO   io.trino.execution.resourcegroups.InternalResourceGroupManager  -- Loading resource group configuration manager --
INFO   io.trino.plugin.resourcegroups.db.FlywayMigration               Performing migrations...
ERROR  io.trino.server.Server   Unable to obtain connection from database (…) : Connection refused
```

프로세스가 종료된다 (`launcher status` → `Not running`). **재시도·백오프 없음.** `FlywayMigration.migrate()` 가 `Server.doStart()` 안에서 **HTTP 서버 바인딩 이전에 메인 스레드로 동기 실행**되기 때문이다.

> **⚠️ `max-refresh-interval` 은 이걸 막지 못한다.** 그 설정은 *이미 돌고 있는* 코디네이터가 리프레시 실패를 견디는 시간이고, **기동 경로와 무관하다.**
>
> **운영 제약**: 리소스 그룹 DB 가 정지 중이면 Trino 코디네이터를 재시작할 수 없다. 안전 시퀀스(FR-CO-02) 4단계 진입 전에 DB 도달성을 확인해야 한다 — 유입을 차단해 놓고 되살리지 못하는 상태가 최악이다.

#### ③ 반면 **이미 돌고 있는 코디네이터는 DB 가 사라져도 멀쩡하다** — **확인**

DB 를 정지시킨 채 코디네이터를 그대로 두고 측정했다.

| 확인 | 결과 |
|---|---|
| `GET /v1/info` | **200, `ACTIVE`** |
| 기존 사용자의 쿼리 | **`FINISHED`** |
| **한 번도 쿼리한 적 없는 사용자**의 쿼리 | **`FINISHED`** |
| DB 복구 후 | **Trino 재시작 없이 자가 회복** |

세 번째가 핵심이다. `${USER}` 그룹은 지연 생성이므로 *"DB 가 없으면 신규 사용자만 못 던지는"* 상태가 될 수 있었지만, **캐시된 설정만으로 그룹 생성과 셀렉터 매칭이 모두 성립한다.** 디스패치 시점에 DB 를 읽지 않는다.

> **⚠️ 함정 — 장애 중 로그가 초당 1건씩 쌓인다**
>
> 재시도 간격은 `refresh-interval`(**기본 `1s`**)이다. `max-refresh-interval` 은 이 간격을 늘리지 않는다 — 그건 낡은 설정을 견디는 총 시간이다. 실측에서 **2분 9초 동안 129건**이 나왔고, 매 건마다 전체 스택 트레이스가 붙는다.
>
> ```
> ERROR DbResourceGroupConfigurationManager  Error loading configuration from db
> org.jdbi.v3.core.ConnectionException: … Connection refused
> ```
>
> 기본값으로 두면 **DB 장애 하루 = 코디네이터당 ERROR 약 8.6만 건 + 스택 트레이스**다. 로그 저장소와 에러율 알림이 같이 흔들린다. 리소스 그룹 값은 자주 바뀌지 않으므로 **`resource-groups.refresh-interval=10s`** 로 늘리는 것을 권한다 (반영 지연 10초 ↔ 로그량 1/10).
>
> **회복은 아무 로그도 남기지 않는다.** 성공한 리프레시는 조용하다. 즉 **"에러가 멈춘 것"이 유일한 회복 신호**다 — 알림은 이 ERROR 의 *부재*로 판정해야 한다.

#### ④ 실제 스키마 (`\d+`, Trino 477 이 자동 생성)

**`resource_groups`**

| 컬럼 | 타입 | NULL |
|---|---|---|
| `resource_group_id` | `bigint` (자동증가) | not null (**PK**) |
| `name` | `varchar(250)` | **not null** |
| `max_queued` | `integer` | **not null** |
| `hard_concurrency_limit` | `integer` | **not null** |
| `soft_memory_limit` | `varchar(128)` | null |
| `soft_concurrency_limit` | `integer` | null |
| `scheduling_policy` | `varchar(128)` | null |
| `scheduling_weight` | `integer` | null |
| `jmx_export` | `boolean` | null |
| `soft_cpu_limit` / `hard_cpu_limit` | `varchar(128)` | null |
| `hard_physical_data_scan_limit` | `varchar(128)` | null |
| `parent` | `bigint` | null |
| `environment` | `varchar(128)` | null |

**`selectors`** — PK `id`(자동증가). `resource_group_id bigint not null`, `priority bigint not null`, `user_regex`/`source_regex`/`query_type`/`client_tags`/`original_user_regex`/`authenticated_user_regex` `varchar(512)`, `user_group_regex varchar(2048)`, `selector_resource_estimate varchar(1024)`.

**`resource_groups_global_properties`** — PK `name varchar(128)`, `value varchar(512)`. **CHECK 제약으로 `name` 은 `cpu_quota_period` 와 `physical_data_scan_quota_period` 둘만 허용**된다.

> **⛔ 함정 2가지 — FR-WL-07(편집 화면) 설계에 직결된다**
>
> 1. **`ON DELETE CASCADE` 가 걸려 있다.** `resource_groups.parent` 와 `selectors.resource_group_id` 양쪽 모두. **루트 그룹 한 줄을 지우면 하위 트리와 셀렉터 전부가 함께 사라진다.** 삭제 UI 는 영향 범위를 먼저 보여줘야 한다.
> 2. **`(name, parent, environment)` 유니크 제약이 없다.** PK 는 자동증가 ID 뿐이다. 같은 INSERT 를 두 번 실행하면 **DB 가 조용히 중복을 받는다.** 멱등성은 전적으로 애플리케이션 책임이다.

**관련**: `DECISIONS.md` D-010, `docs/templates/resource-groups-db.sql`

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

### T1-5-1. 클라이언트 프로토콜 응답의 `stats` — **실측 2026-08-21 (로컬 Trino 477)**

FR-BM-01 이 기록하는 숫자의 출처다. `POST /v1/statement` 후 `nextUri` 를 끝까지 따라갔을 때, **마지막 응답의 `stats`** 가 최종값이다.

**⛔ 첫 응답의 `stats` 를 읽으면 안 된다.** 실측한 첫 응답은 `state: QUEUED` 에 전 필드가 0 이었다. 거기서 읽으면 **모든 쿼리가 0ms 로 기록된다** — 벤치마크로서는 조용히 전부 틀린 값이다.

최종 응답에서 실제로 온 필드 (전량 실측):

| 필드 | 예시 | 비고 |
|---|---|---|
| `id` | `20260820_150544_00006_ym5f6` | **실패한 쿼리에도 온다.** 나중에 히스토리에서 찾을 유일한 열쇠 |
| `stats.state` | `FINISHED` | |
| `stats.elapsedTimeMillis` | 464 | |
| `stats.cpuTimeMillis` | 259 | |
| `stats.queuedTimeMillis` / `planningTimeMillis` / `analysisTimeMillis` | 7 / 128 / 1 | |
| `stats.processedRows` / `processedBytes` | 18050 / 352 | ⚠️ 아래 |
| `stats.peakMemoryBytes` | 1360 | |
| `stats.rootStage.*` | 스테이지별 동일 지표 | |

⚠️ **`processedRows` 는 사소한 쿼리에서 0 으로 온다.** `SELECT 1` 은 최상위 `processedRows: 0` 인데 `rootStage.processedRows` 는 1 이었다. 행 수를 정확히 세야 하는 용도로 쓰지 않는다 — TMS 는 참고 표시로만 쓴다.

**실패 시**: `error` 를 담은 응답이 오고 `stats` 는 없다. TMS 는 이때 **자기 벽시계 시간**을 기록한다 — "40초 만에 실패" 와 "즉시 실패" 는 다른 발견이고, Trino 측 숫자가 없다고 실패를 안 남기면 그 구분이 사라진다.

> **여기서 실제 버그가 하나 나왔다.** `clients/sql.py` 가 `response.text` (속성) 를 읽고 있었다. `HttpResponse.text` 는 **메서드**이고, 바운드 메서드는 truthy 라 `or "{}"` 도 걸리지 않아 `json.loads` 가 `TypeError` 로 죽는다. 단위 테스트의 가짜 응답이 `.text` 를 문자열로 갖고 있어서 **작성 시점부터 테스트가 버그에 동의하고 있었다.** 실 Trino 에 처음 붙인 순간 드러났다. 가짜는 이제 실제 `HttpResponse` 를 쓴다.

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

**필드 의미 — 확인 (2026-08-07, `gateway-api.md`)**

| 필드 | 의미 |
|---|---|
| `proxyTo` | Gateway가 **쿼리를 실제로 전달하는 주소**. 클러스터 생성·수정 시 지정한다 |
| `externalUrl` | **선택 항목.** 문서 원문: *"If the Trino cluster URL is different from the `proxyTo` URL, for example if they are internal and external hostnames used, you can use the optional `externalUrl` field to override the link in the **Active Backends** page."* |

> **`externalUrl` 은 Active Backends 화면의 링크만 바꾼다.** 라우팅에는 관여하지 않는다. 내부/외부 호스트명이 갈리지 않는 환경이라면 **`proxyTo` 와 같은 값을 넣거나 비워도 무방하다.**
> **TMS 매핑**: `proxyTo` → `coordinator_url`(JMX 폴링 대상), `externalUrl` → `trino_ui_url`(사용자에게 보여줄 링크).

**API 인증·인가 — 확인 (2026-08-07, `security.md`)**

REST API는 웹 UI와 **동일한 인증·인가**를 받는다. 역할은 셋이다.

| 역할 | 문서 원문 |
|---|---|
| `ADMIN` | *"Allows access to the Editor tab, which can be used to configure the clusters"* |
| `USER` | *"Allows access to the rest of the website"* |
| **`API`** | *"Allows access to rest apis to configure the clusters"* |

권한 부여 경로: preset user 정의 / LDAP 속성 / OAuth 클레임 (`privilegesField` 설정).

### T2-3-1. Gateway 19 로컬 실측 (2026-08-08) — **문서로는 알 수 없던 사실 5건**

프로덕션과 같은 **버전 19** 를 로컬에 설치해 직접 확인했다. 아래는 전부 실행 결과다.

> **아티팩트 위치**: GitHub Releases 에 바이너리가 **없다**(v17~20 전부 `assets: []`). Maven Central 검색 색인도 15까지만 보인다. 실제 파일은 저장소에 있다 —
> `https://repo1.maven.org/maven2/io/trino/gateway/gateway-ha/19/gateway-ha-19-jar-with-dependencies.jar`
> 실행: `java -jar gateway-ha-19-jar-with-dependencies.jar config.yaml` (구버전의 `server` 서브커맨드는 없다).

#### ⛔ (1) 인증 설정이 없으면 API 가 **무인증으로 쓰기까지** 허용한다

`authentication` 블록 없이 기동한 상태에서 전부 성공했다.

| 요청 | 결과 |
|---|---|
| `GET /gateway/backend/all` | **200** — 백엔드 목록 노출 |
| `POST /gateway/backend/modify/add` | **200** — 백엔드 추가됨 |
| `POST /gateway/backend/deactivate/{name}` | **200** — `active: false` 로 바뀜 |
| `GET /webapp/getRoutingRules` | **200** — 라우팅 규칙 노출 |

> **포트에 도달할 수 있는 누구든 전 클러스터의 쿼리 유입을 끊거나 백엔드를 바꿔치기할 수 있다.** 운영 Gateway 에 `authentication` 이 설정되어 있는지 **반드시 확인해야 한다.**

#### ✅ (2) 라우팅 규칙 **조회 엔드포인트가 존재한다** — 문서에 없다

```
GET /webapp/getRoutingRules   → 200
{"code":200,"msg":"Successful.","data":[
  {"name":"adhoc-header","description":"...","priority":0,
   "condition":"request.getHeader(\"X-Trino-Source\") == \"adhoc-test\"",
   "actions":["result.put(\"routingGroup\", \"adhoc\")"]}]}
```

- `POST` 는 **405**. GET 전용이다.
- `routingRules` 설정이 없으면 `RoutingRulesManager.getRoutingRules` 에서 NPE → **500**. 404 가 아니라 500 이라는 점이 단서였다.
- 전제 설정: `routingRules.rulesEngineEnabled: true` + `rulesType: FILE` + `rulesConfigPath`.

> **⛔ 이 발견은 `DESIGN_R2.md` 의 "FR-GW-05·FR-RV-01 구현 불가" 판정을 뒤집는다.** 다만 **문서화되지 않은 경로**이므로 버전업 시 예고 없이 사라질 수 있다. 사용한다면 실패를 정상 열화로 처리해야 한다.

#### ⛔ (3) `modify/delete` 는 평문 이름을 받고, **무엇을 보내든 200 을 반환한다**

| 본문 | 응답 | 실제 삭제 |
|---|---|---|
| `{"name":"x"}` (JSON) | 200 | **안 됨** |
| `"x"` (JSON 문자열) | 200 | **안 됨** |
| `x` (평문) | 200 | 됨 |

> **200 은 삭제 성공을 뜻하지 않는다.** TMS 가 삭제를 구현한다면 **반드시 `backend/all` 을 다시 읽어 확인**해야 한다. 상태 코드만 믿으면 "삭제했습니다"라고 표시해놓고 아무 일도 안 일어난 상태가 된다.

#### ⛔ (4) `monitorType: UI_API` 는 `backendState` 블록을 **요구한다**

없으면 기동 자체가 실패한다 — `IllegalArgumentException: BackendStateConfiguration is required for monitor type: UI_API`. 이 블록에 코디네이터 조회용 계정(`username`/`password`/`ssl`)을 넣는다.

#### ⛔ (5) 클러스터 통계 수집이 **TLS 검증에 실패하면 조용히 죽는다** — S1 이 무력화된다

자체 서명 인증서를 쓰는 코디네이터를 향해 이렇게 됐다.

```
ClusterStatsHttpMonitor.monitor → SSLHandshakeException: PKIX path building failed
ERROR ClusterStatsHttpMonitor  Received null/empty response for /ui/api/stats
```

기동 로그에는 `Using QueryCountBasedRouterProvider instead of default` 가 정상 출력된다. **설정은 적용됐는데 라우터의 입력이 0 이다.**

> **S1(least-loaded 라우팅)을 적용할 때 가장 빠지기 쉬운 함정이다.** Gateway JVM 의 truststore 에 사내 CA 가 없으면, 설정을 다 해도 라우터가 통계 없이 동작한다. 적용 후 **`/ui/api/stats` 관련 오류가 로그에 없는지** 반드시 확인할 것.

#### 부가 — `readyz` 는 백엔드가 없어도 200

문서 기반으로 적어둔 "백엔드 등록·헬스체크 전 503" 은 실측과 다르다. 백엔드 0개 상태에서도 `livez`·`readyz` 모두 200 이었다. **readyz 를 라우팅 준비 상태의 근거로 쓰지 말 것.**

---

**⛔ 문서화된 엔드포인트는 위 8개가 전부다 — 2026-08-08 재확인 (`gateway-api.md`, `routing-rules.md`)**

R2 설계에 필요한 두 경로가 **존재하지 않는다.**

| 필요한 것 | 상태 |
|---|---|
| 라우팅 규칙 **조회** (FR-GW-05, FR-RV-01) | **없다.** `POST /webapp/updateRoutingRules`(쓰기)만 문서화되어 있고 GET 이 없다. `routing-rules.md` 에도 조회 경로가 없다 |
| `databaseCache` **상태 조회** (FR-GW-04) | **없다.** 백엔드 DB 상태나 캐시 동작 여부를 보고하는 엔드포인트가 없다 |

> `rulesType: FILE` 이면 규칙은 Gateway 호스트의 **파일**에 있고 기본 1분마다 재읽기된다. TMS 는 Gateway 호스트 파일시스템에 접근하지 않으므로(별도 VM) 이 파일을 읽을 수 없다. **추측 경로를 만들지 않는다** — 규칙 조회는 API 가 생기기 전까지 구현 불가다.

> **⛔ "읽기 전용" 역할은 존재하지 않는다.** 백엔드 목록을 REST로 읽으려면 `API` 역할이 필요한데, 이 역할은 문서상 *configure* 권한이다 — 즉 **같은 자격증명으로 백엔드 추가·수정·삭제도 가능하다.** TMS가 Gateway 자격증명을 보유하면 그 자격증명은 라우팅을 바꿀 수 있는 권한이며, `tms-svc` 와 동급으로 보호해야 한다.
> **전제**: *"All authentication and authorization mechanisms require configuring TLS as the foundational layer."* Gateway에 TLS가 켜져 있어야 인증이 동작한다.

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

### T2-8. Gateway 19 클러스터 통계 모니터 — **실측 2026-08-10. 권고 1건 정정**

Gateway 19 jar 역어셈블 + Trino 477 실측. **`gateway-config-request.md` 가 원래 요청하던 `UI_API` 는 틀렸다.**

`ClusterStatsMonitorType` enum 실물(확인, jar): `NOOP` · `INFO_API` · `UI_API` · `JDBC` · `JMX` · `METRICS`.
→ **`UI_API` 는 19 에 존재한다.** 업스트림 `main` 문서에 안 보이는 것과 별개다.

| monitorType | 부르는 것 | Trino 477 · `tms-svc` |
|---|---|---|
| `INFO_API` | `GET /v1/info` | 200. **up/down 만.** 쿼리 수가 없어 `QueryCountBasedRouterProvider` 가 무력화된다 |
| `UI_API` | `/ui/api/stats`, `/ui/api/query?state=QUEUED` | **401.** Web UI 폼 로그인이 필요해 basic auth 로는 안 된다. Gateway 로그: `login request failed` |
| `JMX` | `/v1/jmx/mbean/trino.metadata:name=DiscoveryNodeManager` | **500 `InstanceNotFoundException`** — 477 에 없다 |
| `JDBC` | SQL | `ExecuteQuery` 필요 |
| **`METRICS`** | `GET /metrics?name[]=...` | **200** |

**⛔ 477 이 노드 수 MBean 이름을 바꿨다.** Gateway 19 의 기본값은 `trino_metadata_name_DiscoveryNodeManager_ActiveNodeCount` 인데, 477 에서 이 MBean 은 `trino.node:name=CoordinatorNodeManager` 다. JMX 모니터는 이 이름이 하드코딩되어 있어 **477 에서 쓸 수 없다.** METRICS 모니터는 `monitor.metricMinimumValues` 의 키를 조회 대상 지표명으로 쓰므로 우회된다 — Gateway 가 실제로 보낸 요청으로 확인:

```
GET /metrics?name[]=trino_execution_name_QueryManager_RunningQueries
            &name[]=trino_execution_name_QueryManager_QueuedQueries
            &name[]=trino_node_name_CoordinatorNodeManager_ActiveNodeCount   ← 우리가 지정한 이름
→ 200, 세 값 모두 반환
```

쿼리 수 지표 2개는 **Gateway 19 기본값 그대로 477 에서 동작한다.**

**`backendState` 계정**: `ClusterStatsJmxMonitor(HttpClient, BackendStateConfiguration)` / METRICS 모니터 모두 `backendState` 의 username·password 로 `Authorization` + `X-Trino-User` 를 보낸다. `/metrics` 는 `MANAGEMENT_READ` 이므로 **`tms-svc` 로 200 을 받는다(실측).** 다만 `tms-svc` 는 kill 권한도 가지므로, 읽기 전용 모니터 계정 분리를 권한다.

> **미검증 1건**: 로컬에서는 Trino 가 자체 서명 인증서라 Gateway 가 `Failed communicating with server` 로 막혀 **엔드투엔드 healthy 전환까지는 확인하지 못했다.** 구성요소(엔드포인트·지표명·권한)는 전부 실측이며, 남은 것은 Gateway 가 코디네이터 CA 를 신뢰하는지뿐이다 — 사내는 내부 CA 발급분이므로 §4 의 truststore 항목이 그대로 적용된다.

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
