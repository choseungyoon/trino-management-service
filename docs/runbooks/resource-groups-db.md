# 런북 — 리소스 그룹을 file 에서 db 매니저로 옮긴다 (D-010)

> **한 번에 한 클러스터씩 한다.** Gateway 가 두 클러스터로 라우팅하므로, `cluster1` 을 완전히 끝내고 검증한 뒤 `cluster2` 에 들어간다. 중간에 잘못돼도 나머지 한 대가 서비스를 받는다.
>
> **근거**: `DECISIONS.md` D-010 · `TRINO_VERIFIED.md` §T1-4-1 (로컬 477 실측)
> **설정값**: `docs/templates/resource-group.json` (읽는 용도) · `docs/templates/resource-groups-db.sql` (실제 적재)

---

## 이 작업이 바꾸는 것

| | 지금 | 이후 |
|---|---|---|
| 리소스 그룹 값 변경 | 코디네이터 재시작 | **`UPDATE` → 10초 반영** |
| 메모리 상한 | `query.max-memory=4016GB` = **사실상 없음** | 900GB (클러스터의 24%) |
| 힙 | 250GB (RAM 560GB 중) | 400GB |
| 새 제약 | — | **리소스 그룹 DB 정지 중에는 코디네이터를 재시작할 수 없다** |

마지막 줄이 이 작업의 대가다. 돌고 있는 코디네이터는 DB 가 사라져도 멀쩡하지만(실측 확인), **기동은 못 한다.** TMS 는 재시작 4단계 진입 전에 이걸 확인해 막는다.

---

## 0. 시작 전에 모아 둘 값

```bash
# 각 클러스터 코디네이터에서 — 이 값이 DB 행의 environment 가 된다
grep node.environment /etc/trino/node.properties        # → cluster1 / cluster2

# 현재 메모리 설정 (되돌릴 때 필요하다)
grep -E 'query.max-memory|heap-headroom' /etc/trino/config.properties
grep Xmx /etc/trino/jvm.config

# 코디네이터가 워커로도 쓰이는지 — true 면 아래 메모리 계산이 달라진다
grep node-scheduler.include-coordinator /etc/trino/config.properties
```

| 필요한 것 | 값 |
|---|---|
| TMS PostgreSQL 접속 정보 | `config.secret.yaml` 의 `database.url` 과 같은 인스턴스 |
| admin 계정 | `datalake.admin` |
| `node.environment` | `cluster1`, `cluster2` |

> **⚠️ `config.properties` / `jvm.config` 원본을 먼저 백업한다.** 되돌릴 유일한 수단이다.

---

## 1. DB 준비 (한 번만, 두 클러스터 공통)

TMS 데이터베이스에 **전용 schema 와 전용 계정**을 만든다. TMS 애플리케이션 테이블과 같은 database 지만 schema 가 다르다 — TMS 마이그레이션이 Trino 의 쿼리 수용 테이블에 닿을 경로를 없애기 위해서다.

```sql
-- TMS 데이터베이스에 접속해서
CREATE SCHEMA trino_resource_groups;

CREATE ROLE trino_rg WITH LOGIN PASSWORD '<생성한 비밀번호>';
GRANT USAGE, CREATE ON SCHEMA trino_resource_groups TO trino_rg;
```

`CREATE` 권한은 **2단계에서 Trino 가 테이블을 자동 생성하기 위해** 필요하다. 4단계에서 회수한다.

> **TMS 마이그레이션 계정에는 이 schema 권한을 주지 않는다.** 격리의 실체가 그것이다.

---

## 2. 첫 클러스터 — 설정 변경

**아직 재시작하지 않는다.** 파일만 바꾼다.

### 2-1. `etc/resource-groups.properties` (코디네이터만)

```properties
resource-groups.configuration-manager=db
resource-groups.config-db-url=jdbc:postgresql://<TMS DB host>:5432/<db>?currentSchema=trino_resource_groups
resource-groups.config-db-user=trino_rg
resource-groups.config-db-password=<위에서 만든 비밀번호>
resource-groups.refresh-interval=10s
resource-groups.max-refresh-interval=24h
```

```bash
chmod 600 /etc/trino/resource-groups.properties
chown trino:trino /etc/trino/resource-groups.properties
```

