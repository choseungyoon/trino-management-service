# RUNBOOK — 사내 실환경 배포 (git pull → DB → 서비스 기동 → Trino 연결)

**대상**: 사내망 VM + systemd (K8s 미사용, 확정)
**수행**: 플랫폼팀
**소요**: 약 60~90분 (DB 구축 10분 포함)
**전제**: `docs/runbooks/db-setup.md`, `docs/runbooks/local-account-setup.md` 를 이 문서가 순서대로 호출한다. 두 문서를 따로 먼저 읽을 필요는 없다.

> **⛔ 저장소는 PUBLIC 이다 (D-002).** 자격증명·실제 호스트명·내부 IP 를 커밋하지 않는다. 이 문서의 `<...>` 자리는 전부 사내 값으로 채우되, **채운 결과를 git 에 올리지 않는다.** `config.yaml` 은 추적되는 파일이므로 클러스터 주소를 넣을 때 사내 정책을 먼저 확인하라 (§6-1 에 대안 있음).

---

## 0. 시작 전 — 준비물 체크리스트

이 값들이 없으면 중간에 막힌다. **먼저 다 모아라.**

| # | 필요한 것 | 확보처 | 없으면 |
|---|---|---|---|
| 0-1 | 배포 대상 VM (root/sudo) | 인프라팀 | — |
| 0-2 | Python **3.9 이상** | VM 기본 | §2 에서 확인 |
| 0-3 | Artifactory PyPI 프록시 URL | 사내 표준 | 외부 PyPI 직접 접근 불가 → 설치 불가 |
| 0-4 | **TMS 전용** PostgreSQL 12+ 인스턴스 | DBA | §3. **Gateway DB 를 쓰지 않는다** |
| 0-5 | Trino 코디네이터 URL 2개 (`https://호스트:포트`) | 운영팀 | — |
| 0-6 | Trino 서비스 계정 `tms-svc` + 비밀번호 | 운영팀 | §4 |
| 0-7 | `rules.json` 에 `tms-svc` 권한 2종 반영 | 운영팀 | §4. **이게 제일 자주 막힌다** |
| 0-8 | 사내 내부 CA 인증서 (PEM) | 보안팀 | §7 |
| 0-9 | TMS 앞단 HTTPS 종단 (LB 또는 nginx) | 인프라팀 | §10. **없으면 로그인이 안 된다** |
| 0-10 | 각 운영자 이름 목록 (계정 생성용) | — | §8 |

---

## 1. 코드 배치

```bash
sudo useradd --system --home-dir /opt/tms --shell /usr/sbin/nologin tms
sudo install -d -o tms -g tms -m 755 /opt/tms
# /var/log/tms 는 만들 필요 없다 - 유닛의 LogsDirectory= 로 systemd 가 만든다

sudo -u tms git clone https://github.com/choseungyoon/trino-management-service.git /opt/tms
cd /opt/tms
sudo -u tms git log --oneline -1     # 받은 리비전을 기록해 둘 것
```

이미 받아 둔 경우:

```bash
cd /opt/tms && sudo -u tms git pull
```

> **경로를 바꾸려면 systemd 유닛도 함께 고쳐야 한다.** `ops/systemd/*.service` 가 `/opt/tms`, `/opt/tms/venv/bin/`, `/opt/tms/config/config.yaml` 을 하드코딩하고 있다.

---

## 2. Python 가상환경 + 의존성 설치

```bash
python3 --version          # 3.9 이상이어야 한다
sudo -u tms python3 -m venv /opt/tms/venv
```

**Artifactory 경유로만 설치한다** (외부 PyPI 직접 접근 불가).

```bash
sudo -u tms /opt/tms/venv/bin/pip install \
  --index-url https://<artifactory-host>/artifactory/api/pypi/<pypi-remote>/simple \
  --upgrade pip

sudo -u tms /opt/tms/venv/bin/pip install \
  --index-url https://<artifactory-host>/artifactory/api/pypi/<pypi-remote>/simple \
  /opt/tms
```

매번 `--index-url` 을 치기 싫으면 고정한다:

```bash
sudo -u tms install -d -m 755 /opt/tms/.config/pip
sudo -u tms tee /opt/tms/.config/pip/pip.conf >/dev/null <<EOF
[global]
index-url = https://<artifactory-host>/artifactory/api/pypi/<pypi-remote>/simple
EOF
```

**설치 확인** — 두 실행 파일이 생겨야 한다.

```bash
ls -l /opt/tms/venv/bin/tms-api /opt/tms/venv/bin/tms-collector
```

설치되는 의존성: `fastapi`, `uvicorn[standard]`, `PyYAML`, `Jinja2`, `httpx`, `psycopg[binary]`, `python-multipart`.

> **⚠️ Python 3.9 가 하한이다.** 일부 대상 호스트가 아직 3.9 다. 3.8 이하에서는 설치가 거부된다.

---

## 3. PostgreSQL 구축

**`docs/runbooks/db-setup.md` 를 그대로 수행한다.** 여기서는 순서와 빠지기 쉬운 지점만 짚는다.

