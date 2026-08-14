# DESIGN — FR-WL-07 리소스 그룹 편집

> **선행**: D-010(리소스 그룹을 db 매니저로), D-011(서버 렌더 UI)
> **근거 데이터**: `TRINO_VERIFIED.md` §T1-4-1 (2026-08-13 로컬 477 실측 — 테이블 DDL·제약·기동 조건)

---

## 0. 왜 이제 가능한가

`DESIGN_R2.md` §1-6 은 **"쓰기는 R2 에서 하지 않는다"** 고 미뤘다. 이유는 이랬다:

> `HardConcurrencyLimit` 은 JMX 로 쓸 수 있지만 **파일 설정에서 오므로 재시작하면 되돌아갈 가능성이 높다(미검증).** 되돌아가는 변경을 "적용됨"으로 보여주는 것은 함정이다.

**D-010 이 그 전제를 없앴다.** 이제 설정의 출처가 파일이 아니라 **DB 테이블**이고, TMS 가 그 테이블에 쓴다. 되돌아갈 곳이 없다 — 쓴 값이 곧 출처다. §1-6 이 걸어둔 선행 조건 3개 중 ①(재시작 후 값 유지)이 다른 경로로 해소됐고, ②(되돌아가면 명시)는 무의미해졌으며, ③(`reason` + 감사 + admin)만 남는다.

**덤으로 오래된 제약 하나가 같이 풀린다.** §1-3 은 *"설정되었으나 유휴인 그룹은 보이지 않는다"* 고 적었다. JMX 는 트래픽이 흐른 그룹만 보여주기 때문이다. 이제 **설정된 전체 목록이 DB 에 있다.**

---

## 1. 범위

### 만드는 것

| ID | 내용 |
|---|---|
| FR-WL-07 | 리소스 그룹 **설정** 트리 조회 (DB 소스). 실행 중 상태(JMX)와 대조 |
| FR-WL-08 | 그룹 **값 수정** — 동시 실행 수·큐 길이·메모리 한도 등 |
| FR-WL-09 | 그룹·셀렉터 **추가/삭제** |
| FR-WL-10 | **변경 이력 + 되돌리기** |

### 만들지 않는 것

| | 대신 |
|---|---|
| 최초 트리 일괄 적재 | `docs/templates/resource-groups-db.sql` |
| 드래그로 트리 재배치 | 부모 선택 드롭다운 |
| "이 값을 바꾸면 어떻게 될까" 시뮬레이션 | 실제로 바꾸고 워크로드 화면에서 본다 |
| 예약 변경 (특정 시각에 적용) | 범위 밖 |
| `exact_match_source_selectors` 편집 | `exact-match-selector-enabled` 가 기본 `false` 다 |
| 전역 프로퍼티(`cpu_quota_period` 등) 편집 | 쿼터를 안 쓰기로 했다(D-010). 쓰게 되면 그때 |

---

## 2. 데이터 — 어디에 쓰고 이력은 어디에 두나

### 쓰는 곳

`trino_resource_groups` schema 의 `resource_groups` / `selectors`. **Trino 가 10초마다 읽는 바로 그 테이블이다.**

컬럼·타입·제약은 전부 §T1-4-1 에 실측으로 확보돼 있다. **추측하지 않는다** — 입력 검증의 상한값은 거기서 그대로 가져온다.

### 이력은 별도 테이블에 둔다

**Trino 의 테이블에는 이력 컬럼도 `reason` 컬럼도 없다.** 그리고 절대규칙 3 은 쓰기마다 감사를 요구한다.

`audit_action` 에 `details JSONB` 가 있으므로 거기 넣을 수도 있지만, **넣지 않는다.** 감사는 CSV 로 내보내는 append-only 기록이고(FR-AA-05), 행마다 트리 전문이 붙으면 내보내기가 못 쓰게 된다. 대신 전용 테이블을 두고 `request_id` 로 감사와 잇는다.

```sql
-- migrations/010_resource_group_revision.sql
CREATE TABLE resource_group_revision (
    id           BIGSERIAL    PRIMARY KEY,
    occurred_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    environment  VARCHAR(128) NOT NULL,
    actor        VARCHAR(256) NOT NULL,
    reason       TEXT         NOT NULL,
    -- Links to audit_action. The audit row is the record that something was
    -- done; this row is what it looked like before and after.
    request_id   UUID         NOT NULL,
    kind         VARCHAR(32)  NOT NULL,   -- group_update | group_create | ...
    target       VARCHAR(512) NOT NULL,   -- dotted path, or selector id
    tree_before  JSONB        NOT NULL,
    tree_after   JSONB        NOT NULL
);
```

