# 런북 — `tms-svc` 에 `ExecuteQuery` 부여 (D-012)

> **근거**: `DECISIONS.md` D-012 · `ARCHITECTURE.md` §1-1(A1 개정) · `TRINO_VERIFIED.md` §T1-2-1
> **여는 것**: FR-FL-02(미조인 워커 이름으로 식별), FR-BM-01(표준 쿼리 세트 실행)

---

## ⛔ 먼저 확인할 것 — 이 사실이 결정을 뒤집는다

`ExecuteQuery` 는 "쿼리를 실행할 수 있다" 이지 "`system.runtime` 만 읽을 수 있다" 가 아니다. **실행 후 무엇을 읽을 수 있는지는 OPA 의 카탈로그 규칙이 정한다.**

```bash
# rego 에서 tms-svc 가 catalog / schema / table 규칙에 매칭되는가?
grep -n -A5 '"catalogs"\|"schemas"\|"tables"' <rules.json 경로>
```

| 결과 | 판단 |
|---|---|
| `tms-svc` 가 어느 카탈로그 규칙에도 매칭되지 않음 | ✅ **부여한다.** 도달 범위가 `system.*` 로 한정되고, TMS 침해 시 파급이 사실상 늘지 않는다 |
| catch-all 로 카탈로그가 열려 있음 | ⛔ **부여하지 않는다.** `ExecuteQuery` = 보이는 모든 테이블 읽기가 된다 |

두 번째라면 D-012 의 근거가 성립하지 않는다. **부여 자체를 미루거나, `tms-svc` 를 카탈로그 규칙에서 명시적으로 배제한 뒤 부여한다.**

---

## 변경 — 한 단어다

`queries` 규칙의 `tms-svc` 항목에 `"execute"` 를 더한다. 위치는 **catch-all 위**여야 한다 (먼저 매칭되는 규칙이 이긴다).

```jsonc
{
  "queries": [
    { "user": "tms-svc",            "allow": ["execute", "view", "kill"] },  // ← execute 추가
    { "user": "prometheus_scraper", "allow": [] },
    { "allow": ["execute", "view", "kill"] }
  ]
}
```

`system_information` 은 손대지 않는다 — 이미 `read` 를 갖고 있고 이 변경과 무관하다.

**배포**: 이 파일은 플랫폼팀 Git 관리이며 코디네이터의 OPA 가 읽는다. 워커에는 필요 없다 — `system.runtime.nodes` 는 코디네이터가 답한다.

---

## 확인

```bash
# 부여 전이면 PERMISSION_DENIED, 부여 후면 노드 목록이 나온다
curl -sk -u "$TMS_USER:$TMS_PW" -X POST \
  --data 'SELECT node_id, state FROM system.runtime.nodes' \
  https://<코디네이터>:8443/v1/statement | jq '.error.errorName // .columns'
```

TMS 화면에서는 **Fleet → "Ask the coordinator which node is missing"** 이 나타난다. 단 이 버튼은 **코디네이터가 보는 노드 수가 인벤토리보다 적을 때만** 뜬다 — 정상일 때는 보이지 않는 것이 정상이다.

---

## ⛔ 이 권한이 유지되는 조건

D-012 는 **쿼리 수가 0 에 가깝게 유지된다**는 전제로 성립한다. A1 이 SQL 을 막았던 세 가지 비용은 전부 빈도의 함수였다.

| 지켜지는 방식 | 무엇 |
|---|---|
| `clients/sql.py` | SQL 이 TMS 를 떠나는 **유일한 지점** |
| `tests/test_sql_isolation.py` | `tms.collector.*` 가 그 모듈을 import 하면 **CI 실패** |
| 화면 | 개수가 어긋날 때만 조회를 제안한다 |

**가드 테스트가 실패하면 allowlist 에 넣지 마라.** 물어야 할 것은 "이 호출자가 타이머 위에 있는가" 다. 그렇다면 D-012 의 근거가 그 호출을 덮지 못하므로 **결정을 다시 여는 것**이다.

---

## 되돌리기

`"execute"` 를 지우면 된다. 코드는 권한이 없는 상태를 이미 처리한다 — 조회가 `PERMISSION_DENIED` 로 실패하고, 화면은 **"이 권한이 필요하다"** 를 그대로 말한다. TMS 재시작도, 설정 변경도 필요 없다.
