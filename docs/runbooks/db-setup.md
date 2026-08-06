# RUNBOOK — TMS PostgreSQL 초기 구축

**대상**: D-004로 확정된 **TMS 전용 PostgreSQL 인스턴스**
**수행**: 플랫폼팀 (사내망 DB이므로 직접 실행)
**소요**: 약 10분

> ⚠️ **Gateway용 PostgreSQL에 만들지 않는다.** Gateway DB는 queryId→backend 조회로 **쿼리 경로의 일부**다. TMS가 부하를 얹으면 NFR-ISOLATION 취지에 어긋난다 (D-004).

---

## 0. 준비

| 항목 | 값 |
|---|---|
| PostgreSQL 버전 | 12 이상 (`BIGSERIAL`, `JSONB`, `INET`, `TEXT[]`, 표현식 CHECK 사용) |
| DB 이름 | `tms` (예시) |
| 소유자 역할 | `tms_owner` — 스키마 소유. 애플리케이션이 쓰지 않는다 |
| 애플리케이션 역할 | `tms_app` — `tms-api` / `tms-collector` 가 접속 |

**역할을 둘로 나누는 이유**: 애플리케이션이 소유자면 `GRANT`/`REVOKE`가 무의미해진다. 소유자는 자기 테이블에 항상 전권을 갖기 때문에 **append-only 보장이 성립하지 않는다.**

---

## 1. 역할과 데이터베이스 생성

superuser로 실행한다.

```bash
sudo -u postgres psql
```

```sql
-- 비밀번호는 셸 이력에 남지 않도록 psql 안에서 입력한다.
CREATE ROLE tms_owner LOGIN PASSWORD '<owner-password>';
CREATE ROLE tms_app   LOGIN PASSWORD '<app-password>';

CREATE DATABASE tms OWNER tms_owner ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;

-- public 스키마에 아무나 테이블을 만들지 못하게 한다 (PG 15+ 기본값이지만 명시).
\connect tms
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT  CREATE, USAGE ON SCHEMA public TO tms_owner;
```

> `LC_COLLATE 'C'`: 감사 로그 검색은 사용자명·액션 타입 같은 ASCII 정형 값이 대부분이라 `C` 콜레이션이 인덱스 성능에 유리하다. 한국어 정렬이 필요하면 바꿔도 되지만, 그 경우 인덱스를 재생성해야 한다.

---

## 2. 스키마 적용 (001)

**소유자 역할로** 실행한다.

```bash
export PGPASSWORD='<owner-password>'
psql -h <db-host> -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/001_init.sql
unset PGPASSWORD
```

`ON_ERROR_STOP=1`이 없으면 중간 실패를 놓친다. **반드시 붙일 것.**

**확인**

```bash
psql -h <db-host> -U tms_owner -d tms -c "\dt"
```

기대 결과 — 4개 테이블:

```
 audit_action | health_event | collector_snapshot | health_test_override
```

---

## 3. 권한 적용 (002)

```bash
export PGPASSWORD='<owner-password>'
psql -h <db-host> -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/002_grants.sql
unset PGPASSWORD
```

DB 이름은 `current_database()` 로 스크립트가 직접 읽는다 — 별도 플래그가 필요 없다.

> 애플리케이션 역할 이름이 `tms_app`이 아니면 `002_grants.sql` 첫 줄의 `\set app_role` 을 바꾼다.

---

## 4. ⭐ append-only 보장 검증 (건너뛰지 말 것)

**이 검증이 실패하면 FR-AA-04가 지켜지지 않는 것이다.** 감사 로그를 고칠 수 있으면 감사 체계가 아니다.

```bash
export PGPASSWORD='<app-password>'
APP="psql -h <db-host> -U tms_app -d tms"

# 1) INSERT 는 성공해야 한다
$APP -c "INSERT INTO audit_action
         (actor, action_type, target_kind, target_id, reason, outcome, request_id)
         VALUES ('setup-check','QUERY_KILL','query','test','runbook verification',
                 'SUCCESS', gen_random_uuid());"

# 2) UPDATE 는 실패해야 한다
$APP -c "UPDATE audit_action SET reason = 'tampered';"

# 3) DELETE 는 실패해야 한다
$APP -c "DELETE FROM audit_action;"
unset PGPASSWORD
```

**기대 결과**

| 단계 | 기대 |
|---|---|
| 1 INSERT | `INSERT 0 1` |
| 2 UPDATE | `ERROR: permission denied for table audit_action` |
| 3 DELETE | `ERROR: permission denied for table audit_action` |