기존 `resource-groups.config-file` 줄은 **지운다.** 남겨두면 어느 쪽이 이기는지 헷갈린다.

| 값 | 왜 |
|---|---|
| `refresh-interval=10s` | 기본 `1s` 는 DB 장애 시 **초당 1건 + 스택 트레이스**를 남긴다. 하루 장애면 코디네이터당 8.6만 건. 10초로도 재시작 없는 반영이라는 목적은 그대로 달성된다 |
| `max-refresh-interval=24h` | 기본 `1h`. DB 재기동·마이그레이션 창을 코디네이터가 아예 인지하지 못하게 한다 |
| `?currentSchema=` | Trino 477 이 이 값을 존중해 지정 schema 에 테이블을 만드는 것을 실측 확인했다 |

### 2-2. `config.properties` (코디네이터 + 워커 전부)

```properties
query.max-memory-per-node=270GB
query.max-memory=900GB
memory.heap-headroom-per-node=60GB
```

`query.max-total-memory` 줄이 있으면 **지운다.** 자동으로 `query.max-memory × 2` = 1,800GB 가 된다.

### 2-3. `jvm.config` (코디네이터 + 워커 전부)

```
-Xmx400G
```

> **코디네이터 힙은 250G 로 둔다.** 코디네이터는 쿼리 메모리 풀에 기여하지 않는다(`node-scheduler.include-coordinator=false` 기준). 위 `query.*` 값은 코디네이터 `config.properties` 에도 동일하게 넣는다 — 값의 출처는 코디네이터다.

**검증**: `query.max-memory-per-node + memory.heap-headroom-per-node < 최대 힙` → `270 + 60 = 330GB < 400GB` ✅

---

## 3. 첫 클러스터 — 안전 시퀀스 재시작

**TMS 화면에서 진행한다.** 절대규칙 5 의 시퀀스를 건너뛰는 경로는 없다.

> **⚠️ 이번 최초 전환에서만 — TMS `config.yaml` 의 `resource_groups.enabled` 를 `false` 로 둔다.**
>
> TMS 는 재시작 전에 "이 클러스터의 행이 DB 에 있는가"를 확인해 없으면 막는다(D-010). 그런데 **테이블 자체가 아직 없다** — Trino 가 첫 기동 때 만든다. 닭과 달걀이라, 최초 전환에서는 이 확인을 꺼 두고 6단계에서 켠다.

1. TMS → 해당 클러스터 → **Restart** → 사유 입력 → **Begin the restart sequence**
   → Gateway 에서 이 클러스터가 비활성화된다. **여기서부터 이 클러스터에 새 쿼리가 안 들어간다.**
2. 실행 중 쿼리가 0 이 될 때까지 대기 (화면이 카운트를 보여준다)
3. **Restart** 버튼 → 재시작 수행 (`manual` 모드면 직접, `ansible` 모드면 TMS 가 실행)
4. **코디네이터가 올라오면 테이블 4개가 자동 생성된다.** 확인:

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'trino_resource_groups';
-- resource_groups / selectors / resource_groups_global_properties
-- / exact_match_source_selectors
```

> **코디네이터가 안 뜨면 여기서 멈춘다.** 로그에 `Unable to obtain connection from database` 가 있으면 접속 정보 문제다. `resource-groups.properties` 를 백업본으로 되돌리고 다시 기동하면 원상복구된다 — 아직 DB 에 아무것도 없으므로 잃는 것이 없다.

---

## 4. 행 적재

**아직 트래픽을 복구하지 않은 상태에서** 한다. 이 클러스터는 Gateway 에서 빠져 있으므로 쿼리가 없다.

```bash
psql -h <TMS DB host> -U <관리 계정> -d <db> \
     -v env=cluster1 -f docs/templates/resource-groups-db.sql
