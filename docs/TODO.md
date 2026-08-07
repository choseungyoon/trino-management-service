# TODO — 사내에서 진행할 작업

> **갱신 2026-08-07** · 소유자: Platform Owner
> 코드로 끝나지 않고 **사내 환경 접근이나 타 팀 협조가 필요한 것**만 모았다.
> 코드 작업은 `BOLTS.md`, 판정 이력은 `BACKLOG.md` 를 본다.

---

## 지금 상태 한 줄

R1(운영 콘솔)은 **실환경에 배포되어 동작 중**이다. 기능은 다 돌아가고, 남은 것은 **완료 판정(DoD)을 닫는 일**과 **인프라 선행 작업**이다.

---

## A. R1 마감 — 이것만 끝나면 R1이 닫힌다

### ⭐ A-1. NFR-PERF-03 프로덕션 실측 `[가장 중요]`

**왜**: 아래 §2에 따로 설명한다. 요약하면 **지금 프로덕션 코디네이터에 5초마다 폴링을 넣고 있는데, 그 비용을 실측한 적이 없다.**

```bash
cd /opt/tms && sudo -u tms git pull
read -rs TMS_TRINO_PASSWORD && export TMS_TRINO_PASSWORD

sudo -E /opt/tms/venv/bin/python scripts/measure_production_load.py \
  --coordinator https://<trino-a>:8443 \
  --coordinator https://<trino-b>:8443 \
  --pairs 6 --window 120

unset TMS_TRINO_PASSWORD
```

- **피크 시간대에** 돌린다. 한가할 때 재면 의미가 없다 (이유는 §2)
- 약 24분. 내부 CA 미신뢰면 `--insecure` 추가
- 먼저 `--dry-run --window 10 --pairs 2` 로 1분간 절차만 확인해도 된다

| 결과 | 다음 |
|---|---|
| 충족 (코드 0) | `PERF_MEASUREMENT.md` §0 "잠정" 제거 → **R1 DoD 닫힘** |
| 초과 (코드 1) | `PERF_MEASUREMENT.md` §6-2 — 폴링 주기 상향으로 대응 (설정만) |
| **판정 불가 (코드 2)** | **닫지 말 것.** `--pairs` 늘려 재측정 |

> collector를 멈추는 동안에도 **쿼리는 영향받지 않는다** (NFR-ISOLATION). 화면만 잠시 stale로 표시된다.

- [ ] 실측 수행
- [ ] 결과 공유 → 판정 및 문서 갱신

### A-2. 딥링크 설정 확인

`config.yaml` 의 `deeplinks` 가 비어 있으면 **링크가 렌더링되지 않는다**(죽은 링크를 만들지 않기 위한 의도된 동작). 아는 것부터 채우면 된다.

- [ ] `query_history.query_url_template` / `home_url` — 기존 쿼리 히스토리 프로젝트 URL
- [ ] `superset_url`
- [ ] `grafana.cluster_dashboard` — **S6 완료 후** (아래 C-2)
- [ ] `log.template` — **S7 완료 후** (아래 C-3). FR-LOG-DEEPLINK의 전제다

### A-3. 운영자 계정 정리

- [ ] 팀원별 계정 추가 (`scripts/hash_password.py --user <이름> --roles <역할>`)
- [ ] 각자 최초 로그인 후 비밀번호 변경
- [ ] **변경 응답의 새 해시를 `config.secret.yaml` 에 반영** — 빠뜨리면 재시작 시 임시 비밀번호로 되돌아간다
- [ ] 계정 공유 금지 확인 — 공유하면 감사 로그의 `actor` 가 전부 같아져 "누가 죽였나"에 답할 수 없다

역할: `viewer`(조회) / `operator`(+kill, 감사조회) / `admin`(+헬스변경, 감사내보내기)

### A-4. 인증서 정리

- [ ] nginx 인증서가 **사내 CA 발급분**인지 확인 (자체 서명이면 교체 티켓)
- [ ] 만료일 확인 및 갱신 절차 확보

---

## B. Gateway 후속 (회신 대기 중)

### B-1. Gateway API 계정 `[요청 완료, 회신 대기]`

- [ ] `API` 역할 계정 발급 수령
- [ ] Gateway에 **TLS 활성** 확인 — 없으면 인증 자체가 동작하지 않는다
- [ ] `backend/all` 실동작 검증