> **2번이나 3번이 성공하면 중단하고 §3을 다시 확인하라.** 흔한 원인은 `tms_app`이 DB 소유자이거나, 과거에 `GRANT ALL`을 받은 경우다.

**CHECK 제약도 함께 확인한다** — 빈 `reason`이 거부되어야 한다.

```bash
PGPASSWORD='<app-password>' psql -h <db-host> -U tms_app -d tms -c \
  "INSERT INTO audit_action
   (actor, action_type, target_kind, target_id, reason, outcome, request_id)
   VALUES ('x','QUERY_KILL','query','t','   ','SUCCESS', gen_random_uuid());"
```

기대: `ERROR: new row ... violates check constraint "audit_action_reason_not_blank"`

> `gen_random_uuid()`는 PostgreSQL 13+ 내장이다. 12라면 `CREATE EXTENSION IF NOT EXISTS pgcrypto;` 를 먼저 실행한다.

**검증용 행 정리** — 애플리케이션 역할로는 지울 수 없다(그게 정상이다). 소유자로 지운다.

```bash
PGPASSWORD='<owner-password>' psql -h <db-host> -U tms_owner -d tms -c \
  "DELETE FROM audit_action WHERE actor = 'setup-check';"
```

---

## 5. TMS에 접속 정보 등록

**저장소는 PUBLIC이다 (D-002). 자격증명을 커밋하지 않는다.**

방법 A — 시크릿 파일:

```bash
cp config/config.secret.yaml.example config/config.secret.yaml
chmod 600 config/config.secret.yaml
# database.url 을 채운다:
#   postgresql://tms_app:<app-password>@<db-host>:5432/tms
```

방법 B — systemd EnvironmentFile (**권장**):

```bash
sudo install -d -m 750 -o root -g tms /etc/tms
sudo tee /etc/tms/tms.env >/dev/null <<'EOF'
TMS_DATABASE_URL=postgresql://tms_app:<app-password>@<db-host>:5432/tms
TMS_TRINO_PASSWORD=<tms-svc-password>
EOF
sudo chmod 600 /etc/tms/tms.env
sudo chown root:tms /etc/tms/tms.env
```

환경변수가 시크릿 파일보다 우선한다. 다만 **빈 값은 덮어쓰지 않는다** — `EnvironmentFile`에 항목이 없어도 파일의 값이 살아남는다.

---

## 6. 접속 확인

```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, "src")
from tms.core.config import load_config
config = load_config("config/config.yaml")
print("clusters:", config.cluster_names)
print("database configured:", bool(config.database_url))   # 값은 마스킹되어 출력된다
PY
```

`Secret`은 `str()`/`repr()`에서 `***`를 반환하므로 이 출력에 비밀번호가 찍히지 않는다.

---

## 7. collector 기동 (첫 통합 확인 지점)

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tms-collector
sudo journalctl -u tms-collector -f
```

**정상 로그**

```
collector started for 2 cluster(s): prod-a, prod-b
```

**확인 쿼리** — 폴링이 실제로 스냅샷을 쓰는지:

```sql
SELECT cluster, kind, collected_at, collection_error,
       length(payload::text) AS payload_bytes
FROM collector_snapshot
ORDER BY cluster, kind;
```

| 증상 | 원인 | 조치 |
|---|---|---|
| `collection_error`에 `TrinoForbidden` | `rules.json` 권한 | `system_information:read` / `queries:view` 확인 |
| `collection_error`에 `MBeanNotRegistered` | MBean 이름 변경(버전업) | `GET /v1/jmx/mbean` 열거로 실제 이름 확인 |
| `collection_error`에 `filtered by access control` | **H-09 발동** — 목록이 조용히 비었다 | `queries:view` 확인. 조용한 실패를 잡은 것이므로 정상 동작 |
| `another tms-collector already holds the advisory lock` | 두 번째 인스턴스 | **정상 차단.** 중복 기동 여부 확인 |
| 행이 아예 없음 | 폴링 미도달 | `journalctl -u tms-collector` 확인 |

---

## 8. 롤백

```sql
-- 개발/스테이징 한정. 프로덕션 감사 로그는 지우지 않는다.
DROP TABLE IF EXISTS health_test_override, collector_snapshot, health_event, audit_action;
```

> **프로덕션에서 `audit_action`을 DROP 하지 않는다.** 스키마 변경이 필요하면 새 마이그레이션으로 컬럼을 추가한다. FR-AA-04는 데이터 보존을 요구한다.