```

스크립트 끝의 검증 SELECT 두 개가 그 자리에서 결과를 보여준다. **그룹 3개 + 셀렉터 2개**가 나와야 한다.

> **⚠️ 이 스크립트는 멱등하지 않다.** Trino 스키마에 `(name, parent, environment)` 유니크 제약이 없어서 두 번 실행하면 중복 트리가 조용히 생긴다. 중복이 의심되면:
> ```sql
> SELECT environment, count(*) FROM trino_resource_groups.resource_groups
> GROUP BY environment;
> ```

**SQL 파일의 플레이스홀더 하나를 먼저 채운다** — `user_regex` 의 admin 계정. 현재 `^datalake\.admin$` 로 되어 있으니 그대로면 수정 불필요하다.

적재 후 **최대 10초** 뒤 코디네이터가 자동으로 읽는다. 재시작하지 않는다.

---

## 5. 첫 클러스터 — 검증 후 트래픽 복구

TMS 시퀀스 화면에서 헬스가 `GOOD` 인지 확인하고 **Restore traffic** 을 누른다. 그 전에:

```bash
# 리소스 그룹이 실제로 적용됐는지 — 쿼리를 하나 흘린 뒤
curl -s -u "$USER:$PW" 'https://<코디네이터>:8443/v1/jmx/mbean' \
  | jq -r '.[].objectName' | grep InternalResourceGroup
```

`type=InternalResourceGroup,name=global` 과 `name=admin` 이 보여야 한다.
**`global.<사용자명>` 은 안 보이는 게 정상이다** — 사용자별 리프에는 `jmxExport` 를 주지 않았다.

트래픽 복구 후 TMS **워크로드 화면**에 `global` 이 뜨는지 확인한다.

---

## 6. TMS 의 재시작 게이트 켜기

행이 적재된 지금 켠다. `config.yaml`:

```yaml
resource_groups:
  enabled: true
  schema: trino_resource_groups

clusters:
  - name: <TMS 에 등록된 클러스터 이름>
    node_environment: cluster1     # ← 코디네이터의 node.environment 와 정확히 일치
```

```bash
systemctl restart tms-api
python scripts/verify_connectivity.py    # node_environment 가 OK 로 나오는지 확인
```

이후 TMS 는 재시작 4단계 진입 전에 이 클러스터의 행이 DB 에 있는지 확인하고, **없거나 DB 가 안 닿으면 버튼을 막는다.** 화면에 이유가 뜬다.

---

## 7. 두 번째 클러스터

1번(DB 준비)은 이미 끝났다. **2 → 3 → 4 → 5 를 `cluster2` 로 반복**한다. 차이는 두 가지뿐이다:

- 4단계에서 `-v env=cluster2`
- 3단계의 `resource_groups.enabled` 를 다시 끌 필요 **없다** — 게이트는 클러스터별 `node_environment` 로 판단하고, `cluster2` 는 아직 행이 없으니 막힌다. **최초 전환 동안만 다시 `false` 로 내렸다가 6단계에서 올린다.**

두 클러스터가 끝나면 `config.yaml` 의 두 번째 클러스터에도 `node_environment: cluster2` 를 넣고 `tms-api` 를 재시작한다.

---

## 7-1. TMS 편집 화면 켜기 (FR-WL-07~10)

여기까지 하면 **`psql` 없이 TMS 화면에서 값을 고칠 수 있다.** 필요한 것은 마이그레이션 두 개와 권한뿐이다.

```bash
# 1) 리비전 테이블과 감사 액션 타입 (010), 권한 (011)
cd /etc/trino-management-service
sudo -u trino-gateway git pull
psql -h <TMS DB host> -U <소유자> -d <db> -f migrations/010_resource_group_revision.sql
psql -h <TMS DB host> -U <소유자> -d <db> -f migrations/011_resource_group_grants.sql

# 2) TMS 애플리케이션 계정에 Trino 의 schema 접근 권한
psql -h <TMS DB host> -U <소유자> -d <db> <<'SQL'
GRANT USAGE ON SCHEMA trino_resource_groups TO tms_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA trino_resource_groups TO tms_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA trino_resource_groups TO tms_app;
SQL

sudo systemctl restart tms-api
sudo -u trino-gateway /etc/trino-management-service/venv/bin/tms-config-check
```

> **⛔ 별도 쓰기 계정(`tms_rg_writer`)을 만들지 않는다.** 설계 단계에서는 그 편이 나아 보였지만(D-010 H-1), 구현하면서 **원자성과 맞바꾸는 거래**라는 것이 드러났다. 두 계정은 두 연결이고, 두 연결은 한 트랜잭션이 될 수 없다. 같은 연결을 쓰면 **변경과 리비전 스냅샷이 함께 커밋되거나 함께 실패한다** — 스냅샷 없는 변경이 생길 수 없다. 감사 기록은 다른 모든 쓰기 액션과 동일하게 감사 선행 + outcome 기록으로 남는다.

**`group_provider_configured`** 도 함께 확인한다. `etc/group-provider.properties` 가 생기기 전까지는 `false` 로 둔다 — 그래야 `user_group_regex` 셀렉터를 만들 때 "이 규칙은 매칭되지 않는다"고 경고한다.

```yaml
resource_groups:
  enabled: true
  schema: trino_resource_groups
  group_provider_configured: false