| 단계 | 내용 |
|---|---|
| 3-1 | 역할 2개 생성: `tms_owner`(스키마 소유), `tms_app`(애플리케이션 접속) |
| 3-2 | `CREATE DATABASE tms OWNER tms_owner` |
| 3-3 | **`ALTER SCHEMA public OWNER TO tms_owner;`** |
| 3-4 | `psql -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/001_init.sql` |
| 3-5 | `psql -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/002_grants.sql` |
| 3-5b | `psql -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/003_snapshot_kinds.sql` — **R2 이상 필수** |
| 3-5c | `004_restart_sequence.sql` + `005_restart_sequence_grants.sql` — **안전 시퀀스(FR-CO-02) 사용 시 필수** |
| 3-6 | **append-only 검증** (db-setup.md §4) — 건너뛰지 말 것 |

> **⛔ 3-3 을 빠뜨리면 조용히 깨진다.** `002_grants.sql` 이 `WARNING: no privileges were granted for "public"` 만 남기고 성공한 것처럼 끝난다. PG14 는 PUBLIC 의사 역할의 기본 USAGE 덕분에 우연히 동작하지만, 보안 강화로 `REVOKE ALL ON SCHEMA public FROM PUBLIC` 이 적용되는 순간 `tms_app` 이 모든 테이블에 접근하지 못한다. (2026-08-06 재현 확인)

> **⛔ 역할을 하나로 합치지 마라.** 애플리케이션이 소유자면 `GRANT`/`REVOKE` 가 무의미해지고 **감사 로그 append-only 가 성립하지 않는다** (FR-AA-04). 소유자는 자기 테이블에 항상 전권을 갖는다.

**게이트**: db-setup.md §4 의 UPDATE/DELETE 가 **둘 다 `permission denied`** 여야 다음으로 간다.

---

## 4. Trino 측 준비 (TMS 기동 전에 끝나야 한다)

운영팀이 수행한다. **이 단계가 안 되면 헬스 화면이 전부 UNKNOWN 이 된다.**

### 4-1. 서비스 계정

`tms-svc` 계정과 비밀번호. TMS 는 **basic auth 만** 쓰고 `X-Trino-User` 를 보내지 않는다 — 인증 주체와 세션 사용자를 같게 유지해 impersonation 검사를 아예 피한다 (TRINO_VERIFIED.md §T3-5).

### 4-2. `rules.json` 권한 — 2종 모두 필요

```json
{
  "system_information": [
    { "user": "tms-svc", "allow": ["read"] }
  ],
  "queries": [
    { "user": "tms-svc", "allow": ["view", "kill"] }
  ]
}
```

| 권한 | 없으면 |
|---|---|
| `system_information: read` | JMX 전부 403 → **H-03~H-07 판정 불가** |
| `queries: view` | 실행 중 쿼리 목록이 **403 이 아니라 빈 목록**으로 온다 (조용한 실패) |
| `queries: kill` | 쿼리 kill 실패 |

> **⛔ `system_information` 규칙 자체가 없으면 기본값이 "전부 거부" 다.** 규칙 블록을 통째로 빼면 안 된다.
> **⛔ `queries` 거부는 403 이 아니라 필터링(빈 배열)으로 나타난다.** 그래서 TMS 에 H-09 자가진단이 따로 있다. 목록이 비었는데 JMX 가 실행 중 쿼리를 보고하면 권한 문제로 판정한다.

### 4-3. HTTPS 필수

**basic auth 는 HTTPS 에서만 동작한다.** HTTP 로 접속하면 Trino 가 `insecureAuthenticator` 를 쓰기 때문에 `401 Password not allowed for insecure authentication` 이 뜬다. 코디네이터 URL 은 반드시 `https://` 다.

---

## 5. ⭐ 연결 사전 검증 (서비스 기동 전)

**서비스를 띄우기 전에 여기서 막힌 걸 다 잡아라.** 기동 후 로그를 읽는 것보다 훨씬 빠르다.

```bash
cd /opt/tms
read -rs TMS_TRINO_PASSWORD && export TMS_TRINO_PASSWORD   # 화면·이력에 안 남는다

/opt/tms/venv/bin/python scripts/verify_connectivity.py \
  --coordinator https://<trino-a-호스트>:8443 \
  --user tms-svc \
  --expected-workers 12
```

사내 CA 가 아직 신뢰 저장소에 없다면 `--insecure` 를 임시로 붙인다 (§7 에서 해결한 뒤 다시 검증하라).

**두 클러스터 모두 수행한다.** 다 끝나면:

```bash
unset TMS_TRINO_PASSWORD
```

**결과 해석**

| 출력 | 의미 | 조치 |
|---|---|---|
| `V1-2 JMX reachable, N MBeans registered` | 정상 | — |
| `V1-2 403 Forbidden - tms-svc lacks system_information:read` | 권한 누락 | §4-2 |
| `V1-3/H-03 ActiveNodeCount=13` | 정상 (코디네이터 포함) | `expected_workers` 확인 |
| `V1-3 ... NOT REGISTERED` | MBean 이름 변경(버전업) | 출력된 candidate 목록으로 실제 이름 확인 후 보고 |
| `V1-4/H-09 SILENT FILTERING` | **목록이 조용히 비었다** | §4-2 의 `queries: view` |
| `401 Password not allowed for insecure authentication` | HTTP 로 접속 | §4-3 |

