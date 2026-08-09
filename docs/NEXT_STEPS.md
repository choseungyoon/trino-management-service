# NEXT_STEPS — 사내에서 확인·작업·결정할 것 전량

> **갱신 2026-08-09** · 소유자: Platform Owner
> 주말 작업분(Workload · Gateway · 안전 재시작 · Fleet)을 기준으로, **사람이 해야만 진행되는 것**만 모았다.
> 배포 절차 자체는 `runbooks/upgrade-r2-r3.md`. 코드 작업 이력은 `BOLTS.md`.

---

## 0. 한 장 요약

| 구분 | 개수 | 성격 |
|---|---|---|
| **결정** (D) | 4건 | 답이 나와야 코드가 움직인다 |
| **확인** (V) | 6건 | 사내에서 눈으로 봐야 아는 것 |
| **작업** (W) | 7건 | 절차·설정·타 팀 협조 |

**지금 가장 값이 큰 것 3개**
1. **V-1 배포 후 5개 화면 확인** — 나머지 전부의 전제
2. **D-1 `ExecuteQuery` 권한** — 답이 "주지 않는다"면 즉시 닫히고, FR-FL-02 를 영구 축소로 확정할 수 있다
3. **W-1 NFR-PERF-03 실측** — R1 DoD 를 닫는 마지막 항목이자, Workload 폴링 주기를 정할 유일한 근거

---

## 1. 결정 (D) — 답이 나와야 코드가 움직인다

### ⭐ D-1. TMS 에 `ExecuteQuery` 권한을 줄 것인가

**배경**: FR-FL-02(어느 워커가 discovery 에 조인하지 않았는지)는 `system.runtime.nodes` 조회가 필요하고, 그것은 **TMS 가 프로덕션에서 SQL 을 실행할 수 있게 된다**는 뜻이다. 실측에서 현재 TMS 계정은 `PERMISSION_DENIED` 를 받는다.

| | 주지 않는다 (현재 구현) | 준다 |
|---|---|---|
| 얻는 것 | 개수 불일치만 안다 ("12대 중 11대") | 미조인 워커를 **이름으로** 짚는다 |
| 비용 | 없음 | TMS 침해 시 임의 SQL 실행 가능. 폴링마다 쿼리 히스토리에 TMS 쿼리가 쌓인다 |

**권고: 주지 않는다.** 개수 불일치만으로도 "한 대 빠졌다"는 알 수 있고, 어느 대인지는 Ansible 로 확인하는 편이 싸다. 필드 하나와 바꾸기에는 권한이 너무 넓다.

- [ ] 결정 → `TRINO_VERIFIED.md` §T1-2-1 과 화면 문구 확정

### ⭐ D-2. 재시작 실행을 Ansible 자동화로 전환할 것인가 (D-009 재확인)

이미 "TMS 호스트 직접 실행"으로 결정되어 있고 코드도 그렇게 되어 있다. **다만 기본값은 `manual` 이며, 켜는 것은 별개의 행위다.**

- [ ] **먼저 `manual` 로 한 번 완주**해 볼 것 — 게이트(빈 클러스터 확인 → 헬스 확인)는 두 모드가 완전히 동일하다. 사고를 막는 부분은 이미 다 있다
- [ ] 그 다음 `cluster_ops.restart_mode: ansible` 전환 여부 결정
- [ ] 전환 시 확인: TMS 호스트가 **전 Trino 노드에 SSH 접근**을 갖게 된다는 점을 보안 담당과 공유했는가

### D-3. R2 범위 문서 불일치 (H1) — 어느 문서를 기준으로 할 것인가

`BACKLOG.md` 와 `REQUIREMENTS.md` 부록 B 가 3건에서 어긋난다. 전량 대조는 `DESIGN_R2.md` §7-1.

| 기능 | `BACKLOG.md` | 부록 B (v0.2) |
|---|---|---|
| FR-BENCHMARK | R3 | **R2** |
| FR-ROUTING-SVC | **R2** | R4 |
| FR-CATALOG | R3 | R4 |

**권고: 부록 B 기준.** 나중 문서이고 변경 이유까지 적혀 있다.

> **⛔ 어느 쪽으로 정하든 FR-BENCHMARK 는 지금 완성할 수 없다.** BM-05(프로덕션 쿼리 샘플)는 히스토리 프로젝트 데이터가, BM-02(CPU/Net/Disk 시계열)는 Prometheus(W-5)가 선행이다. 착수 가능한 것은 BM-01·03·04 뿐 — 모르고 "R2 에 넣는다"고 정하면 **절반짜리 기능이 하나 더 는다.**

- [ ] 기준 문서 확정 → 다른 쪽을 맞춤

### D-4. 다음 개발 슬라이스

남은 것과 상태:

| 후보 | 상태 | 막는 것 |
|---|---|---|
| **FR-CO-01** 설정 조회·변경 | 착수 가능 | 없음 (Ansible 실행기 재사용) |
| **FR-FL-04/05** 증설 스크립트 훅 + 진행 추적 | 착수 가능 | 없음 (같은 실행기·진행 로그 재사용) |
| **FR-FLEET-DRIFT** config 체크섬 | 부분 | 노드별 체크섬 수집용 새 플레이북 필요 |
| FR-FL-02 미조인 워커 식별 | 막힘 | **D-1** |
| FR-GW-04 databaseCache 폴백 표시 | 착수 가능 | 없음 |
| FR-SLO | 막힘 | 목표값(인간 결정) + 워크로드 데이터 |
| FR-CATALOG | 보류 | `catalog.management=dynamic` (experimental) 도입 결정 |

**권고: FR-FL-04/05.** 안전 재시작의 진행 로그·실행기·감사 구조를 그대로 쓰므로 새로 만들 것이 적고, "증설이 어디까지 갔나"는 지금 눈으로 볼 방법이 없는 것 중 하나다.

- [ ] 다음 슬라이스 지정

---

## 2. 확인 (V) — 사내에서 눈으로 봐야 아는 것

### ⭐ V-1. 배포 후 5개 화면 `[내일]`

`runbooks/upgrade-r2-r3.md` §9 순서대로. 위에서 막히면 아래는 볼 필요 없다.

- [ ] Overview — 기존과 동일한가 (회귀 확인)
- [ ] Workload — 그룹 트리, 컬럼 클릭 랭킹, 그룹 클릭 → 쿼리 목록
- [ ] Gateway — 백엔드 목록과 TMS 클러스터 대응
- [ ] Fleet — 노드 목록, 코디네이터 버전
- [ ] Safe Restart — 6단계 미리보기

### V-2. Fleet 이 실제로 노드를 보는가

- [ ] 전 워커가 "No answer" 면 → `node_url_template` 포트·스킴, 또는 **TMS 호스트 → 워커 HTTP 포트 방화벽**
- [ ] 손으로 먼저: `curl -sk https://<워커>:8443/v1/info` (인증 불필요)
- [ ] 인벤토리 워커 수와 `expected_workers` 가 맞는가 (`tms-config-check` 가 경고한다)

### V-3. Workload 가 그룹을 보는가

- [ ] 비어 있으면 → 마이그레이션 003, `resource-groups.json` 의 `jmxExport`, 또는 아직 아무 그룹도 쿼리를 안 받음
- [ ] **`jmxExport` 누락과 "아직 활동 없음"은 화면에서 구별되지 않는다.** 설정 파일을 직접 확인해야 한다

### V-4. 첫 안전 재시작 `[한가한 시간 · 예비 클러스터]`

- [ ] 시작 직후 **Gateway 화면에서 해당 백엔드가 비활성**인지 확인
- [ ] 진행 로그에서 드레인이 도는 것 확인
- [ ] **끝까지 갈 생각이 없으면 중단** — 중단은 트래픽을 반드시 되돌린다. 되돌아온 것을 Gateway 화면에서 재확인
- [ ] 감사 로그에 `CLUSTER_RESTART` 가 단계별로 남았는지

### V-5. Graceful shutdown 실증 `[워커 1대]`

**⚠️ 미해소 G-4.** 공식 문서는 graceful shutdown 에 대해 `allow-all`/`file` 만 언급하고 **OPA 를 언급하지 않는다.** OPA 로 인가된다는 결론은 소스 근거이므로 실증이 필요하다.

- [ ] 워커 한 대에 `access-control.properties` + Rego `WriteSystemInformation` 적용
- [ ] Fleet 화면에서 shutdown → **드레인 → 종료까지 관찰**
- [ ] **쿼리 실패 0건 확인** (FR-FL-03 의 AC)
- [ ] 최소 `2 × shutdown.grace-period` + 실행 중 task 시간이 걸린다 — 기본값이면 4분 이상. **그 전에 "멈췄다"고 판단하지 말 것**

### V-6. 감사 로그 append-only 재확인

- [ ] `tms_app` 으로 `UPDATE restart_sequence_event` / `DELETE` → **둘 다 실패해야 한다**

---

## 3. 작업 (W)

### ⭐ W-1. NFR-PERF-03 프로덕션 실측 `[R1 DoD 마지막 항목]`

**왜**: 지금 프로덕션 코디네이터에 5초마다 폴링을 넣고 있는데 **그 비용을 실측한 적이 없다.** 게다가 Workload 를 켜면 폴링마다 MBean 열거 1회 + 그룹당 읽기 1회가 추가된다.

```bash
cd /opt/tms && sudo -u tms git pull
read -rs TMS_TRINO_PASSWORD && export TMS_TRINO_PASSWORD
sudo -E /opt/tms/venv/bin/python scripts/measure_production_load.py \
  --coordinator https://<trino-a>:8443 --coordinator https://<trino-b>:8443 \
  --pairs 6 --window 120
unset TMS_TRINO_PASSWORD
```

- **피크 시간대에** 돌린다. 한가할 때 재면 의미가 없다
- 약 24분. 내부 CA 미신뢰면 `--insecure`
- 종료 코드 0=충족 / 1=초과(주기 상향으로 대응) / **2=판정 불가 → 닫지 말 것**