```

### 화면에서 할 수 있는 것

| | |
|---|---|
| **조회** | 설정된 전체 트리 + 실행 중 상태 대조. `viewer` 도 볼 수 있다 |
| **값 수정** | 행 인라인 편집. `reason` 필수, **admin 한정** |
| **추가/삭제** | 그룹·셀렉터. 삭제는 CASCADE 파급을 목록으로 먼저 보여준다 |
| **이력/되돌리기** | 누가·왜 바꿨는지. 되돌리기는 트리 전체 복원이며 **이력을 지우지 않고 덧붙인다** |

### 저장이 거부되는 경우

검증 규칙 전량은 `DESIGN_WL07.md` §4 에 있다. 실무에서 자주 걸리는 것:

- **`hard_concurrency_limit: 0`** — Trino 는 받지만 그 그룹이 아무것도 실행하지 않게 된다. 튜닝 값의 탈을 쓴 삭제라 거부한다
- **catch-all 셀렉터가 없어지는 변경** — 477 문서가 미매칭 쿼리의 동작을 규정하지 않으므로 그 상태에 도달할 수 없다. 마지막 catch-all 에는 삭제 버튼이 아예 없다
- **형제 그룹 이름 중복** — DB 에 유니크 제약이 없어 중복 트리가 조용히 생긴다

경고(저장은 됨)로 나오는 것: 스캔·CPU 쿼터 추가, `jmx_export` 끄기, group provider 없는 `user_group_regex`, 형제 메모리 합 초과.

---

## 7-2. 메모리 상한만 따로 바꾸기 (db 전환 이후)

§2-2 는 메모리 변경을 db 전환에 묶어 두었다. 전환이 끝난 뒤 메모리만 손대는 경우는 이쪽이다.

> **✅ 2026-08-15 적용 완료.** 아래는 되돌리거나 값을 다시 조정할 때 읽는다.

**고쳤던 이유**: `query.max-memory=4016GB` 는 **클러스터 총량보다 커서 절대 발동하지 않는다.** 쿼리 하나가 클러스터 전체를 먹어도 아무것도 막지 않는다. 문서상 이 값은 초과 쿼리를 *죽이는* 유일한 장치인데, 그게 꺼져 있는 상태다.

### ⛔ 힙을 함께 바꾸는지에 따라 값이 다르다

두 설정은 제약으로 묶여 있다 — `query.max-memory-per-node + memory.heap-headroom-per-node < 최대 힙`.

| | **A. 힙 유지 (`-Xmx 250G`)** | **B. 힙도 400G 로** |
|---|---|---|
| 워커 쿼리 풀 | 220GB | 340GB |
| **클러스터 총량** | **2,420GB** | **3,740GB** |
| `query.max-memory-per-node` | **176GB 유지** — 270GB 는 못 넣는다 (270+30 > 250) | `270GB` |
| `memory.heap-headroom-per-node` | 30GB 유지 | `60GB` |
| `query.max-memory` **600GB** | 클러스터의 **25%** ← 권장 | — |
| `query.max-memory` **900GB** | 클러스터의 **37%** | 클러스터의 **24%** ← 권장 |
| `jvm.config` 변경 | 없음 | 있음 |
| 재시작 | 필요 (전 노드) | 필요 (전 노드) |

**A 에서 900GB 를 넣어도 동작한다.** 상한이 없던 것에 비하면 큰 개선이고, 나중에 힙을 400G 로 올릴 때 그대로 맞는 값이 된다. 다만 그때까지는 쿼리 하나가 클러스터의 **1/3 이상**을 쓸 수 있다는 뜻이다. **힙 변경 계획이 없다면 600GB 를 권한다.**

> **⛔ A 에서 `query.max-memory-per-node` 를 270GB 로 올리지 마라.** `270 + 30 = 300GB > 250GB` 라 제약 위반이고, **코디네이터가 기동하지 않는다.**

### 절차 (A 기준)

```bash
# 0) 현재 값과 백업
grep -E 'query.max-memory|heap-headroom' /etc/trino/config.properties
sudo cp /etc/trino/config.properties /etc/trino/config.properties.bak-$(date +%F)

