# 데모 콘솔 호스팅

사외에서 화면을 눌러볼 수 있게 데모를 공개 URL 로 띄우는 절차다.

> **⛔ 운영 배포가 아니다.** 실제 TMS 는 systemd 로 뜨고(`ops/systemd/`) 실제 PostgreSQL·코디네이터를 본다. 여기 있는 것은 **브라우저 테스트 하네스**다 — 인메모리 저장소, 스텁 Trino, 가짜 리소스 그룹 저장소. **클러스터를 가리킬 방법이 아예 없고**, 그 점이 이걸 노출해도 되는 이유다.

---

## Vercel 은 쓰지 마라

기술적인 이유가 있다. **Vercel 의 Python 런타임은 서버리스**다 — 요청마다 새 인스턴스가 뜰 수 있고, 인스턴스 사이에 메모리가 공유되지 않는다.

이 데모의 상태는 **전부 메모리에 있다.** 그룹 값을 고치면 다음 요청에서 사라지거나, 어떤 요청에서는 보이고 어떤 요청에서는 안 보인다. **편집 화면이 무작위로 되감기는 것처럼 보인다** — 확인하려고 띄운 것이 오히려 없는 버그를 만들어낸다.

**프로세스가 계속 살아 있는 플랫폼**이 필요하다.

| | |
|---|---|
| **Fly.io** | 권장. 프로세스가 상주하고 Dockerfile 을 그대로 쓴다 |
| **Render** | 무료 티어는 유휴 시 잠들고 첫 요청이 느리지만, 상태는 유지된다 |
| **Railway** | 동일하게 맞는다 |
| ~~Vercel~~ | ⛔ 위 이유로 부적합 |
| ~~Cloudflare Workers~~ | ⛔ Python WSGI/ASGI 상주 프로세스가 아니다 |

---

## 먼저 — 비밀번호를 바꿔야 한다

**이 저장소는 PUBLIC 이다** (D-002). 데모의 기본 비밀번호와 세션 비밀키는 `tests/browser/harness.py` 에 그대로 적혀 있다. 그대로 띄우면 **비밀번호가 없는 것과 같다.**

`tests/browser/demo.py` 는 `TMS_DEMO_TLS=0`(= 호스팅 모드)인데 비밀번호가 기본값이면 **기동을 거부한다.** 배포 로그의 경고는 아무도 읽지 않고, 실패하면 인터넷에 열린 운영 콘솔이 남기 때문이다.

```bash
TMS_DEMO_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")
TMS_DEMO_SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
```

---

## Fly.io

```bash
brew install flyctl && fly auth login

# 저장소 루트에서
fly launch --no-deploy --dockerfile deploy/demo/Dockerfile --name tms-demo

fly secrets set \
  TMS_DEMO_PASSWORD="$TMS_DEMO_PASSWORD" \
  TMS_DEMO_SESSION_SECRET="$TMS_DEMO_SESSION_SECRET"

fly deploy
fly open        # https://tms-demo.fly.dev
```

`fly launch` 가 만든 `fly.toml` 에서 `internal_port` 가 **8080** 인지 확인한다 (Dockerfile 의 `PORT` 와 같아야 한다).

## Render

1. New → Web Service → 이 저장소 연결
2. Runtime **Docker**, Dockerfile Path `deploy/demo/Dockerfile`
3. Environment 에 `TMS_DEMO_PASSWORD`, `TMS_DEMO_SESSION_SECRET` 추가
4. Create

---

## 노출되는 것과 노출되지 않는 것

| | |
|---|---|
| **보인다** | 화면 구성, 필드 이름, 검증 문구, `prod-a`/`prod-b` 라는 가짜 클러스터 이름 |
| **없다** | 실제 호스트명·IP·자격증명·쿼리·사용자. 데이터는 전부 하네스가 만든 것이다 |
| **불가능하다** | 클러스터 조회·변경. 실제 Trino 를 가리킬 설정 경로가 이 이미지에 없다 |

저장소가 이미 PUBLIC 이라 UI 소스와 화면 구조는 어차피 공개돼 있다. 그래도 **운영 콘솔처럼 보이는 것이 공개 URL 에 있다**는 사실은 의식적으로 결정할 값어치가 있다 — 특히 사내 사람이 실물로 착각할 여지가 있다면.

**확인이 끝나면 내려라.** 상시 운영할 물건이 아니다.

```bash
fly apps destroy tms-demo
```

---

## 로컬로 충분한 경우

같은 화면을 인증서 경고만 넘기면 로컬에서 볼 수 있다. 사외에서 봐야 하는 게 아니라면 이쪽이 항상 낫다.

```bash
<venv>/bin/python -m tests.browser.demo 8443
# https://127.0.0.1:8443
```