> `ActiveNodeCount` 는 **코디네이터를 포함한다** (실측: 워커 12대 → 13). 스크립트가 이걸 자동 판정해서 알려준다.

---

## 6. 설정

### 6-1. `config/config.yaml` — 비밀 아닌 값

클러스터 목록을 사내 값으로 바꾼다.

```yaml
clusters:
  - name: prod-a
    coordinator_url: https://<trino-a-호스트>:8443
    expected_workers: 12
    trino_ui_url: https://<trino-a-호스트>:8443/ui/
  - name: prod-b
    coordinator_url: https://<trino-b-호스트>:8443
    expected_workers: 12
    trino_ui_url: https://<trino-b-호스트>:8443/ui/

server:
  host: 127.0.0.1     # §10 참조. 리버스 프록시가 같은 VM 이면 이대로 둔다
  port: 8500
```

> **저장소가 PUBLIC 이라 실제 호스트명도 커밋 대상이 아니다.** 두 가지 중 택일한다.
> - **(a)** `config.yaml` 을 사내 값으로 고치고 **커밋하지 않는다** (`git update-index --skip-worktree config/config.yaml` 로 실수 방지)
> - **(b)** 사내 전용 설정을 별도 경로에 두고 `TMS_CONFIG=/etc/tms/config.yaml` 로 가리킨다 — **권장**. 이 경우 `config.secret.yaml` 도 같은 디렉터리에서 찾는다

딥링크는 아는 것부터 채운다. **빈 값은 링크를 아예 렌더링하지 않는다** (죽은 링크를 만들지 않기 위해서다).

```yaml
deeplinks:
  query_history:
    query_url_template: https://<기존 쿼리히스토리>/query/{query_id}
    home_url: https://<기존 쿼리히스토리>/
  superset_url: https://<superset>/
  grafana:
    cluster_dashboard: https://<grafana>/d/trino/{cluster}
  log:
    template: https://<loki-또는-opensearch>/...{query}...{from_ms}...{to_ms}
```

### 6-2. 시크릿 — `/etc/tms/tms.env` (권장)

```bash
sudo install -d -m 750 -o root -g tms /etc/tms

# 세션 비밀키 생성 (전 인스턴스 동일해야 한다)
SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")

sudo tee /etc/tms/tms.env >/dev/null <<EOF
TMS_DATABASE_URL=postgresql://tms_app:<app-password>@<db-host>:5432/tms
TMS_TRINO_PASSWORD=<tms-svc-비밀번호>
TMS_SESSION_SECRET=${SESSION_SECRET}
EOF

sudo chmod 600 /etc/tms/tms.env
sudo chown root:tms /etc/tms/tms.env
unset SESSION_SECRET
```

| 환경변수 | 대응 설정 |
|---|---|
| `TMS_DATABASE_URL` | `database.url` |
| `TMS_TRINO_PASSWORD` | `trino.password` |
| `TMS_SESSION_SECRET` | `portal.session_secret` |
| `TMS_CONFIG` | 설정 파일 경로 (기본 `/opt/tms/config/config.yaml`) |
| `TMS_LOG_LEVEL` | 로그 레벨 (기본 `INFO`) |

환경변수가 `config.secret.yaml` 보다 **우선**한다. 다만 **빈 값은 덮어쓰지 않는다** — 항목이 없으면 파일 값이 살아남는다.

> **⛔ `TMS_SESSION_SECRET` 이 없으면 기동에 실패한다.** 임의 생성으로 대체하지 않는 것은 의도된 설계다 — 임의 생성이면 재시작마다 전원 로그아웃되고 다중 인스턴스에서 조용히 깨진다.
> **⛔ tms-api 를 여러 대 띄운다면 전부 같은 값이어야 한다.** 다르면 LB 가 사용자를 옮길 때마다 세션이 끊긴다.

---

## 7. 내부 CA / TLS 검증

`config.yaml` 의 `trino.verify_tls` 는 기본 `true` 다. 사내 코디네이터가 **내부 CA 서명 인증서**를 쓴다면 CA 를 알려줘야 한다.

**권장 — `SSL_CERT_FILE` 로 CA 번들 지정** (2026-08-07 실측 확인)

```bash
sudo install -o root -g tms -m 644 <내부CA>.pem /etc/tms/internal-ca.pem

sudo tee -a /etc/tms/tms.env >/dev/null <<'EOF'
SSL_CERT_FILE=/etc/tms/internal-ca.pem
EOF
```

검증 결과 (httpx 0.28.1 / OpenSSL 3.x):

| 조건 | 결과 |
|---|---|
| `verify_tls: true`, CA 미지정 | `ConnectError` (인증서 검증 실패) |
| `verify_tls: true` + `SSL_CERT_FILE` | **HTTP 200** |

> **`verify_tls: false` 는 최후 수단이다.** 검증을 통째로 끄는 것이라 중간자 공격에 그대로 노출된다. 사내망이라도 CA 를 지정하는 편이 낫다. 부득이 쓴다면 `config.yaml` 에 사유를 주석으로 남기고 티켓을 걸어라.
>
> CA 번들에 체인이 여러 개면 PEM 을 이어 붙이면 된다 (`cat root.pem intermediate.pem > internal-ca.pem`).

---

## 8. 운영자 계정 생성