**필드 단위 diff 가 아니라 트리 전문 스냅샷을 넣는다.** 한 environment 의 트리는 지금 3행, 커져도 수십 행이다. 전문을 넣으면 되돌리기가 "이 스냅샷을 다시 적용한다"로 끝나고, 부분 diff 를 역산하다 어긋날 여지가 없다. 크기로 아낄 것이 없는데 모호함을 살 이유가 없다.

`audit_action.action_type` 에 두 값을 추가한다: **`RESOURCE_GROUP_CHANGE`**, **`RESOURCE_GROUP_REVERT`**. 되돌리기를 별도 타입으로 두는 이유는 운영자의 의도가 다르고, 나중에 "되돌린 적이 몇 번인가"를 찾을 수 있어야 하기 때문이다.

---

## 3. 쓰기 모델

### 한 변경 = 한 트랜잭션

```
BEGIN
  pg_advisory_xact_lock(hashtext(environment))   -- 동시 편집 직렬화
  트리 읽기 → tree_before
  변경 적용
  트리 다시 읽기 → tree_after
  전체 검증 (§4) — 실패하면 ROLLBACK
  resource_group_revision INSERT
  audit_action INSERT
COMMIT
```

**검증을 변경 *이후*의 트리에 대해 한다.** 한 필드만 보면 통과하지만 트리 전체로는 깨지는 규칙(catch-all 셀렉터 부재, 형제 메모리 합)이 있기 때문이다.

**⚠️ 2026-08-14 구현 중 정정.** 감사는 `AuditGuard` 가 자기 연결로 쓰므로 이 트랜잭션에 들어오지 않는다. 실제로 보장되는 것은 두 가지다:

* **변경과 리비전 스냅샷은 한 트랜잭션이다** — 스냅샷 없는 변경이 생길 수 없다. 이것이 같은 DB 를 고른 값이다.
* **감사는 다른 모든 쓰기 액션과 동일하게** 선행 기록 + 종료 시 outcome 갱신이다. 감사 저장소가 죽어 있으면 액션 자체가 거부된다(AU1).

D-010 이 "한 트랜잭션"이라고 적은 것은 과했다. 여기가 정확한 범위다.

`pg_advisory_xact_lock` 은 두 관리자가 동시에 같은 클러스터를 편집할 때 필요하다. 낙관적 버전 관리 대신 잠금을 쓰는 이유는, 이 화면의 동시 사용자가 사실상 0~2명이라 경합이 드물고 **잠금이 훨씬 단순하기 때문**이다.

### 되돌리기

`tree_before` 를 그대로 다시 적용한다. 되돌리기도 **새 리비전을 만든다** — 이력을 지우지 않는다. 감사가 append-only 인 것과 같은 이유다.

### ⛔ 삭제는 보이는 것보다 파급이 크다

```
resource_groups.parent      → ON DELETE CASCADE
selectors.resource_group_id → ON DELETE CASCADE
```

**`global` 한 줄을 지우면 하위 트리와 그 그룹들을 가리키던 셀렉터가 전부 사라진다.** 그리고 10초 뒤 그 클러스터는 리소스 그룹이 없는 상태가 된다.

삭제 UI 는 **실행 전에 사라질 것을 전부 나열한다.** "3개 그룹과 2개 셀렉터가 함께 삭제됩니다" 가 아니라 목록으로 보여준다. 개수만 보여주면 사람은 세어보지 않는다.

---

## 4. 검증 규칙 — 이 설계의 핵심

**DB 가 막아주지 않는다.** `(name, parent, environment)` 유니크 제약이 없고, 값의 의미는 Trino 만 안다. 전부 TMS 가 막아야 한다.

### 거부 (저장 불가)