# 1) 전 노드(코디네이터 + 워커 11)의 config.properties
#    query.max-memory=600GB          (또는 900GB)
#    query.max-memory-per-node       ← 손대지 않는다 (176GB 유지)
#    query.max-total-memory 줄이 있으면 지운다 → 자동으로 max-memory × 2
```

**2) TMS 안전 시퀀스로 클러스터당 1회 재시작.** 한 번에 한 클러스터씩이고, `cluster1` 을 복구·검증한 뒤 `cluster2` 로 간다.

> **이제 재시작 전에 TMS 가 리소스 그룹 저장소를 확인한다** (D-010 완화 1). 저장소가 안 닿거나 그 클러스터 행이 없으면 4단계 버튼이 막힌다 — db 매니저를 쓰는 코디네이터는 저장소 없이 기동하지 못하기 때문이다. 화면에 이유가 뜬다.

**3) 검증** — 재시작 후 값이 실제로 들어갔는지 본다:

```bash
grep query.max-memory /etc/trino/config.properties
# 그리고 쿼리 하나를 흘려 정상 동작 확인
```

### 되돌리기

`config.properties.bak-*` 복원 후 **안전 시퀀스로** 재시작. 이 변경은 상한을 *좁히는* 것이므로, 되돌릴 이유는 대개 "정상 배치가 상한에 걸려 죽는다"이다. 그 경우 되돌리기보다 **값을 올리는 쪽**이 맞다 — 상한이 없는 상태로 돌아가지 마라.

---

## 8. 이후의 값 변경 — 재시작 없음

```sql
UPDATE trino_resource_groups.resource_groups
   SET hard_concurrency_limit = 12
 WHERE name = '${USER}' AND environment = 'cluster1';
```

10초 뒤 반영된다. **`environment` 조건을 빼지 마라** — 두 클러스터가 같은 테이블을 공유한다.

> **⛔ `DELETE` 는 보이는 것보다 파급이 크다.** `resource_groups.parent` 와 `selectors.resource_group_id` 양쪽에 `ON DELETE CASCADE` 가 걸려 있다. **`global` 한 줄을 지우면 하위 그룹과 셀렉터가 전부 사라지고, 그 클러스터는 10초 뒤 리소스 그룹이 없는 상태가 된다.**

---

## 되돌리기

| 언제 | 어떻게 |
|---|---|
| 3단계에서 코디네이터가 안 뜬다 | `resource-groups.properties` 를 백업본(file 매니저)으로 되돌리고 기동. DB 에 아무것도 안 남는다 |
| 4단계 이후 리소스 그룹이 이상하다 | 값만 `UPDATE` 로 고친다. 재시작 불필요 |
| db 매니저 자체를 물린다 | `resource-groups.properties` 를 file 매니저로 되돌리고 **안전 시퀀스로** 재시작. DB 행은 남겨 둬도 무해하다 |
| 메모리 설정을 물린다 | `config.properties` · `jvm.config` 백업본 복원 후 **안전 시퀀스로** 재시작 |

---

## 장애 시 — 리소스 그룹 DB 가 죽었다

| 상황 | 어떻게 되나 |
|---|---|
| 코디네이터가 **돌고 있다** | **정상 동작한다.** 기존·신규 사용자 모두 쿼리가 된다. 로그에 `Error loading configuration from db` 가 10초마다 쌓일 뿐이다 |
| 코디네이터를 **재시작해야 한다** | ⛔ **하지 마라. 뜨지 않는다.** DB 복구가 먼저다. TMS 가 막아 주지만, TMS 를 거치지 않는 재시작은 막을 수 없다 |
| DB 복구 | **자동으로 회복한다.** Trino 재시작 불필요. 다만 **성공한 리프레시는 로그를 남기지 않으므로, 회복 신호는 "에러가 멈춘 것"이다** — 알림을 그렇게 짠다 |