> **임시 조치다.** AD 연동 전까지만 쓴다 (D-007). 기동 시 WARN 로그로 이 사실을 알린다.

```bash
cd /opt/tms
sudo -u tms /opt/tms/venv/bin/python scripts/hash_password.py \
  --user <이름> --roles admin --temporary
```

- 비밀번호는 **가려진 프롬프트**로 입력한다 (셸 이력·프로세스 목록에 안 남는다)
- 12자 이상 + 문자종류 3종 이상
- `--temporary`: 최초 로그인 후 비밀번호를 바꾸기 전까지 다른 모든 동작이 막힌다

출력을 `config.secret.yaml` 에 붙여넣는다.

```bash
sudo -u tms cp /opt/tms/config/config.secret.yaml.example /opt/tms/config/config.secret.yaml
sudo -u tms chmod 600 /opt/tms/config/config.secret.yaml
```

> **⛔ `sudo -u tms` 의 `-u tms` 를 빼먹지 마라.** 그냥 `sudo` 로 만들면 파일 소유자가 `root` 가 되고, 모드가 600 이라 **서비스 계정(`tms`)이 읽지 못한다.** 기동이 `permission denied ... config/config.secret.yaml` 로 실패한다. 편집기로 `sudo vi` 해서 만든 경우도 동일하다.

**소유권 확인** — 서비스를 띄우기 전에 이걸 확인하는 편이 빠르다.

```bash
ls -l /opt/tms/config/config.secret.yaml     # tms tms, -rw-------
sudo -u tms head -c1 /opt/tms/config/config.secret.yaml >/dev/null \
  && echo "OK: tms 계정이 읽을 수 있다" || echo "FAIL: 아래 명령으로 고쳐라"
```

틀어졌다면:

```bash
sudo chown tms:tms /opt/tms/config/config.secret.yaml
sudo chmod 600 /opt/tms/config/config.secret.yaml
```

```yaml
portal:
  local_users:
    syhcho:
      password_hash: "pbkdf2_sha256$600000$...$..."
      roles: [admin]
      must_change_password: true
    <다음사람>:
      password_hash: "..."
      roles: [operator]
```

| 역할 | 가능한 것 |
|---|---|
| `viewer` | 포털, 실행 중 쿼리 조회, 헬스 조회 |
| `operator` | viewer + **쿼리 kill**, 감사 로그 조회 |
| `admin` | operator + 헬스 테스트/임계값 변경, 감사 로그 내보내기 |

> **⛔ `portal.local_users` 가 비어 있으면 웹 UI 가 아예 마운트되지 않는다.** 로그인할 방법이 없는 UI 를 띄우지 않기 위한 의도된 동작이다. `/api/v1/login` 도 "Authentication is not configured" 를 반환한다. **계정을 최소 1개는 만들어야 화면이 나온다.**
>
> **⛔ 계정을 공유하지 마라.** `admin` 하나를 여럿이 쓰면 모든 감사 기록의 `actor` 가 `admin` 이 되어 "누가 이 쿼리를 죽였나"에 답할 수 없다. 그게 FR-AUDIT-ACTION 이 존재하는 이유다.
>
> **평문 `password:` 키는 거부되며 기동이 실패한다.** 평문이 들어간 파일은 언젠가 커밋되기 때문이다.

---

## 8-5. ⭐ 재시작 전 설정 검증

서비스를 올리기 전에 설정을 한 번에 점검한다. **기동 후 로그를 읽는 것보다 훨씬 빠르다.**

```bash
read -rs TMS_TRINO_PASSWORD && export TMS_TRINO_PASSWORD
/opt/tms/venv/bin/tms-config-check --config /opt/tms/config/config.yaml
unset TMS_TRINO_PASSWORD
```

| 종료 코드 | 의미 |
|---|---|
| 0 | 사용 가능 (경고는 있을 수 있다) |
| 1 | 문제 발견 — **고치기 전에 재시작하지 마라** |
| 2 | 설정을 아예 읽지 못했다 |

검사 항목: `coordinator_url` 이 `https` 인지 · Trino 계정·비밀번호 실제 인증 · `system_information:read` / `queries:view` 권한 · DB 접속과 테이블 4개 · 세션 비밀키 · 로컬 계정 유무 · 시크릿 파일 권한 · 딥링크 치환자.

> **계정 오타는 이 검사에서만 잡힌다.** 정적 검사(`--offline`)로는 잡을 수 없다. 비밀번호를 넣고 돌려라.

이후 설정을 바꿀 때마다 재시작 전에 같은 명령을 돌리면 된다.

---

## 9. systemd 기동 — collector 먼저

```bash
sudo cp /opt/tms/ops/systemd/tms-collector.service /etc/systemd/system/
sudo cp /opt/tms/ops/systemd/tms-api.service       /etc/systemd/system/
sudo systemctl daemon-reload
```

`TMS_CONFIG` 를 바꿨다면 (§6-1 (b)) 유닛에도 반영한다:

```bash
sudo systemctl edit tms-collector   # 그리고 tms-api 도 동일하게
```
```ini
[Service]
Environment=TMS_CONFIG=/etc/tms/config.yaml
```

### 9-1. collector (첫 통합 확인 지점)

```bash
sudo systemctl enable --now tms-collector
sudo journalctl -u tms-collector -f
```

