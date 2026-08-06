# AUDIT_MODEL — FR-AUDIT-ACTION 데이터 모델 및 강제 방식

> **Bolt 1 산출물 (U5)** · 작성 2026-08-06 · 상태: **인간 승인 대기**
> 출처 모델: Cloudera Manager 설정 변경 감사 + "Reason for change" 필수 입력 (`MARKET_RESEARCH.md`)

---

## 1. 원칙

| # | 원칙 | 근거 |
|---|---|---|
| **AU1** | **감사 기록에 실패하면 액션을 실행하지 않는다.** | FR-AA-01/04. 감사 없는 쓰기는 없다 |
| **AU2** | `reason` 없으면 **400**. 공백·빈 문자열도 거부 | FR-AA-02, CLAUDE.md 절대 규칙 3 |
| **AU3** | **append-only.** UPDATE/DELETE 경로를 코드에 만들지 않는다 | FR-AA-04 |
| **AU4** | 기록 주체는 **실제 요청자**다. TMS 서비스 계정이 아니다 | 추적성 |
| **AU5** | 실패한 액션도 기록한다 | "왜 안 됐나"도 감사 대상이다 |

> **AU1의 의미**: TMS PostgreSQL이 죽으면 **쓰기 API가 전부 503을 반환한다.** 이는 버그가 아니라 설계다 (`ARCHITECTURE.md` §4). 감사 우회 경로는 만들지 않는다.

---

## 2. 스키마 (PostgreSQL — D-004 승인 대기)

```sql
-- Append-only. No UPDATE/DELETE path exists in application code.
CREATE TABLE audit_action (
    id              BIGGENERATED_PLACEHOLDER,   -- BIGSERIAL / IDENTITY, 최종 확정은 Bolt 2
    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- Who (AU4)
    actor           VARCHAR(256) NOT NULL,      -- 실제 요청자 (LDAP/AD 사용자명)
    actor_roles     TEXT[]       NOT NULL,      -- 액션 시점의 역할 스냅샷
    actor_ip        INET,

    -- What
    action_type     VARCHAR(64)  NOT NULL,      -- §3 목록
    target_kind     VARCHAR(64)  NOT NULL,      -- 'query' | 'cluster' | 'health_test'
    target_id       VARCHAR(512) NOT NULL,      -- queryId / cluster name / test id
    target_cluster  VARCHAR(128),

    -- Why (AU2)
    reason          TEXT         NOT NULL CHECK (btrim(reason) <> ''),

    -- Result (AU5)
    outcome         VARCHAR(16)  NOT NULL,      -- 'SUCCESS' | 'FAILURE'
    error_message   TEXT,

    -- Context
    request_id      UUID         NOT NULL,      -- 요청 추적용
    details         JSONB                       -- 액션별 부가 정보 (변경 전/후 등)
);

CREATE INDEX audit_action_occurred_idx ON audit_action (occurred_at DESC);
CREATE INDEX audit_action_actor_idx    ON audit_action (actor, occurred_at DESC);
CREATE INDEX audit_action_target_idx   ON audit_action (target_kind, target_id);

-- AU3 방어선: 애플리케이션 계정에서 UPDATE/DELETE 권한을 회수한다.
-- REVOKE UPDATE, DELETE ON audit_action FROM tms_app;
```

> **`reason` 의 `CHECK (btrim(reason) <> '')` 는 DB 레벨 2차 방어선이다.** 1차 방어는 API 레벨(§4). 두 겹으로 막는다.
> **`REVOKE UPDATE, DELETE`** 를 마이그레이션에 포함한다. 코드에 경로가 없다는 것만으로는 AU3을 보장하지 못한다.

---

## 3. R1의 감사 대상 액션

R1에는 쓰기 액션이 많지 않다. **전수를 여기 열거하고, 목록에 없는 쓰기 API는 만들지 않는다.**

| `action_type` | 트리거 | `target_kind` | 필요 역할 |
|---|---|---|---|
| `QUERY_KILL` | FR-QL-04 쿼리 kill | `query` | operator, admin |
| `HEALTH_TEST_TOGGLE` | FR-CH-03 개별 테스트 활성/비활성 | `health_test` | admin |
| `HEALTH_ROLLUP_TOGGLE` | FR-CH-04 roll-up 비활성화 | `cluster` | admin |
| `HEALTH_THRESHOLD_CHANGE` | FR-CH-05 임계값 변경 | `health_test` | admin |
| `AUDIT_EXPORT` | FR-AA-05 감사 로그 내보내기 | `cluster`(=`*`) | admin |

> **`AUDIT_EXPORT`도 감사한다.** 누가 감사 기록을 통째로 꺼냈는지가 남지 않으면 감사 체계가 아니다.
> **R1에 없는 것**: 클러스터 재시작, 워커 축소, 설정 변경, 카탈로그 조작 — 전부 R3/R4다. 이 목록에 추가하려면 요구사항 변경 승인이 필요하다.