| # | 규칙 | 근거 |
|---|---|---|
| V1 | `name` 비어 있지 않고 **≤ 250자** | `varchar(250)` §T1-4-1 |
| V2 | `max_queued`, `hard_concurrency_limit` **필수, > 0** | NOT NULL. 0 이면 그 그룹은 아무것도 실행하지 못한다 |
| V3 | `soft_memory_limit` 은 절대값(`100GB`) 또는 백분율(`80%`) 형식 | 477 문서 |
| V4 | `soft_cpu_limit` 이 있으면 `hard_cpu_limit` 도 있어야 한다 | 477 문서 명시 |
| V5 | `scheduling_policy` ∈ `fair` \| `weighted_fair` \| `weighted` \| `query_priority` | 477 문서 |
| V6 | `query_priority` 인 그룹의 **모든 하위 그룹도 `query_priority`** | 477 문서 명시 |
| V7 | 정규식 컬럼 길이: `user_regex` 등 ≤512, `user_group_regex` ≤2048 | §T1-4-1 |
| V8 | 셀렉터의 `resource_group_id` 는 **같은 environment 의 실재 그룹** | 다른 환경을 가리키면 아무 일도 안 일어난다 |
| V9 | **같은 부모 아래 같은 이름 금지** | DB 에 유니크 제약이 없다. 중복 트리는 조용히 생긴다 |
| V10 | **catch-all 셀렉터가 최소 1개 존재해야 한다** (조건 없음, 최저 우선순위) | ⛔ 아래 참조 |
| V11 | `environment` 가 설정된 클러스터의 `node_environment` 와 일치 | 아니면 아무도 읽지 않는 행을 편집하는 것이다 |

> **V10 이 가장 중요하다.** Trino 477 문서는 **어떤 셀렉터에도 매칭되지 않는 쿼리가 어떻게 되는지 규정하지 않는다.** 실측도 하지 않았다. 그러므로 **그 상태를 만들 수 있는 경로를 두지 않는다.** 마지막 catch-all 셀렉터를 지우려 하면 거부한다.

### 경고 (저장은 하되 말해준다)

| # | 규칙 | 왜 |
|---|---|---|
| W1 | 형제 `soft_memory_limit` 백분율 합이 부모를 넘는다 | 합법이지만 의도한 배분이 아닐 가능성이 높다. **백분율은 부모가 아니라 클러스터 기준**이다 |
| W2 | `jmx_export` 가 꺼진 그룹 | **워크로드 화면에 영영 안 보인다.** 사용자별 리프에는 의도적으로 끄지만, 구조 그룹에서 꺼져 있으면 실수다 |
| W3 | `hard_physical_data_scan_limit` / CPU 쿼터를 새로 넣는다 | 초과 시 쿼리가 죽는 게 아니라 **쿼터 주기 동안 조용히 대기**한다. 가장 진단하기 어려운 실패다 |
| W4 | `user_group_regex` 를 쓴다 | **group provider 가 없으면 영구 미매칭**이다. TMS 는 이걸 안다 — `etc/group-provider.properties` 부재는 이미 확인된 사실이다 |
| W5 | 어떤 셀렉터도 가리키지 않는 그룹 | 도달 불가능한 그룹. 부모로만 쓰는 것이면 정상이다 |

> **W4 는 이번 세션에서 실제로 겪은 실수다.** 원래 `resource-group.json` 의 `{ "userGroup": "admin", … }` 셀렉터가 죽은 줄이었다. 사람이 다시 밟지 않도록 화면이 말해준다.

### 검증할 수 없는 것 — 정직하게

**정규식의 동작은 검증하지 못한다.** Python `re` 로 구문 오류는 잡지만, Trino 는 **Java 정규식**을 쓴다. 구문이 통과해도 매칭 결과가 다를 수 있다. 화면에 그렇게 쓴다 — "구문만 확인했다".

---

## 5. 화면

### 5-1. 설정 트리 (FR-WL-07) — 두 소스를 대조한다

| 그룹 | 설정 (DB) | 실행 중 (JMX) | 상태 |
|---|---|---|---|
| `global` | 동시 100 / 큐 1000 | running 3, queued 0 | ✅ |
| `global.${USER}` | 동시 8 / 큐 100 | — | 유휴 (jmx_export 꺼짐, 의도) |
| `admin` | 동시 20 / 큐 100 | — | **트래픽 없음** |
| — | 없음 | `legacy.batch` MBean 존재 | ⚠️ **DB 에 없는 그룹이 돌고 있다** |

**이 대조가 새로 생기는 능력이다.** 지금까지 TMS 는 "MBean 이 없다"가 *트래픽이 없어서인지* *`jmxExport` 가 꺼져서인지* 구분하지 못했다. 이제 DB 에 `jmx_export` 컬럼이 있으므로 **구분해서 말할 수 있다.**

마지막 줄이 이상 신호다 — 설정에 없는 그룹이 실행 중이면 누군가 DB 를 직접 고쳤거나 environment 가 어긋난 것이다.

`workload.enabled` 가 꺼져 있으면 JMX 열은 비우고 왜인지 쓴다. **설정 트리는 workload 와 무관하게 보인다** — DB 만 읽으면 되기 때문이다.

