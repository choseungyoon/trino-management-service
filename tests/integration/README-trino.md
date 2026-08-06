# 로컬 Trino 477 검증 환경

`scripts/verify_connectivity.py` 와 collector·헬스 엔진을 **실제 Trino 477** 에 붙여 확인하기 위한 구성.
2026-08-06 에 이 절차로 검증했고, 그 결과가 `TRINO_VERIFIED.md` 의 "실증" 표기 항목들이다.

---

## 왜 필요했나

문서와 소스만으로 정한 사실 중 실물과 다를 수 있는 것이 있었다. 실제로 하나가 틀렸고
(`HeartbeatFailureDetector` — 477 문서가 코드보다 뒤처져 있었다), 여기서 두 가지를 더 잡았다.

- **basic auth 는 HTTPS 에서만 동작한다.** HTTP + `allow-insecure-over-http` 는
  "HTTP 에서 PASSWORD 인증 허용"이 아니라 "HTTP 에서는 insecure 인증기만 사용"이다.
- **`internal-communication.shared-secret` 이 PASSWORD 인증의 필수 선행 조건**이다.

---

## 구성

```bash
# 1) 다운로드 — 전체 배포판(830MB) 대신 core(278MB) 로 충분하다.
#    필요한 것은 REST/JMX 경로이며 번들 커넥터가 아니다.
#    ⚠️ Maven Central 에는 476까지만 있다. 477 부터는 GitHub Releases.
curl -sSL -O https://github.com/trinodb/trino/releases/download/477/trino-server-core-477.tar.gz
curl -sSL -O https://github.com/trinodb/trino/releases/download/477/trino-tpch-477.zip
curl -sSL -o trino-cli.jar https://github.com/trinodb/trino/releases/download/477/trino-cli-477
tar xzf trino-server-core-477.tar.gz
unzip -q trino-tpch-477.zip -d trino-server-core-477/plugin/
mv trino-server-core-477/plugin/trino-tpch-477 trino-server-core-477/plugin/tpch
```

### `etc/config.properties`

```properties
coordinator=true
node-scheduler.include-coordinator=true
http-server.http.port=8080
discovery.uri=http://127.0.0.1:8080
query.max-memory=1GB
query.max-memory-per-node=512MB

# basic auth 는 HTTPS 에서만 동작한다 — HTTP 로는 검증할 수 없다
http-server.authentication.type=PASSWORD
http-server.https.enabled=true
http-server.https.port=8443
http-server.https.keystore.path=<etc>/keystore.p12
http-server.https.keystore.key=changeit

# PASSWORD 인증 시 필수. 없으면 Guice 오류로 기동 실패
internal-communication.shared-secret=<random>
```

### 자체 서명 인증서

```bash
keytool -genkeypair -alias trino -keyalg RSA -keysize 2048 -validity 30 \
  -keystore etc/keystore.p12 -storetype PKCS12 -storepass changeit \
  -dname "CN=localhost, OU=TMS, O=Local, C=KR" \
  -ext "SAN=DNS:localhost,IP:127.0.0.1"
```

### `etc/password.db`

Trino 는 bcrypt 또는 **PBKDF2WithHmacSHA1** 를 받는다 (`user:iterations:hexsalt:hexhash`, 최소 1000 iterations).

```python
import hashlib, os
def entry(user, password, iterations=1000, keylen=64):
    salt = os.urandom(8)
    h = hashlib.pbkdf2_hmac("sha1", password.encode(), salt, iterations, dklen=keylen)
    return f"{user}:{iterations}:{salt.hex()}:{h.hex()}"
```

### `etc/rules.json` — **운영 환경 구조 그대로**

조용한 필터링을 재현하려면 이 구조가 핵심이다.

```jsonc
{
  "system_information": [
    { "user": "tms-svc",            "allow": ["read"] },
    { "user": "prometheus_scraper", "allow": ["read"] }
  ],
  "queries": [
    { "user": "tms-svc",            "allow": ["view", "kill"] },
    { "user": "prometheus_scraper", "allow": [] },
    { "allow": ["execute", "view", "kill"] }
  ],
  "catalogs": [ { "allow": "all" } ]
}
```

---

## 실행

```bash
bin/launcher restart
# starting:false 가 될 때까지 대기
curl -sk https://127.0.0.1:8443/v1/info

TMS_TRINO_PASSWORD=<tms-svc 비밀번호> python3 scripts/verify_connectivity.py \
  --coordinator https://127.0.0.1:8443 --user tms-svc --expected-workers 0 --insecure
```

`--insecure` 는 자체 서명 인증서 때문이다.

### 실행 중 쿼리 만들기

```bash
TRINO_PASSWORD=<analyst 비밀번호> java -jar trino-cli.jar \
  --server https://127.0.0.1:8443 --insecure --user analyst --password \
  --catalog tpch --schema sf1000 --source superset \
  --execute "SELECT count(*), sum(extendedprice) FROM lineitem" &
```

> 조인(`lineitem JOIN lineitem`)은 `query.max-memory-per-node` 를 넘겨 즉시 실패한다.
> 오래 도는 쿼리가 필요하면 **스트리밍 집계**를 쓴다.

---

## 이 환경에서 확인된 것

| 항목 | 결과 |
|---|---|
| `/v1/info` 의 `starting` 필드 | ✅ 존재. 기동 직후 `true` → 완료 후 `false` |
| `trino.node:name=CoordinatorNodeManager` | ✅ 존재, `*NodeCount` 5종 |
| `ActiveNodeCount` 의 코디네이터 포함 | ✅ 워커 0 클러스터에서 `1` |
| `Duration` / `DataSize` JSON 형식 | ✅ `'10.93s'`, `'537.42us'`, `'10488440B'` |
| collector 파싱 → 스냅샷 → 헬스 평가 | ✅ 전 구간 |
| **조용한 필터링 (H-09)** | ✅ 재현 및 탐지. 유휴 클러스터는 오탐 없음 |
| kill + 사유 전달 | ✅ `ADMINISTRATIVELY_KILLED`, 사유가 사용자 메시지에 포함 |
| 권한 없는 kill | ✅ `TrinoForbidden(transient=False)` |

---

## 정리

```bash
bin/launcher stop
rm -rf trino-server-core-477 trino-server-core-477.tar.gz trino-tpch-477.zip trino-cli.jar
```
