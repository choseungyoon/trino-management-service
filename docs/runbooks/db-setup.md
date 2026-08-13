# RUNBOOK — TMS PostgreSQL 초기 구축

**대상**: D-004로 확정된 **TMS 전용 PostgreSQL 인스턴스**
**수행**: 플랫폼팀 (사내망 DB이므로 직접 실행)
**소요**: 약 10분

> **이 문서의 위치**: 전체 배포는 [`deploy.md`](deploy.md) 가 주관하고, 이 런북은 그 §3(DB 구축)에 해당한다.
> **이 문서는 §6 에서 끝난다.** 서비스 기동(collector/api)은 코드·설정·systemd 유닛이 모두 갖춰진 뒤의 일이라 `deploy.md` §9 소관이다.
>
> ```
> deploy.md §1~2  코드 + venv
> deploy.md §3    ← 이 문서 (§0~§6)
> deploy.md §4~8  Trino 권한 · 연결 검증 · 설정 · 계정
> deploy.md §9    collector / api 기동
> ```

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

\connect tms

-- public 스키마 소유권을 tms_owner 에게 넘긴다.
-- ⚠️ 이 줄을 빼면 002_grants.sql 의 `GRANT USAGE ON SCHEMA public` 이
--    "WARNING: no privileges were granted for public" 만 남기고 조용히 실패한다.
--    tms_owner 가 자기 소유가 아닌 스키마의 권한을 재부여할 수 없기 때문이다.
--    PG14 에서는 PUBLIC 의사 역할이 기본 USAGE 를 갖고 있어 우연히 동작하지만,
--    보안 강화로 `REVOKE ALL ON SCHEMA public FROM PUBLIC` 을 적용하는 순간
--    tms_app 이 모든 테이블에 접근하지 못한다. (2026-08-06 로컬 재현 확인)
ALTER SCHEMA public OWNER TO tms_owner;

-- public 스키마에 아무나 테이블을 만들지 못하게 한다.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
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

## 7. collector 기동 — **이 문서에서 하지 않는다**

> **⛔ 여기서 `systemctl enable --now tms-collector` 를 실행하지 마라. 실패한다.**
>
> DB 만 준비된 상태에서는 성공할 수 없다. collector 기동에는 아래가 **전부** 필요하다.
>
> | 필요한 것 | 준비하는 곳 |
> |---|---|
> | `/etc/trino-management-service` 코드 + `venv` (`tms-collector` 실행 파일) | `deploy.md` §1~2 |
> | `config.yaml` 의 실제 클러스터 주소 | `deploy.md` §6-1 |
> | `/etc/tms/tms.env` (DB URL·Trino 비밀번호·세션 키) | `deploy.md` §6-2 |
> | Trino `rules.json` 권한 | `deploy.md` §4 |
> | `/etc/systemd/system/tms-collector.service` | `deploy.md` §9 |
>
> **DB 구축은 §6 에서 끝난다.** 이어서 `deploy.md` 로 가라. 그 문서가 이 런북을 §3 에서 호출하고, collector 기동은 §9-1 에서 다룬다.

**→ 다음: [`deploy.md`](deploy.md) §4 부터 계속**

(이 런북만 단독으로 수행 중이라면, DB 쪽 할 일은 §6 으로 완료다.)

---

## 8. 롤백

```sql
-- 개발/스테이징 한정. 프로덕션 감사 로그는 지우지 않는다.
DROP TABLE IF EXISTS health_test_override, collector_snapshot, health_event, audit_action;
```

> **프로덕션에서 `audit_action`을 DROP 하지 않는다.** 스키마 변경이 필요하면 새 마이그레이션으로 컬럼을 추가한다. FR-AA-04는 데이터 보존을 요구한다.