### 5-2. 값 수정 (FR-WL-08)

행 인라인 편집. 저장 시 `reason` 필수. 저장 후 화면은 **"적용됨"이 아니라 "최대 10초 내 반영"** 이라고 쓴다 — `refresh-interval=10s` 이고, 즉시가 아니다. 아직 일어나지 않은 일을 일어났다고 쓰지 않는다.

**적용 확인**: 컬렉터가 JMX 에서 `HardConcurrencyLimit` 을 이미 읽고 있으므로, 트래픽이 있는 그룹은 **다음 폴링에서 실제로 반영됐는지 대조할 수 있다.** 유휴 그룹은 확인할 방법이 없고, 그것도 그렇게 쓴다.

### 5-3. 셀렉터 (FR-WL-09)

`priority` 내림차순으로 보여준다 — **평가 순서 그대로**다. 순서가 곧 의미인데 화면이 다른 순서로 보여주면 안 된다. 각 행에 "이 셀렉터가 보내는 곳" 을 점 경로로 표시한다.

catch-all 행은 시각적으로 구분하고 **삭제 버튼을 주지 않는다** (V10).

### 5-4. 이력 (FR-WL-10)

리비전 목록 — 시각, 사람, `reason`, 무엇이 바뀌었는지. 각 행에 **되돌리기**. 되돌리기도 `reason` 을 요구한다.

---

## 6. htmx

`web/static/htmx.min.js` 로 vendoring 한다 (D-011, 사내에서 파일 커밋 가능 확인됨). npm·빌드·Node 없음.

| 상호작용 | 방식 |
|---|---|
| 행 편집 열기 | `hx-get="/resource-groups/{id}/edit"` → `hx-target="closest tr"` |
| 저장 | `hx-post` → `hx-swap="outerHTML"` 로 그 행만 교체 |
| 삭제 | `hx-delete` + 파급 목록을 담은 확인 |
| 검증 실패 | 서버가 **오류가 담긴 같은 행 HTML** 을 돌려준다 |

**검증 로직이 한 벌로 유지된다.** 서버가 어차피 보안 경계이므로 거기 있어야 하고, htmx 는 그 결과를 HTML 로 그대로 돌려받는다.

**`tms.js` 의 fragment 갱신 코드는 줄어든다** — 지금 손으로 만든 것이 `hx-get`/`hx-target` 이다.

---

## 7. 단계

| 단계 | 내용 | 위험 |
|---|---|---|
| **1** | 설정 트리 조회 + JMX 대조 (FR-WL-07). **쓰기 없음** | 없음. 읽기만 한다 |
| **2** | 값 수정 (FR-WL-08) + 리비전·감사·되돌리기 | 중간. 검증이 여기서 다 선다 |
| **3** | 그룹·셀렉터 추가/삭제 (FR-WL-09/10) | 높음. CASCADE 와 V10 |

**1단계를 먼저 하는 이유는 쓰기 위험이 0 인데 가장 아쉬웠던 것을 바로 해결하기 때문이다** — "설정되었으나 유휴인 그룹을 알 수 없다"가 여기서 없어진다. 그리고 2·3단계가 쓸 트리 읽기·대조 코드를 1단계가 만든다.

**2단계가 실제 요구다.** 안정화까지 숫자를 계속 고쳐야 하는데 지금은 `psql` 로 직접 `UPDATE` 해야 한다.

---

## 8. 인간 결정이 필요한 항목

| | 질문 | 권고 |
|---|---|---|
| ~~**H-1**~~ | ~~TMS 전용 쓰기 계정을 만들 것인가~~ | ⛔ **철회 (2026-08-14, 구현 중 판명).** 두 계정은 두 연결이고, 두 연결은 한 트랜잭션이 될 수 없다. 별도 계정을 두면 **변경과 리비전 스냅샷의 원자성**을 잃는다 — D-010 에서 같은 DB 를 고른 이유 그 자체를. 기존 `tms_app` 계정에 schema 권한을 주고 한 연결로 간다. "누가 바꿨나"는 감사 테이블이 이미 답한다 |
| **H-2** | 되돌리기를 어디까지 허용할 것인가 | 전체 트리 복원만. 부분 되돌리기는 만들지 않는다 — 조합이 늘어나는 만큼 검증 구멍이 생긴다 |
| **H-3** | 편집 권한을 admin 으로 한정할 것인가 | **한정한다.** 쿼리 수용 제어이므로 재시작과 같은 등급이다 (`MANAGE_HEALTH` 재사용 여부는 구현 시) |
