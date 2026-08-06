# RUNBOOK — 로컬 계정 설정 (임시)

> **임시 조치다.** AD 연동 전까지만 사용한다 (D-007). 코드가 기동 시 WARN 로그로 이 사실을 알린다.
> **⚠️ 저장소는 PUBLIC이다 (D-002).** 계정 정보는 `config.secret.yaml`(gitignore) 또는 환경변수에만 둔다.

---

## 1. 세션 비밀키 생성

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

`/etc/tms/tms.env` 에 넣는다 (**tms-api 전 인스턴스가 동일한 값**이어야 한다 — 다르면 LB가 사용자를 옮길 때마다 세션이 끊긴다).

```bash
sudo tee -a /etc/tms/tms.env >/dev/null <<EOF
TMS_SESSION_SECRET=<위에서 생성한 값>
EOF
sudo chmod 600 /etc/tms/tms.env
```

> 비밀키가 없으면 **기동에 실패한다.** 임의 생성으로 대체하지 않는다 — 재시작마다 전원 로그아웃되고 다중 인스턴스에서 조용히 깨지기 때문이다.

---

## 2. 계정 생성

```bash
python3 scripts/hash_password.py --user syhcho --roles admin --temporary
```

- 비밀번호는 **가려진 프롬프트**로 입력한다. 셸 이력과 프로세스 목록에 남지 않는다.
- 12자 이상 + 문자종류 3종 이상을 요구한다. `--allow-weak` 로 무시할 수 있으나 **프로덕션 쿼리를 죽일 수 있는 계정**임을 감안하라.
- `--temporary` 를 붙이면 최초 로그인 후 비밀번호를 바꾸기 전까지 다른 API가 전부 403이 된다.

출력을 `config/config.secret.yaml` 에 붙여넣는다.

```yaml
portal:
  local_users:
    syhcho:
      password_hash: "pbkdf2_sha256$600000$...$..."
      roles: [admin]
      must_change_password: true
```

```bash
chmod 600 config/config.secret.yaml
```

### ⚠️ 계정을 공유하지 말 것

`admin` 하나를 여러 명이 쓰면 **모든 감사 기록의 `actor` 가 `admin`** 이 된다. "누가 이 쿼리를 죽였나"에 답할 수 없어지고, 그게 FR-AUDIT-ACTION이 존재하는 이유다. **사람마다 계정을 만드는 편이 낫다** — 항목을 하나 더 추가하면 된다.

| 역할 | 가능한 것 |
|---|---|
| `viewer` | 포털, 실행 중 쿼리 조회, 헬스 조회 |
| `operator` | viewer + **쿼리 kill**, 감사 로그 조회 |
| `admin` | operator + 헬스 테스트/임계값 변경, 감사 로그 내보내기 |

**평문 `password:` 키는 거부된다.** 기동이 실패하며, 이는 의도된 동작이다 — 평문이 들어간 파일은 언젠가 커밋된다.

---

## 3. 로그인

```bash
curl -sk -X POST https://<tms-host>:8500/api/v1/login \
  -H 'Content-Type: application/json' \
  -c cookies.txt \
  -d '{"username":"syhcho","password":"<임시 비밀번호>"}'
```

```json
{"user":"syhcho","roles":["admin"],"must_change_password":true}
```

`must_change_password: true` 면 **다른 API는 전부 403**이다.

---

## 4. 비밀번호 변경 (임시 계정이면 필수)

```bash
curl -sk -X PUT https://<tms-host>:8500/api/v1/password \
  -H 'Content-Type: application/json' -b cookies.txt \
  -d '{"current_password":"<임시>","new_password":"<새 비밀번호>"}'
```

응답에 **새 해시**가 들어 있다.

```json
{
  "changed": true,
  "password_hash": "pbkdf2_sha256$600000$...$...",
  "persist_note": "config.secret.yaml 의 ... password_hash 를 이 값으로 교체하고 must_change_password 를 제거하라. 재시작 전까지만 유효하다."
}
```

> **⚠️ 반드시 파일에 반영하라.** 프로세스는 gitignore된 설정 파일을 소유하지 않아 스스로 쓰지 못한다. 반영하지 않으면 **재시작 시 임시 비밀번호로 되돌아간다.** 이 한계는 AD 연동으로 해소된다 (D-007).

```yaml
portal:
  local_users:
    syhcho:
      password_hash: "<응답의 새 해시>"
      roles: [admin]
      # must_change_password 줄을 지운다
```

---

## 5. 확인

```bash
curl -sk https://<tms-host>:8500/api/v1/me -b cookies.txt
```

```json
{"user":"syhcho","roles":["admin"],
 "capabilities":["export_audit","kill_query","manage_health","view_audit","view_health","view_portal","view_queries"]}
```

---

## 6. 문제 해결

| 증상 | 원인 | 조치 |
|---|---|---|
| 기동 실패: `session secret is required` | `TMS_SESSION_SECRET` 미설정 | §1 |
| 기동 실패: `plaintext 'password' is not accepted` | 설정에 평문 비밀번호 | `hash_password.py` 사용 |
| 401 `로그인이 필요하다` | 쿠키 없음/만료 | 재로그인 |
| 429 `계정이 잠겼다` | 5분 내 5회 실패 | 5분 대기 |
| 403 `임시 비밀번호를 먼저 변경해야 한다` | 정상 동작 | §4 |
| 재시작 후 옛 비밀번호로 되돌아감 | 새 해시 미반영 | §4 마지막 단계 |
| LB 뒤에서 세션이 자꾸 끊김 | 인스턴스별 `TMS_SESSION_SECRET` 불일치 | 전 인스턴스 동일 값으로 |

---

## 7. AD 연동 시 전환

`config.secret.yaml` 의 `portal.local_users` 를 비우면 로컬 인증이 **자동 비활성화**된다. 코드 제거가 필요 없다.