**정상 로그**
```
collector started for 2 cluster(s): prod-a, prod-b
```

**스냅샷이 실제로 쌓이는지 확인**
```sql
SELECT cluster, kind, collected_at, collection_error,
       length(payload::text) AS payload_bytes
FROM collector_snapshot ORDER BY cluster, kind;
```

**기동 실패 / 스냅샷 이상 진단**

| 증상 | 원인 | 조치 |
|---|---|---|
| `Unit tms-collector.service not found` | 유닛 미설치 | §9 첫 `cp` + `daemon-reload` |
| `status=203/EXEC` | `/opt/tms/venv/bin/tms-collector` 없음 | §2 `pip install /opt/tms` 재확인 |
| **`status=226/NAMESPACE`** | **샌드박스 마운트 설정 실패** — Python 이 실행되기도 전이다 | 아래 9-3 |
| 기동 실패 `permission denied ... config.secret.yaml` | 파일 소유자가 `root` (`sudo` 로 생성) | §8 소유권 확인. `sudo chown tms:tms` |
| 기동 실패 `configuration error` / `is required` | `config.yaml` 또는 `/etc/tms/tms.env` 미완 | §6 |
| 기동 실패 DB 접속 오류 | `TMS_DATABASE_URL` 오류·방화벽 | §3, §6-2 |
| `another tms-collector already holds the advisory lock` | 중복 기동 | **정상 차단.** 위 경고 참조 |
| `collection_error` 에 `TrinoForbidden` | `rules.json` 권한 | §4-2 |
| `collection_error` 에 `MBeanNotRegistered` | 버전업으로 MBean 이름 변경 | `GET /v1/jmx/mbean` 열거 후 보고 |
| `collection_error` 에 `filtered by access control` | **H-09 발동** — 목록이 조용히 비었다 | §4-2 `queries: view`. 조용한 실패를 잡은 것이므로 **정상 동작** |
| 인증서 오류 / `ConnectError` | 내부 CA 미신뢰 | §7 |
| 행이 아예 없음 | 폴링 미도달 | `journalctl -u tms-collector -n 100` |

> **⛔ collector 는 절대 1개만 띄운다.** 2개를 띄우면 모든 코디네이터에 부하가 2배가 되어 NFR-PERF-03 이 조용히 깨진다. 중복 기동은 PostgreSQL advisory lock 으로 차단되며 `another tms-collector already holds the advisory lock; exiting` 를 남기고 **종료 코드 4** 로 끝난다 — **이건 정상 동작이다.**
>
> 이 경우 systemd 가 `Restart=on-failure` 로 재시도하다가 `StartLimitBurst=5`(5분 내 5회)에 걸려 유닛이 `failed` 상태로 남는다. **끝없는 재시작 루프 대신 눈에 띄는 실패로 드러나게 한 것**이므로, `failed` 를 보면 중복 기동부터 의심하라. 유닛에 `@` 인스턴스나 템플릿을 만들지 마라.

### 9-2. api

```bash
sudo systemctl enable --now tms-api
sudo journalctl -u tms-api -f
curl -s http://127.0.0.1:8500/health     # {"status":"ok"}
curl -s http://127.0.0.1:8500/ready      # {"status":"ready"}
```

`tms-api` 는 **무상태**다. 스냅샷을 DB 에서 읽을 뿐 타이머로 Trino 를 폴링하지 않으므로, 여러 대로 늘려도 코디네이터 부하가 늘지 않는다.

**기동 실패 진단**

| 증상 | 원인 | 조치 |
|---|---|---|
| `Directory 'tms/web/static' does not exist` | **UI 파일이 설치되지 않았다** | 아래 |
| `226/NAMESPACE` | 샌드박스 마운트 실패 | §9-3 |
| `permission denied ... config.secret.yaml` | 파일 소유자가 `root` | §8 |
| 기동은 되는데 `/` 가 404 | `portal.local_users` 비어 있음 | §8 |

`static`/`templates` 가 설치되지 않는 문제는 **0.1.0 최초 배포판의 패키징 버그**였다 (2026-08-07 수정). `pip install` 로 만들어지는 패키지에 UI 파일이 빠져 있었다. 최신 코드로 재설치하면 해결된다.

```bash
cd /opt/tms && sudo -u tms git pull
sudo -u tms /opt/tms/venv/bin/pip install --force-reinstall --no-deps \
  --index-url https://<artifactory-host>/artifactory/api/pypi/<pypi-remote>/simple /opt/tms
sudo systemctl restart tms-api
```

**설치 확인** — 아래가 비어 있으면 여전히 안 들어간 것이다.

```bash
SP=$(/opt/tms/venv/bin/python -c "import tms,os;print(os.path.dirname(os.path.dirname(tms.__file__)))")
ls "$SP/tms/web/static" "$SP/tms/web/templates"
```

기대: `static` 2개(`tms.css`, `tms.js`), `templates` 14개.

---

### 9-3. `226/NAMESPACE` 로 죽는 경우

```
tms-collector.service: Main process exited, code=exited, status=226/NAMESPACE
```

