# 통합 스모크 테스트

`tests/` 의 단위 테스트와 달리 **실제 PostgreSQL과 실제 FastAPI 앱**을 사용한다.
의존성(`psycopg`, `fastapi`, `httpx`)이 설치된 환경에서만 동작하므로 기본 테스트 스위트에 포함되지 않는다.

이 계층이 존재하는 이유는 단순하다 — **단위 테스트가 잡지 못한 버그를 실제로 잡았기 때문이다.**
`slide_session` 미들웨어가 비밀번호 변경 직후 발급된 세션 쿠키를 요청 시점의 옛 claims 로 덮어써서,
비밀번호를 바꿔도 `must_change_password` 게이트에 영원히 갇히는 문제가 있었다.
주입 기반 단위 테스트로는 재현되지 않는다. ASGI 미들웨어 순서가 원인이기 때문이다.

---

## 준비

```bash
# 1) 격리된 PostgreSQL (기존 인스턴스를 건드리지 않는다)
export PGDATA=/tmp/tms-testpg
export PGSOCK=/tmp/tmspg
mkdir -p "$PGSOCK"
initdb -D "$PGDATA" -U postgres --auth=trust -E UTF8 --locale=C
pg_ctl -D "$PGDATA" -o "-p 5433 -k $PGSOCK -c listen_addresses=127.0.0.1" -l /tmp/tms-pg.log start

# 2) 역할과 스키마 — docs/runbooks/db-setup.md 와 동일한 절차
export PGHOST=127.0.0.1 PGPORT=5433
psql -U postgres -d postgres <<'SQL'
CREATE ROLE tms_owner LOGIN PASSWORD 'owner_pw';
CREATE ROLE tms_app   LOGIN PASSWORD 'app_pw';
CREATE DATABASE tms OWNER tms_owner ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;
SQL
psql -U postgres -d tms -c "ALTER SCHEMA public OWNER TO tms_owner; REVOKE CREATE ON SCHEMA public FROM PUBLIC;"
PGPASSWORD=owner_pw psql -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/001_init.sql
PGPASSWORD=owner_pw psql -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/002_grants.sql

# 3) 런타임 의존성
python3 -m venv /tmp/tms-venv
/tmp/tms-venv/bin/pip install "psycopg[binary]" fastapi "uvicorn[standard]" httpx
```

## 실행

```bash
/tmp/tms-venv/bin/python tests/integration/smoke_api_postgres.py
```

`실패 0건` 이 나와야 한다. 종료 코드가 곧 결과다.

## 검증 범위

| 영역 | 확인 내용 |
|---|---|
| 인증 | 미인증 401, 오답 401, 로그인 성공, 세션 쿠키 발급 |
| 임시 비밀번호 | 변경 전 다른 API 403, 약한 비밀번호 400, 변경 후 정상 접근 |
| 조회 | `/me` capabilities, 링크 허브, 클러스터 roll-up, 헬스 advice, 쿼리 목록 + 딥링크, stale 판정 |
| 쓰기 | 빈 `reason` 400 **이면서 kill 미실행**, 정상 kill, 사유가 Trino 로 전달 |
| 감사 | kill 기록, export 성공, **export 자체가 감사됨** |
| 로그아웃 | 쿠키 삭제 후 401 |

Trino 는 스텁이다. 실제 Trino 연동 검증은 `scripts/verify_connectivity.py` 가 담당한다.

## 정리

```bash
pg_ctl -D "$PGDATA" stop
rm -rf "$PGDATA" /tmp/tms-venv "$PGSOCK"
```