```bash
curl -sk -u '<api계정>:<비밀번호>' https://<gateway>:<port>/gateway/backend/all | python3 -m json.tool
```

기대: `name`, `proxyTo`, `active`, `routingGroup`, `externalUrl`

> **⛔ "읽기 전용" 역할은 없다.** 목록 조회에 필요한 `API` 역할은 문서상 *configure* 권한이라 **같은 자격증명으로 백엔드 변경도 가능하다.** 발급받으면 `tms-svc` 와 동급으로 보호할 것 (`/etc/tms/tms.env`, 600).

### B-2. S1 — 랜덤 라우팅 개선 `[Gateway 19 확정으로 착수 가능]`

**왜**: 현재 기본 라우터는 소스상 문자 그대로 `RANDOM.nextInt() % backends.size()` 다. **부하를 전혀 보지 않는다.** 클러스터 하나가 느려져도 여전히 절반이 그쪽으로 간다. 설정 몇 줄로 least-loaded 라우팅이 켜진다.

```yaml
clusterStatsConfiguration:
  monitorType: UI_API      # ⚠️ 기본 INFO_API 로는 통계가 안 모여 라우터가 무력화된다

modules:
  - io.trino.gateway.ha.module.QueryCountBasedRouterProvider

monitor:
  taskDelay: 1m
```

- [ ] 적용 + Gateway 재시작
- [ ] 검증: 적용 전후 두 클러스터의 `RunningQueries` 비교 — 느린 쪽 러닝 쿼리가 상대적으로 낮아져야 한다

---

## C. 인프라 선행 작업 (SETUP)

우선순위는 `BOLT_0_RESULT.md` 판정을 따른다.

### 🔴 C-1. S5 — Gateway DB를 VM1에서 분리 + HA `[최우선]`

**왜**: **현존 SPOF다.** Gateway 2대가 PostgreSQL 하나를 공유하는데 그 DB가 VM1에 얹혀 있다. VM1이 죽으면 두 Gateway가 동시에 DB를 잃는다.

`databaseCache` 를 켜둔 것은 안전망이지 대체재가 아니다. 캐시되는 것은 **백엔드 목록뿐**이고, 만료되면 라우팅이 멈춘다.

- [ ] DB를 별도 호스트로 분리
- [ ] HA 구성
- [ ] S4(LB 교체)의 선행 조건이기도 하다

### C-2. S6 — Prometheus + Grafana `[측정 기반]`

**왜**: "S1이 효과가 있었나", "부하가 늘고 있나"를 판정할 근거가 없다. `prometheus_scraper` 계정은 이미 만들어져 있으나 아직 사용되지 않는다.

- [ ] node_exporter + Prometheus + Grafana 기본 대시보드
- [ ] 완료 후 A-2의 `grafana.cluster_dashboard` 채우기

### C-3. S7 — 로그 수집 (Loki 또는 OpenSearch)

**왜**: **R1 FR-LOG-DEEPLINK의 전제다.** 지금은 로그 딥링크를 걸 대상이 없어 링크가 렌더링되지 않는다.

- [ ] Loki 또는 OpenSearch 구축
- [ ] 완료 후 A-2의 `log.template` 채우기

### C-4. S4 — LB IP HASH 교체 `[S5 이후]`

- [ ] S5 완료 후 착수

---

## D. 이월 (지금은 하지 않음)

| 항목 | 조건 |
|---|---|
| AD 연동 (D-007) | 로컬 계정은 임시. AD 사양 확보 후 |
| 쿼리 히스토리 프로젝트 통합 (D-001) | R1 안정화 후 |
| B4 — 히스토리 저장소 선정 | 위 통합 시점 |
| OPA 데이터 권한 연동 | R2 이후. 도입 시 NFR-PERF-03 **재측정 필요** (§T3-5) |

---

## 순서 제안

```
이번 주 복귀 직후   A-1 실측 (피크 시간대)  ← R1 마감 게이트
                   B-1 회신 오면 검증

그다음 (독립 병행)  C-1 S5   ← SPOF. 가장 위험한 항목
                   C-2 S6   ← 이후 모든 판단의 근거
                   C-3 S7   ← R1 딥링크 완성

Gateway 안정화 후   B-2 S1   ← 랜덤 라우팅 개선 (효과 판정에 C-2 필요)
                   C-4 S4
```

**A-1만 끝나면 R1은 닫힌다.** 나머지는 R2 진입 전 정비 작업이다.