- [ ] 실측 → `PERF_MEASUREMENT.md` §0 "잠정" 제거 → **R1 DoD 닫힘**
- [ ] 결과에 따라 `workload.poll_interval_seconds` 확정

### W-2. Gateway API 계정 `[타 팀]`

- [ ] `API` 역할 계정 발급 (요청 내용: `runbooks/gateway-config-request.md`)
- [ ] Gateway **TLS 활성** 확인 — 없으면 인증이 동작하지 않는다
- [ ] **⛔ "읽기 전용" 역할은 없다.** 이 계정은 백엔드 변경 권한을 포함한다. `tms-svc` 와 동급 보호 (`/etc/tms/tms.env`, 600)

> **안전 재시작의 전제 조건이다.** Gateway 없이는 기능 자체가 켜지지 않는다.

### W-3. 워커 OPA 배포 `[타 팀 · V-5 선행]`

- [ ] **모든 워커에** `etc/access-control.properties` — 문서 원문: *"These configuration must be present on all workers."*
- [ ] Rego 에 TMS 계정 `WriteSystemInformation` 허용
- [ ] **⚠️ 신규 실패 모드**: 워커 OPA 가 죽으면 shutdown 이 거부된다 → 워커 OPA 헬스를 감시 대상에 포함

### W-4. 딥링크 채우기

비어 있으면 **링크가 렌더링되지 않는다**(죽은 링크를 만들지 않는 의도된 동작). 아는 것부터.

- [ ] `query_history.query_url_template` / `home_url`
- [ ] `superset_url`
- [ ] `grafana.cluster_dashboard` — **W-5 이후**
- [ ] `log.template` — **W-6 이후**. FR-LOG-DEEPLINK 의 전제다

### 🔴 W-5. Gateway DB 를 VM1 에서 분리 + HA `[현존 SPOF · 최우선]`

Gateway 2대가 PostgreSQL 하나를 공유하는데 그 DB 가 VM1 에 얹혀 있다. **VM1 이 죽으면 두 Gateway 가 동시에 DB 를 잃는다.**

`databaseCache`(10분)는 안전망이지 대체재가 아니다 — 캐시되는 것은 **백엔드 목록뿐**이고 만료되면 라우팅이 멈춘다.

- [ ] DB 별도 호스트 분리 + HA
- [ ] W-7(LB 교체)의 선행 조건

> **지금이 가장 싼 시기다.** 사용자 약 50명이고 아직 운영 서비스가 아니다. "위험하니 나중에"가 아니라 **"쉬울 때 미리"** 다.

### W-6. Prometheus + Grafana / 로그 수집

- [ ] node_exporter + Prometheus + Grafana — "부하가 늘고 있나"를 판정할 근거가 지금 없다. `prometheus_scraper` 계정은 이미 있다
- [ ] Loki 또는 OpenSearch — **FR-LOG-DEEPLINK 의 전제**

### W-7. 운영 위생

- [ ] 팀원 계정 추가 (`scripts/hash_password.py`), 최초 로그인 후 비밀번호 변경, **새 해시를 `config.secret.yaml` 에 반영**(빠뜨리면 재시작 시 임시 비밀번호로 되돌아간다)
- [ ] **계정 공유 금지** — 공유하면 감사 로그의 `actor` 가 전부 같아져 "누가 죽였나"에 답할 수 없다
- [ ] nginx 인증서가 사내 CA 발급분인지 + 만료일·갱신 절차
- [ ] LB IP HASH → 세션 어피니티 교체 (**W-5 이후**)

---

## 4. 이월 (지금은 하지 않음)

| 항목 | 조건 |
|---|---|
| AD 연동 (D-007) | 로컬 계정은 임시. AD 사양 확보 후 |
| 쿼리 히스토리 프로젝트 통합 (D-001) | R1 안정화 후. B4(저장소 선정)도 이 시점 |
| OPA 데이터 권한 연동 | 도입 시 **NFR-PERF-03 재측정 필요** (§T3-5) |
| FR-SLO | 목표값(인간 결정) + 워크로드 데이터 둘 다 필요 |

---

## 5. 권장 순서

```
내일          V-1 배포 확인 → V-2/V-3 (Fleet·Workload 가 실제로 보이는가)
이번 주       W-1 실측 [피크 시간] · W-2 Gateway 계정 요청
              D-1 · D-3 결정 (둘 다 회의 없이 답할 수 있다)
Gateway 후    V-4 첫 안전 재시작 [한가한 시간, 예비 클러스터]
그 다음       W-3 → V-5 graceful shutdown 실증 [워커 1대]
병행          🔴 W-5 Gateway DB 분리 — 사용자가 늘기 전에
개발 재개     D-4 에서 지정한 슬라이스
```

**W-5 만은 위 순서와 무관하게 따로 굴리는 것을 권한다.** 나머지는 기능이고, 그것은 지금 존재하는 단일 장애점이다.