---

## 4. 강제 방식 (미들웨어)

**개별 핸들러에서 `reason`을 검사하지 않는다.** 빠뜨릴 수 있기 때문이다. 미들웨어가 강제한다.

```
쓰기 라우트로 표시된 요청
  │
  ├─ 1. 인증 확인 ─────────── 실패 → 401
  ├─ 2. 역할 검사 (§3 표) ─── 실패 → 403 (감사 기록: FAILURE)
  ├─ 3. reason 검증 ───────── 없음/공백 → 400 (AU2)
  ├─ 4. 감사 레코드 선기록 (outcome=PENDING 아님 — §4-1 참조)
  ├─ 5. 액션 실행
  └─ 6. 결과로 감사 레코드 확정 (SUCCESS / FAILURE + error_message)
```

### 4-1. 감사 기록 시점 — 선기록 vs 후기록

**채택: 트랜잭션 후기록 + 실패 시에도 기록.**

- 액션 실행 **전에** DB 쓰기 가능 여부를 확인한다(연결 확보). 불가하면 **액션을 시작조차 하지 않고 503** (AU1).
- 액션 실행 **후에** 결과와 함께 한 번에 커밋한다.
- 이유: `PENDING` 중간 상태를 만들면 프로세스가 죽었을 때 영원히 `PENDING`인 레코드가 남는다. append-only 테이블에서는 그것을 정리할 수단이 없다(AU3).
- **트레이드오프**: 액션 실행 직후 TMS가 죽으면 "실행됐지만 기록 없음"이 가능하다. **완전히 없앨 수 없는 창(window)** 이며, `request_id` 를 Trino 호출에 함께 남겨(kill 사유 문자열에 포함) 사후 대조 가능하게 한다.

### 4-2. `reason` 이 Trino까지 전달되는 경로 (FR-QL-04)

`TRINO_VERIFIED.md` §T1-5에서 확정된 대로 `PUT /v1/query/{queryId}/killed` 는 **본문을 실패 메시지로 사용**한다.

```
본문 = "Killed by TMS. actor={actor}, reason={reason}, request_id={request_id}"
```

→ 쿼리를 실행하던 **사용자에게 반환되는 오류 메시지에 사유가 그대로 표시된다.** 운영자가 왜 죽였는지 사용자가 즉시 안다. `DELETE /v1/query/{id}`(취소)는 이 슬롯이 없어 채택하지 않는다.

> **주의**: `reason` 은 최종 사용자에게 노출된다. UI에 **"이 사유는 쿼리 실행자에게 표시됩니다"** 를 명시한다. 내부 전용 메모로 오해하면 안 된다.
> 길이 상한(기본 512자)을 두고 개행을 제거한다.

---

## 5. 조회 및 내보내기 (FR-AA-05)

| 항목 | 내용 |
|---|---|
| 검색 조건 | 기간, `actor`, `action_type`, `target_kind`/`target_id`, `outcome` |
| 정렬 | `occurred_at DESC` 고정 |
| 페이징 | keyset 페이징 (`occurred_at`, `id`) — OFFSET은 깊은 페이지에서 느려진다 |
| 내보내기 | CSV. **`AUDIT_EXPORT` 로 감사 기록** |
| 권한 | 조회 = operator 이상, 내보내기 = admin (`ARCHITECTURE.md` §6-1) |

---

## 6. 보존

| 항목 | 값 |
|---|---|
| 기본 보존 | **무기한** |
| 근거 | R1 감사 볼륨은 일 수백 건 수준이다. 용량 문제가 없는데 지우는 것은 감사 요건에 해롭다 |
| 재검토 | 통합 Bolt에서 쿼리 히스토리 보존 정책(FR-QH-07, 90일)과 함께 재검토. **감사 로그는 쿼리 로그보다 오래 보존하는 것이 일반적이다** |

> 헬스 이벤트(`health_event`, `HEALTH_TESTS.md` §5)는 별도 테이블이며 볼륨이 더 크다. **보존 정책을 감사 로그와 분리**한다 (기본 1년 제안, Bolt 2에서 확정).

---

## 7. `reviewer` 체크 항목

- [ ] 쓰기 라우트 전수가 감사 미들웨어를 통과하는가 (개별 핸들러 검사 금지)
- [ ] `reason` 누락 요청이 400을 받는가 (공백 문자열 포함)
- [ ] 코드에 `audit_action` 대상 UPDATE/DELETE가 없는가
- [ ] 마이그레이션에 `REVOKE UPDATE, DELETE` 가 포함됐는가
- [ ] 실패한 액션도 기록되는가 (403 포함)
- [ ] 기록 주체가 서비스 계정이 아니라 실제 요청자인가
- [ ] DB 불가 시 쓰기 액션이 503으로 거부되는가 (우회로 부재)