**애플리케이션 오류가 아니다.** systemd 가 유닛의 샌드박스(`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `LogsDirectory`)를 위한 **마운트 네임스페이스를 만드는 데 실패**한 것이고, 그래서 Python 은 단 한 줄도 실행되지 않았다. 로그에 앱 메시지가 하나도 없는 이유가 이것이다.

정확한 원인은 종료 직전 줄에 찍힌다.

```bash
sudo journalctl -u tms-collector -n 40 --no-pager | grep -i "namespac\|mount\|Failed to set up"
```

| 로그 | 원인 | 조치 |
|---|---|---|
| `Failed to set up mount namespacing: No such file or directory` | 유닛이 참조하는 경로가 없음 | 아래 (1) |
| `ProtectHome` 관련 | `/opt/tms` 를 `/home` 아래에 두었음 | `ProtectHome=false` 로 완화하거나 경로 이동 |
| `Operation not permitted` | 구버전 systemd·컨테이너·SELinux | `systemctl --version` 확인, 아래 (2) |

**(1) 과거 유닛의 `ReadWritePaths=/var/log/tms` — 가장 흔했던 원인**

이전 버전 유닛에는 `ReadWritePaths=/var/log/tms` 가 있었다. **그 디렉터리가 없으면 네임스페이스 구성이 통째로 실패한다.** 게다가 앱은 디스크에 아무것도 쓰지 않으므로(로그는 전부 journald 로 간다) 애초에 불필요한 지시자였다.

현재 유닛은 `LogsDirectory=tms` 로 바꿨다 — systemd 가 `/var/log/tms` 를 알아서 만들고 소유권도 `User=` 에 맞춘다. **최신 유닛을 다시 설치하면 해결된다.**

```bash
cd /opt/tms && sudo -u tms git pull
sudo cp ops/systemd/tms-collector.service ops/systemd/tms-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart tms-collector
```

옛 유닛을 그대로 쓰고 싶다면 디렉터리만 만들어도 된다.

```bash
sudo install -d -o tms -g tms -m 755 /var/log/tms
sudo systemctl restart tms-collector
```

**(2) 그래도 안 되면 — 어느 지시자가 문제인지 이분법으로 찾는다**

```bash
sudo systemd-analyze verify /etc/systemd/system/tms-collector.service
```

하드닝을 임시로 꺼서 격리한다. **원인을 찾은 뒤에는 반드시 되돌린다.**

```bash
sudo systemctl edit tms-collector
```
```ini
[Service]
ProtectSystem=
ProtectHome=
PrivateTmp=
```

이 상태로 기동되면 원인은 셋 중 하나다. 하나씩 되살려 범인을 특정하라. 기동에 성공하면 override 를 지우고(`sudo systemctl revert tms-collector`) 근본 원인을 고친다.

> **하드닝을 영구히 끄지 마라.** 이 서비스는 프로덕션 쿼리를 죽일 수 있는 자격증명을 들고 있다. `ProtectSystem=strict` 는 침해 시 피해 범위를 줄이는 최후 방어선이다.

---

## 10. ⭐ HTTPS 종단 (없으면 로그인이 안 된다)

**세션 쿠키는 `Secure` 플래그가 붙어 있다.** 브라우저는 HTTPS 로 받은 응답이 아니면 이 쿠키를 저장하지 않는다. `http://<tms-host>:8500/` 으로 직접 접속하면 **로그인 → 리다이렉트 → 다시 로그인 화면이 무한 반복**된다. 로컬 검증에서 실제로 겪은 증상이다.

**쿠키를 약화시키지 말고 앞단에 TLS 를 둔다.** `tms-api` 자체는 TLS 를 종단하지 않는다.

nginx 예시 (같은 VM):

```nginx
server {
    listen 443 ssl;
    server_name <tms-host>;

    ssl_certificate     /etc/ssl/certs/<tms>.pem;
    ssl_certificate_key /etc/ssl/private/<tms>.key;

    location / {
        proxy_pass http://127.0.0.1:8500;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

`/`(UI), `/api/`, `/ui/static`, `/health`, `/ready` 가 전부 같은 앱이므로 **경로 분기 없이 통째로 넘기면 된다.**

**적용과 확인**

```bash
sudo nginx -t && sudo systemctl reload nginx

# 브라우저를 열기 전에 nginx 를 통과하는지부터 확인한다
curl -sk -o /dev/null -w "%{http_code}\n" https://127.0.0.1/     # 303 이면 정상
```

| 결과 | 의미 | 조치 |
|---|---|---|
| `303` | 정상 — `/login` 으로 리다이렉트 | 브라우저로 접속 |
| `502` | nginx 가 백엔드에 붙지 못함 | 아래 SELinux 확인, `tms-api` 상태 확인 |
| `404` | UI 미마운트 | §8 `portal.local_users` |
| 연결 거부 | nginx 미기동·방화벽 | `systemctl status nginx`, 443 방화벽 |

> **⚠️ RHEL·Rocky·CentOS 계열에서 `502` 가 나오면 SELinux 를 먼저 의심하라.** 기본 정책이 nginx 의 아웃바운드 접속을 막아서, 설정이 완벽해도 502 가 난다. `sudo tail /var/log/audit/audit.log | grep denied` 로 확인되면:
>
> ```bash
> sudo setsebool -P httpd_can_network_connect 1
> ```
>
> `-P` 를 붙여야 재부팅 후에도 유지된다. **SELinux 를 통째로 끄지 마라** — 이 서비스는 프로덕션 쿼리를 죽일 수 있는 자격증명을 들고 있다.

**방화벽** — 외부에서 443 이 열려 있어야 한다.

```bash
sudo firewall-cmd --add-service=https --permanent && sudo firewall-cmd --reload   # firewalld
# 또는
sudo ufw allow 443/tcp                                                            # ufw
```

**인증서** — 사내 CA 발급 인증서가 원칙이다. 아직 없어 우선 붙여만 보겠다면 자체 서명으로도 동작한다(브라우저 경고가 뜬다).

```bash
sudo openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
  -keyout /etc/ssl/private/tms.key -out /etc/ssl/certs/tms.pem \
  -subj "/CN=<tms-host>" -addext "subjectAltName=DNS:<tms-host>"
sudo chmod 600 /etc/ssl/private/tms.key
```

> 자체 서명은 **임시방편이다.** 운영자들이 매번 경고를 클릭해 넘기는 데 익숙해지면, 진짜 중간자 공격이 있어도 똑같이 넘긴다. 사내 CA 인증서로 교체할 티켓을 걸어라.

| 구성 | `server.host` | 감사 IP 추가 설정 |
|---|---|---|
| **같은 VM 의 nginx 가 프록시** (권장) | `127.0.0.1` (기본) | 불필요 — 그대로 동작 |
| 외부 LB 가 직접 8500 접속 | `0.0.0.0` + 방화벽으로 LB 만 허용 | **`FORWARDED_ALLOW_IPS` 필요** (아래) |

### 10-1. 감사 로그의 `actor_ip` — 신뢰 경계 (2026-08-07 실측 확인)

"누가 이 쿼리를 죽였나"에 답하려면 IP 가 정확해야 한다 (FR-AUDIT-ACTION). 동작 방식은 이렇다.

TMS 는 `X-Forwarded-For` 를 직접 읽지 않는다. **uvicorn 이 대신 처리한다.** `proxy_headers` 는 기본 `True` 이고, 신뢰 대상은 **기본값이 `127.0.0.1`** 이다. 즉 **직전 연결 상대가 127.0.0.1 일 때만** `X-Forwarded-For` 를 믿고 클라이언트 IP 를 바꿔치기한다.

실측 결과 — 같은 VM 프록시 구성에서 `X-Forwarded-For: 10.99.99.99` 로 감사 액션을 실행하니 감사 레코드의 `actor_ip` 가 `10.99.99.99` 로 기록됐다.

| 구성 | `X-Forwarded-For` 반영 | 결과 |
|---|---|---|
| 같은 VM nginx (peer = 127.0.0.1) | **반영됨** | 실제 사용자 IP 가 기록된다 ✅ |
| 외부 LB 직결 (peer = LB IP) | 무시됨 | 모든 감사 레코드가 **LB 의 IP** 로 남는다 ❌ |

외부 LB 직결 구성이라면 LB 의 IP 를 신뢰 목록에 넣어야 한다.

```bash
sudo tee -a /etc/tms/tms.env >/dev/null <<'EOF'
FORWARDED_ALLOW_IPS=<LB의 IP>
EOF
```

> **⛔ `FORWARDED_ALLOW_IPS=*` 를 절대 쓰지 마라.** 아무 클라이언트나 `X-Forwarded-For` 를 붙여 **감사 로그의 자기 IP 를 위조**할 수 있게 된다. 위조 가능한 감사 로그는 감사 로그가 아니다. 신뢰할 프록시의 IP 만 정확히 나열하라.

프록시는 `X-Forwarded-For` 를 반드시 전달해야 한다 (§10 nginx 예시에 포함되어 있다).

---

## 11. 동작 확인

브라우저로 **`https://<tms-host>/`** 접속.

| # | 확인 | 기대 |
|---|---|---|
| 11-1 | 로그인 화면 | `Sign in` 카드 |
| 11-2 | §8 계정으로 로그인 | 임시 비밀번호면 **비밀번호 변경 화면으로 강제 이동** |
| 11-3 | 비밀번호 변경 | 응답의 **새 해시를 `config.secret.yaml` 에 반영** (아래 경고) |
| 11-4 | Overview | 클러스터 2개 카드, `updated Ns ago` |
| 11-5 | Live Queries → All | 두 클러스터 쿼리가 한 화면에, `Cluster` 컬럼 존재 |
| 11-6 | Health → prod-a | H-01~H-07, H-09 가 **UNKNOWN 이 아닌** 상태 |
| 11-7 | Audit Log | §3-6 검증 행이 보이면 정상 |
| 11-8 | Export CSV | 사유 없이 누르면 **거부**, 사유를 넣으면 CSV |

> **⛔ 11-3 을 빠뜨리면 재시작 시 임시 비밀번호로 되돌아간다.** 프로세스는 gitignore 된 설정 파일을 스스로 쓰지 못한다. 응답의 새 해시를 `config.secret.yaml` 에 넣고 `must_change_password` 줄을 지운 뒤 `sudo systemctl restart tms-api`. 이 한계는 AD 연동으로 해소된다 (D-007).

**11-6 이 전부 UNKNOWN 이면** §4-2 권한 문제일 가능성이 가장 높다. §5 검증으로 되돌아가라.

---

## 12. 문제 해결

| 증상 | 원인 | 조치 |
|---|---|---|
| 로그인했는데 다시 로그인 화면 (무한 반복) | HTTP 로 접속 → `Secure` 쿠키 미저장 | §10. HTTPS 로 접속 |
| 화면이 아예 없고 404 | `portal.local_users` 비어 있음 → UI 미마운트 | §8 |
| 기동 실패 `session secret is required` | `TMS_SESSION_SECRET` 미설정 | §6-2 |
| 기동 실패 `plaintext 'password' is not accepted` | 설정에 평문 비밀번호 | §8, `hash_password.py` |
| 헬스가 전부 UNKNOWN | JMX 403 | §4-2 `system_information: read` |
| 실행 중 쿼리가 0인데 실제로는 돌고 있음 | `queries: view` 거부 (조용한 필터링) | §4-2. H-09 가 잡아준다 |
| `collection_error`에 `TrinoForbidden` | 권한 | §4-2 |
| `collection_error`에 `MBeanNotRegistered` | 버전업으로 MBean 이름 변경 | `GET /v1/jmx/mbean` 열거로 실제 이름 확인 후 보고 |
| `401 Password not allowed for insecure authentication` | 코디네이터에 HTTP 로 접속 | §4-3. `https://` 로 |
| `ConnectError` / 인증서 오류 | 내부 CA 미신뢰 | §7 `SSL_CERT_FILE` |
| `another tms-collector already holds the advisory lock` | collector 중복 기동 | **정상 차단.** 중복 여부 확인 |
| LB 뒤에서 세션이 자꾸 끊김 | 인스턴스별 `TMS_SESSION_SECRET` 불일치 | §6-2. 전 인스턴스 동일 값 |
| 429 계정 잠김 | 5분 내 5회 실패 | 5분 대기 |
| 재시작 후 옛 비밀번호로 되돌아감 | 새 해시 미반영 | §11-3 |
| 데이터가 stale 로 표시 | collector 정지 | `systemctl status tms-collector` |

**로그 위치**

```bash
sudo journalctl -u tms-api -n 200 --no-pager
sudo journalctl -u tms-collector -n 200 --no-pager
```

---

## 13. 업데이트 / 중지 / 롤백

**업데이트**

```bash
cd /opt/tms
sudo -u tms git pull
sudo -u tms /opt/tms/venv/bin/pip install \
  --index-url https://<artifactory-host>/artifactory/api/pypi/<pypi-remote>/simple /opt/tms
sudo systemctl restart tms-collector tms-api
```

마이그레이션이 추가된 릴리스는 **재시작 전에** `migrations/` 의 새 파일을 `tms_owner` 로 적용한다.

> **⚠️ R2 로 올릴 때 `003_snapshot_kinds.sql` 을 빼먹으면 조용히 깨진다.** collector 가 새 스냅샷을 쓰고 PostgreSQL 이 거부하는데, collector 는 로그만 남기고 계속 돈다(저장 실패가 폴링을 멈추면 안 되므로). Workload·Gateway 화면이 **아무 오류 없이 빈 채로** 남는다.

**중지**

```bash
sudo systemctl stop tms-api tms-collector
```

> **TMS 가 완전히 죽어도 모든 쿼리는 정상 실행된다** (NFR-ISOLATION). TMS 는 쿼리 실행 경로에 끼어들지 않는다. 급하면 망설이지 말고 내려라.

**롤백**

```bash
cd /opt/tms
sudo -u tms git checkout <직전 리비전>
sudo -u tms /opt/tms/venv/bin/pip install --index-url <...> /opt/tms
sudo systemctl restart tms-collector tms-api
```

> **⛔ 프로덕션에서 `audit_action` 테이블을 DROP 하지 않는다.** FR-AA-04 가 데이터 보존을 요구한다. 스키마 변경이 필요하면 새 마이그레이션으로 컬럼을 추가하라.

---

## 부록 — 왜 이렇게 되어 있나 (자주 나오는 질문)

| 질문 | 답 |
|---|---|
| collector 를 왜 이중화 못 하나 | 이중화가 아니라 **부하 2배**다. Trino 를 폴링하는 유일한 프로세스이므로 단일 인스턴스여야 NFR-PERF-03 이 성립한다. 가용성은 systemd `Restart=on-failure` 로 확보한다 |
| tms-api 는 늘려도 되나 | 된다. 무상태이고 스냅샷을 DB 에서 읽는다. `TMS_SESSION_SECRET` 만 동일하면 된다 |
| 왜 Gateway DB 를 안 쓰나 | Gateway DB 는 queryId→backend 조회로 **쿼리 경로의 일부**다. TMS 가 부하를 얹으면 NFR-ISOLATION 취지에 어긋난다 (D-004) |
| 왜 세션 쿠키를 Secure 로 강제하나 | 운영자 세션은 프로덕션 쿠리를 죽일 수 있는 권한이다. 평문 전송을 허용할 이유가 없다 |
| 완료된 쿼리는 어디서 보나 | 기존 쿼리 히스토리 프로젝트 소관이다 (D-001). TMS 는 **실행 중** 쿼리만 다루고, 완료 쿼리는 딥링크로 넘긴다 |
