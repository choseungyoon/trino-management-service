# TODO — 사람이 해야만 진행되는 것 전량

> **갱신 2026-08-27** · 소유자: Platform Owner
> **이 프로젝트의 "내가 할 일" 목록은 이 문서 하나다.** 예전에는 `NEXT_STEPS.md`(무엇을)와
> `runbooks/onsite-checklist.md`(어떤 순서로)로 나뉘어 있었고, 같은 항목이 두 곳에서 서로 다른
> 상태를 말하고 있었다. 둘을 합치고 끝난 것은 지웠다.
>
> **여기에 없는 것**: 명령의 원본(각 런북), 결정의 근거(`DECISIONS.md`), 코드 작업 이력(`BOLTS.md`).
> 같은 명령을 두 곳에 적으면 한쪽이 조용히 낡는다.
>
> `docs/WORK_BOARD.md` 는 이 문서의 경쟁자가 아니다 — 사내 보드(`/work`)의 **자동 생성 스냅샷**이고,
> 사외에서 보드를 읽을 유일한 방법이다. 상태가 어긋나면 **이 문서가 이긴다.**

---

## 0. 한 장 요약

| 구분 | 남은 것 | 성격 |
|---|---|---|
| 🔴 **막고 있는 것** | 1건 (V-10) | 이게 안 되면 나머지가 전부 같은 원인이다 |
| **결정** (D) | 2건 | 답이 나와야 코드가 움직인다. **사내에 안 들어가도 된다** |
| **사내 확인** (V) | 6건 | 사내에서 눈으로 봐야 아는 것 |
| **작업** (W) | 6건 | 절차·설정·타 팀 협조 |

**지금 값이 큰 것 3개**

1. 🔴 **V-10 콘솔이 뜨는가** — 화면이 통째로 바뀌었고 사내 실데이터로는 처음이다
2. 🔴 **W-5 Gateway DB 분리** — 현존하는 단일 장애점. 사용자가 늘기 전이 가장 싸다
3. **W-1 NFR-PERF-03 실측** — R1 DoD 를 닫는 마지막 항목

---

## 1. 다음에 사내에 들어가면 — 이 순서로

**한 번에 하나씩, 게이트를 통과하고 다음으로 간다. 위에서 막히면 아래는 볼 필요가 없다.**

### 이미 끝난 것 — 다시 하지 않는다

| | 상태 |
|---|---|
| 마이그레이션 `010`~`019` | ✅ 전량 적용 (`018`/`019` 는 2026-08-26) |
| React 콘솔 기동 · 벤치마크 실행 · 추이 차트 | ✅ 2026-08-27 사내 확인 |
| 작업 보드 초기 적재 · 검증 (V-7) | ✅ |
| Gateway 설정 · API 계정 (구 W-2) | ✅ |
| 벤치마크 실행 확인 | ✅ 클러스터에서 도는 것까지 확인 |
| 롤백 준비 · 기동 회귀 확인 | ✅ |

> ⛔ **앞 번호 마이그레이션을 다시 돌리지 않는다.** `010`·`012`·`016`·`018` 은 각각 감사 액션
> 제약을 `DROP` 후 다시 만든다. 나중 번호를 적용한 뒤 앞 번호를 재실행하면 **뒤에 추가된 액션이
> 사라지고**, 그 결과는 화면 오류가 아니라 **기능이 조용히 멈추는 것**이다 (감사 못 남기는 쓰기는
> 절대규칙 3 으로 거부된다). 2026-08-24 로컬 실측이며 추정이 아니다.

---

### 🔴 1-0. 마이그레이션 `020` `021` `022` `[먼저]`

`020`/`021` 은 벤치마크 주기 실행(FR-BM-07 · D-017)이, `022` 는 설정 조회
(FR-CO-01 · D-018)가 쓴다. **적용 전에는 해당 화면만 "사용할 수 없음" 으로
나오고 나머지는 전부 정상 동작한다.**

- [ ] `git pull` → `pip install -e .`
- [ ] `020` `021` `022` 를 **번호순으로** `tms_owner` 로 적용
- [ ] `tms-config-check` → `benchmark_schedule` 테이블 ·
      `BENCHMARK_SCHEDULE_CHANGE` 액션 · `config` snapshot kind 를 확인한다

> ⛔ **앞 번호를 다시 돌리지 않는다.** `020` 도 감사 액션 제약을 `DROP` 후
> 다시 만든다 — 위의 경고가 그대로 적용된다.

### 🔴 1-1. V-10 — 콘솔이 뜨는가 `[다른 무엇보다 먼저]`

**2026-08-27 에 화면이 통째로 바뀌었다** (D-016). 서버 렌더 Jinja 콘솔은 삭제됐고 `/` 는
커밋된 React 번들이다. **사내 실데이터로는 한 번도 돌려 본 적이 없다.**

D-016 이 명시적으로 포기한 것이 여기서 처음 현실이 된다: **번들이 못 뜨면 빈 화면이다.**
서버 렌더에는 없던 실패 모드다.

- [ ] `git pull` → `pip install -e .` (Artifactory 프록시 경유)
- [ ] `ls venv/bin/tms-*` → `tms-api` `tms-collector` `tms-config-check` `tms-work-export`
      <br>*콘솔 스크립트는 설치 시점에 만들어진다. `git pull` 만으로는 안 생긴다 (`-e` 여도)*
- [ ] `systemctl restart tms-collector tms-api` → `journalctl -u tms-api -n 50`
- [ ] 브라우저로 `/` → **로그인 화면이 그려지는가**
- [ ] 로그인 → Overview 가 그려지는가
- [ ] 왼쪽 내비게이션 **12개 항목이 각각 열리는가** (하나라도 빈 화면이면 거기서 멈춘다)
- [ ] `curl -sk https://<host>:8500/health` → `{"status":"ok"}`
      <br>*⛔ 이건 프로브지 화면이 아니다. 헬스 **화면**은 `/cluster-health` 다*

**빈 화면이 나오면** — 개발자 도구 Network 에서 `/static/index-*.js` 를 본다.

| | 원인 | 조치 |
|---|---|---|
| `404` | `pip install` 이 번들을 안 넣었다 | `deploy.md` §UI 파일 문제. **되돌릴 일이 아니다** |
| `200` 인데 빈 화면 | 알 수 없다 | **되돌린다** (아래) |

**되돌리는 방법**: `git checkout f3cec7a~1` — 컷오버 직전이다. 서버 렌더 콘솔이 그대로 있고
React 콘솔은 `/app` 에 있다. 되돌렸다면 그 이유가 D-016 의 "뒤집는 조건 1" 이므로 기록한다.

**주소가 바뀐 것들** — 북마크와 사내 위키를 고친다:

| 전 | 후 |
|---|---|
| `/clusters/<c>/health` | `/cluster-health?cluster=<c>` — ⛔ `/health` 는 liveness 프로브다 |
| `/clusters/<c>/resource-groups` | `/resource-groups?cluster=<c>` |
| `/clusters/<c>/fleet` | `/fleet?cluster=<c>` |
| `/clusters/<c>/restart` | `/restart?cluster=<c>` |
| `/benchmarks/sets` | `/benchmark/sets` |
| `/benchmarks/<id>` | `/benchmark/runs/<id>` |

> ⛔ **번들은 저장소에 커밋돼 있다.** 배포 호스트에 Node 는 없고 필요하지도 않다.
> 화면이 옛날 그대로라면 `pip install` 이 안 된 것이지 빌드가 필요한 것이 아니다.

> **게이트**: 콘솔이 안 뜨면 아래 V 항목은 전부 같은 원인이다. 여기서 멈춘다.

---

### 1-2. V-2 — Fleet 이 실제로 노드를 보는가

- [ ] `/fleet?cluster=<c>` — 노드 목록과 코디네이터 버전이 나오는가
- [ ] 전 워커가 "No answer" 면 → `node_url_template` 포트·스킴, 또는 **TMS 호스트 → 워커 HTTP 포트 방화벽**
- [ ] 손으로 먼저: `curl -sk https://<워커>:8443/v1/info` (인증 불필요)
- [ ] 인벤토리 워커 수와 `expected_workers` 가 맞는가 (`tms-config-check` 가 경고한다)

### 1-3. V-3 — Workload 가 그룹을 보는가

- [ ] `/workload` — 그룹 트리, 컬럼 클릭 랭킹, 그룹 클릭 → 쿼리 목록
- [ ] 비어 있으면 → 마이그레이션 003, `resource-groups.json` 의 `jmxExport`, 또는 아직 아무 그룹도 쿼리를 안 받음
- [ ] ⛔ **`jmxExport` 누락과 "아직 활동 없음"은 화면에서 구별되지 않는다.** 설정 파일을 직접 확인해야 한다

### 1-4. V-9 — 리소스 그룹 편집 `[마이그레이션 010/011 은 이미 올라가 있다]`

**⚠️ 이 화면의 쓰기는 프로덕션 쿼리 수용 제어를 바꾼다.** 10초 안에 코디네이터에 반영되고,
재시작이라는 관문이 없다. 처음에는 **영향 없는 그룹 하나**로 연습한다.

**선행 설정**

- [ ] Trino 가 **이미 `db` 매니저를 쓰고 있는지 먼저 확인** — 아직 `file` 이면 고칠 대상이 없다
- [ ] `tms_app` 에 `trino_resource_groups` schema 권한 (`resource-groups-db.md` 의 3줄 GRANT)
- [ ] `resource_groups.enabled: true`, `schema:` 를 코디네이터의 `?currentSchema=` 와 일치
- [ ] `group_provider_configured: false` — **`etc/group-provider.properties` 가 없으므로 이게 정확한 값이다.**
      `false` 여야 편집 화면이 `user_group_regex` 셀렉터를 만들 때 경고한다

**확인**

- [ ] 좌측 네비에 **Resource Groups**, 설정 트리가 실행 중인 그룹과 대조된다
- [ ] 값 하나 수정 → 사유 요구 → 저장 → **10초 내 반영**
- [ ] 일부러 잘못된 값(동시 실행 0) → **거부되고 입력한 값이 사라지지 않는다**
- [ ] 이력 화면에 리비전 · **되돌리기** → 이전 트리 복원 + 되돌리기 자체가 새 리비전
- [ ] 감사 로그에 `RESOURCE_GROUP_CHANGE` / `RESOURCE_GROUP_REVERT` 가 사유와 함께
- [ ] `user_group_regex` 셀렉터를 만들어 보면 **경고가 뜬다**
- [ ] `tms_app` 으로 `UPDATE resource_group_revision` / `DELETE` → **둘 다 실패**
      <br>*실패하지 않으면 `011` 이 안 들어갔다. 이력이 고쳐질 수 있으면 이력이 아니다*

### 1-5. V-8 — 벤치마크 나머지 `[실행 자체는 확인됨]`

돌아가는 것은 봤다. **아직 안 본 것은 차트와 비교, 그리고 세트를 고쳤을 때 과거가 안전한가**다.

- [ ] `/benchmark` 에 클러스터가 전부 나오고, 운영 중인 것은 **`Serving traffic`** 으로
      표시되며 이유가 문장으로 붙는다. **체크박스는 잠기지 않는다** (D-015)
- [ ] 실행 목록의 `Cluster was` 열 · 실행 화면의 노란 배너
- [ ] ⛔ 같은 클러스터에 하나 더 실행 → **거부** (두 실행이 서로를 측정한다)
- [ ] 같은 세트를 **다른 클러스터**에서 실행 → 실행 화면에서 비교 → 쿼리별 차이와 판정
- [ ] ⛔ `Quiet` 실행과 `Serving traffic` 실행을 비교 → **조건이 다르다는 경고**.
      이게 게이트를 대신하는 안전장치다
- [ ] **차트** — `/benchmark/sets/<키>/queries/<이름>/history` 에서 추이가 그려지는가
      <br>*클러스터당 실행이 2건 미만이면 선을 안 그리고 표만 보여 준다. 그게 정상이다*
- [ ] 쿼리 하나의 SQL 을 고친 뒤 **고치기 전 실행을 다시 연다** → 그 실행이 쓴 SQL 은 그대로다
- [ ] 고치기 전후 실행을 비교 → 그 행에 **변경됨** 표시
- [ ] 실행 중에 같은 세트를 고쳐 본다 → **거부**
- [ ] `DELETE FROM ...` 저장 → **거부**. `-- 무해함` 다음 줄에 숨겨도 → **거부**
- [ ] 사유 없이 저장 → **거부**. 감사에 `BENCHMARK_QUERY_CHANGE`
- [ ] `tms_app` 으로 `UPDATE benchmark_result` / `DELETE` → **둘 다 실패**

### 1-5-1. V-11 — 벤치마크 스케줄 `[020/021 적용 후 · 새 기능]`

⛔ **사람이 없는 시각에 운영 클러스터에 쓰는 유일한 기능이다** (D-017). 처음에는
**가벼운 세트 · 반복 1회 · 하루 주기**로 시작한다.

- [ ] `/benchmark/schedules` → 상단 경고가 보이고, 스케줄 표가 그려진다
- [ ] 스케줄 생성 → 사유 없이 저장 시 **거부**
- [ ] 최소 주기(15분) 미만 → **거부되고 이유가 "용량" 을 말한다**
- [ ] 다음 실행 시각이 맞고, 그 시각에 **실제로 실행이 생긴다**
- [ ] 그 실행의 감사 기록에 **스케줄을 만든 사람**과 **스케줄의 사유**가 남는다
- [ ] 실행 목록에서 그 행이 **"on a schedule"** 로 표시된다
- [ ] 실행 중인 클러스터에 회차가 오면 → **건너뛰고, 실패로 세지 않는다**
      (`consecutive_failures` 가 0 그대로)
- [ ] 스케줄 편집·삭제에 `BENCHMARK_SCHEDULE_CHANGE` 가 사유와 함께 남는다
- [ ] 스케줄 삭제 → **그 스케줄이 만든 실행은 그대로 남는다**
- [ ] `tms_app` 으로 `UPDATE benchmark_schedule` → 성공해야 한다
      (설정이지 증거가 아니다)

> **자동 정지는 일부러 재현하지 않아도 된다.** 3회 연속 실패해야 걸리고,
> 그 상태는 화면에 **"paused by TMS"** 로 사유와 함께 나온다.

> ⚠️ **무거운 세트를 운영 클러스터에 돌리는 것 자체가 부하다.** 가벼운 세트 · 반복 1회로
> 시작한다. 재려던 느려짐을 스스로 만들 수 있다 (D-015).

### 1-5-2. V-12 — 설정 조회 · 드리프트 `[022 적용 후 · 새 기능]`

⛔ **읽기만 한다.** `docs/templates/collect-config.yml` 에는 노드를 바꾸는
태스크가 하나도 없다. 배포는 아직 만들지 않았다 (D-018 2·3단계).

**선행 — ansible 전환과 같이 한다 (§2 D-2)**

- [ ] `docs/templates/collect-config.yml` 를 `/etc/tms/ansible/` 에 설치
- [ ] `trino_etc` · `trino_log` 를 실제 경로에 맞춘다 (파일 상단 주석)
- [ ] `config.yaml` 에 경로를 넣는다:
      ```yaml
      cluster_ops:
        config_scan:
          playbook: /etc/tms/ansible/collect-config.yml
          development_clusters: [<개발 클러스터 이름>]
      ```
- [ ] 손으로 먼저 한 번: `ansible-playbook -i <인벤토리> collect-config.yml`
      → `TMS-CONFIG-SCAN {...}` 줄이 호스트마다 하나씩 나오는가

**확인**

- [ ] `/cluster-config` → **Read the nodes** → 노드 표가 채워진다
- [ ] ⛔ **코디와 워커의 차이가 드리프트로 안 나온다** (역할별로 비교한다)
- [ ] 워커끼리 값이 다르면 **나온다** — 없으면 일부러 한 대만 고쳐 본다
- [ ] `etc/node.properties` 는 **Expected differences** 로 따로 나온다
- [ ] ⛔ **카탈로그는 체크섬만** 나온다. 내용도 비밀번호도 화면에 없다
- [ ] Known properties 열에 숫자가 채워진다 (수백 개)
      <br>*비어 있으면 `trino_log` 경로가 틀렸거나 로그가 로테이션된 것이다 —
      이게 3단계 배포의 오타 검사 재료다*
- [ ] 조회자 계정 → 표는 보이고 **Read the nodes 버튼이 없다**
- [ ] 개발 클러스터에서 워커 한 대를 내리고 스캔 → **드리프트로 안 나온다**

### 1-6. V-6 — 감사 append-only 재확인

- [ ] `tms_app` 으로 `UPDATE restart_sequence_event` / `DELETE` → **둘 다 실패**

### 1-7. W-1 — NFR-PERF-03 프로덕션 실측 `[R1 DoD 마지막 항목 · 피크 시간대]`

**왜**: 지금 프로덕션 코디네이터에 5초마다 폴링을 넣고 있는데 **그 비용을 실측한 적이 없다.**
Workload 를 켜면 폴링마다 MBean 열거 1회 + 그룹당 읽기 1회가 추가된다.

```bash
cd /etc/trino-management-service && sudo -u tms git pull
read -rs TMS_TRINO_PASSWORD && export TMS_TRINO_PASSWORD
sudo -E /etc/trino-management-service/venv/bin/python scripts/measure_production_load.py \
  --coordinator https://<trino-a>:8443 --coordinator https://<trino-b>:8443 \
  --pairs 6 --window 120
unset TMS_TRINO_PASSWORD
```

- **피크 시간대에** 돌린다. 한가할 때 재면 의미가 없다. 약 24분. 내부 CA 미신뢰면 `--insecure`
- 종료 코드 0=충족 / 1=초과(주기 상향으로 대응) / **2=판정 불가 → 닫지 말 것**

- [ ] 실측 → `PERF_MEASUREMENT.md` §0 "잠정" 제거 → **R1 DoD 닫힘**
- [ ] 결과에 따라 `workload.poll_interval_seconds` 확정

### 1-8. V-5 — Graceful shutdown 실증 `[워커 1대 · W-3 선행]`

**⚠️ 미해소 G-4.** 공식 문서는 graceful shutdown 에 대해 `allow-all`/`file` 만 언급하고
**OPA 를 언급하지 않는다.** OPA 로 인가된다는 결론은 소스 근거이므로 실증이 필요하다.

- [ ] 워커 한 대에 `access-control.properties` + Rego `WriteSystemInformation` 적용
- [ ] Fleet 화면에서 shutdown → **드레인 → 종료까지 관찰**
- [ ] **쿼리 실패 0건 확인** (FR-FL-03 의 AC)
- [ ] 최소 `2 × shutdown.grace-period` + 실행 중 task 시간. 기본값이면 4분 이상 —
      **그 전에 "멈췄다"고 판단하지 말 것**

---

### 1-9. 끝나고

- [ ] `tms-work-export` 재실행 → `docs/WORK_BOARD.md` 갱신 → 커밋
- [ ] **보드 정리** (§4 의 목록 그대로)
- [ ] 막힌 것이 있으면 상태를 `blocked` 로 옮기고 **무엇이 막는지** 를 적는다
- [ ] 안 켠 것이 있으면 그것도 보드에 남긴다 — **안 켠 것과 못 켠 것은 다르다**

---

## 2. 결정 (D) — 사내에 안 들어가도 답할 수 있다

### 🆕 D-5. 컷오버를 유지할 것인가

2026-08-27 에 서버 렌더 콘솔(`src/tms/web/`)을 삭제했다. D-016 의 계획된 마지막 단계이고,
그 결정문이 *"두 벌을 동시에 유지하는 것이 이 결정이 피하려는 바로 그것"* 이라고 적고 있다.

**컷오버가 아니었으면 못 찾았을 버그 4개**가 나왔다 — 특히 `GET /api/v1/restarts/{id}` 가
executor 를 폴링하지 않던 것은 **D-2 를 켜는 순간 터졌을 버그**다.

**권고: 되돌리지 않는다.** 다만 V-10 에서 콘솔이 안 뜨면 그 자리에서 되돌린다 (§1-1 의 표).

- [ ] V-10 결과를 보고 확정

### ~~D-2. 재시작 실행을 Ansible 자동화로 전환할 것인가~~ — ✅ **해소 (2026-08-27): 전환한다**

전 Trino 노드 SSH 통신 확인 완료 → `cluster_ops.restart_mode: ansible`. 기록은 `DECISIONS.md` D-009 의 2026-08-27 추가분.

**켜기 전 준비** (사내에서 할 것, §1 에도 있다):

- [ ] Ansible 설치 · `binary` 는 **절대경로**
- [ ] SSH 키는 `/etc/tms/ssh/` — `ProtectHome=true` 라 `/home/tms/.ssh` 는 못 읽는다
- [ ] 유닛 재배포로 `StateDirectory=trino-management-service` 반영
      <br>*(ansible-core 는 쓰기 가능한 `HOME` 없이 import 단계에서 죽는다 — exit 5)*
- [ ] `tms-config-check` 로 확인
- [ ] `manual` 로 한 번 완주해 본 뒤에 전환 (게이트는 두 모드가 동일하다)

> 절차: `upgrade-r2-r3.md` §4-3-1
>
> ⛔ **이제 이 권한 위에 설정·카탈로그 배포까지 얹힌다** (D-018). SSH 가 쓰이는
> 경로가 하나에서 여럿으로 늘었다는 뜻이므로, 보안 담당과 공유한 범위가
> "재시작" 이었다면 다시 이야기해야 한다.

### D-4. 다음 개발 슬라이스

| 후보 | 상태 | 막는 것 |
|---|---|---|
| **FR-CO-01** 설정 조회·변경 | 🔄 **착수** (D-018 1단계) | — |
| **FR-FLEET-DRIFT** config 체크섬 | 부분 | 노드별 체크섬 수집용 새 플레이북 — **사람이 써야 한다** |
| FR-FL-02 미조인 워커 식별 | 착수 가능 | ~~D-1~~ 해소됨 (D-012, `ExecuteQuery` 부여) |
| FR-SLO | 막힘 | 목표값(인간 결정) + 워크로드 데이터 |
| FR-CATALOG | 보류 | `catalog.management=dynamic` (experimental) 도입 결정 |
| ~~FR-GW-04~~ | ⛔ **미충족 확정** — Gateway 가 캐시 적중 신호를 내지 않는다 | Gateway 쪽 엔드포인트 |

**FR-CO-01 착수 (2026-08-27).** D-018 의 1단계 = 조회 + 드리프트. 2단계(카탈로그 배포)와
3단계(`config.properties` 편집)는 1단계가 만들어 내는 **유효 프로퍼티 목록**을 안전장치로
쓰므로 순서를 바꾸지 않는다.

- [x] 다음 슬라이스 = FR-CO-01 (D-018)

---

## 3. 작업 (W) — 절차 · 설정 · 타 팀

### 🔴 W-5. Gateway DB 를 VM1 에서 분리 + HA `[현존 SPOF · 최우선]`

Gateway 2대가 PostgreSQL 하나를 공유하는데 그 DB 가 VM1 에 얹혀 있다.
**VM1 이 죽으면 두 Gateway 가 동시에 DB 를 잃는다.**

`databaseCache`(10분)는 안전망이지 대체재가 아니다 — 캐시되는 것은 **백엔드 목록뿐**이고
만료되면 라우팅이 멈춘다.

- [ ] DB 별도 호스트 분리 + HA
- [ ] W-7(LB 교체)의 선행 조건

> **지금이 가장 싼 시기다.** 사용자 약 50명이고 아직 운영 서비스가 아니다.
> "위험하니 나중에" 가 아니라 **"쉬울 때 미리"** 다. 이것만은 아래 순서와 무관하게 따로 굴린다.

### W-3. 워커 OPA 배포 `[타 팀 · V-5 의 선행]`

- [ ] **모든 워커에** `etc/access-control.properties` — 문서 원문: *"These configuration must be present on all workers."*
- [ ] Rego 에 TMS 계정 `WriteSystemInformation` 허용
- [ ] ⚠️ **신규 실패 모드**: 워커 OPA 가 죽으면 shutdown 이 거부된다 → 워커 OPA 헬스를 감시 대상에 포함

### W-4. 딥링크 채우기

비어 있으면 **링크가 렌더링되지 않는다** (죽은 링크를 만들지 않는 의도된 동작). 아는 것부터.

- [ ] `query_history.query_url_template` / `home_url`
- [ ] `superset_url`
- [ ] `grafana.cluster_dashboard` — **W-6 이후**
- [ ] `log.template` — **W-6 이후**. FR-LOG-DEEPLINK 의 전제다

### W-6. Prometheus + Grafana / 로그 수집

- [ ] node_exporter + Prometheus + Grafana — "부하가 늘고 있나"를 판정할 근거가 지금 없다.
      `prometheus_scraper` 계정은 이미 있다. **FR-BM-02 의 전제**
- [ ] Loki 또는 OpenSearch — **FR-LOG-DEEPLINK 의 전제**

### W-7. 운영 위생

- [ ] 팀원 계정 추가 (`scripts/hash_password.py`), 최초 로그인 후 비밀번호 변경,
      **새 해시를 `config.secret.yaml` 에 반영** (빠뜨리면 재시작 시 임시 비밀번호로 되돌아간다)
- [ ] ⛔ **계정 공유 금지** — 공유하면 감사 로그의 `actor` 가 전부 같아져 "누가 죽였나"에 답할 수 없다
- [ ] nginx 인증서가 사내 CA 발급분인지 + 만료일·갱신 절차
- [ ] LB IP HASH → 세션 어피니티 교체 (**W-5 이후**)

---

## 4. 보드 정리 `[사내에서 /work 로, 5분]`

**사외에서는 손댈 수 없다.** 보드는 사내망 DB 에 있다.

- [ ] `W-11` Vite + React 스캐폴드 → **done**
- [ ] `W-12` 화면 12개를 React 로 이전 → **done**
- [ ] `W-8` 마이그레이션 010~019 적용 → **done**
- [ ] `W-2` Gateway API 역할 계정 → **done**
- [ ] `V-8` 벤치마크 검증 → **in_progress** (실행은 확인, 비교·차트 남음)
- [ ] `D-2` → **done** (ansible 전환 확정) · `D-5`(컷오버 유지) 를 새로 올린다
- [ ] `FR-CO-01` · `FR-FD-01` · `FR-FD-02` → **in_progress** (D-018 1단계)
- [ ] 근거 문서 경로가 `docs/NEXT_STEPS.md` / `docs/runbooks/onsite-checklist.md` 인 항목들
      → **`docs/TODO.md`** 로 고친다 (둘 다 이 문서로 합쳐졌다)

---

## 5. 이월 (지금은 하지 않음)

| 항목 | 조건 |
|---|---|
| AD 연동 (D-007) | 로컬 계정은 임시. AD 사양 확보 후 |
| 쿼리 히스토리 프로젝트 통합 (D-001) | R1 안정화 후. B4(저장소 선정)도 이 시점 |
| OPA 데이터 권한 연동 | 도입 시 **NFR-PERF-03 재측정 필요** |
| FR-SLO | 목표값(인간 결정) + 워크로드 데이터 둘 다 필요 |
| FR-BM-02 / FR-BM-05 | W-6(Prometheus) · 히스토리 프로젝트 통합이 각각 선행 |

---

## 6. 권장 순서

```
사내 들어가면  🔴 020/021 마이그레이션 → V-10 콘솔이 뜨는가  ← 게이트
               V-2 / V-3  (Fleet · Workload 가 실제로 보는가)
               V-9 리소스 그룹 편집 · V-8 벤치마크 나머지 · V-11 스케줄
               V-12 설정 조회·드리프트 (D-2 전환과 같이)
               W-1 실측 [피크 시간대]  ← R1 DoD 가 닫힌다
               §1-9 끝나고 · §4 보드 정리

사외에서       D-5 컷오버 유지 여부 (V-10 결과를 보고)
               D-4 다음 슬라이스
               → 셋 다 회의 없이 답할 수 있다

따로 굴린다    🔴 W-5 Gateway DB 분리 — 위 순서와 무관하게
               W-3 워커 OPA (→ V-5 graceful shutdown 실증)
               W-6 Prometheus / 로그 (→ W-4 딥링크)
```

---

## 부록. 기능을 끄는 법 (문제가 나면 먼저 이것)

네 기능 모두 **기존 화면과 독립**이다. 문제가 나면 기능만 끄는 것이 먼저다.

| 증상 | 조치 |
|---|---|
| 벤치마크만 문제 | `benchmark.enabled: false` → `tms-api` 재시작 |
| 스케줄만 문제 | 화면에서 해당 스케줄을 **끈다**. 전부 끄려면 `benchmark.enabled: false` — 스케줄은 벤치마크의 일부다 |
| 설정 조회만 문제 | `cluster_ops.config_scan.playbook` 을 비운다 → 재시작. 화면이 사라지고 **다른 것은 아무 영향 없다** (읽기 전용이라 노드에 남긴 것도 없다) |
| 리소스 그룹 편집만 문제 | `resource_groups.enabled: false` → 재시작. **Trino 의 db 매니저와는 무관하다** — 화면만 사라지고 쿼리 수용은 그대로 돈다 |
| Fleet 작업만 문제 | `fleet.jobs` 를 비운다 → 재시작 |
| 보드만 문제 | 보드는 항상 켜져 있다. DB 를 못 읽으면 화면이 "보드를 읽을 수 없다" 를 표시하고 **다른 화면은 영향받지 않는다** |
| 콘솔이 통째로 빈 화면 | §1-1 의 표 |
| 기존 화면 회귀 | 코드 롤백 (`upgrade-r2-r3.md` §11) |

**마이그레이션은 되돌리지 않는다.** `010`~`019` 는 새 테이블과 감사 액션을 더할 뿐 기존 테이블의
컬럼을 바꾸지 않는다. 코드를 이전 커밋으로 되돌려도 그 테이블은 그냥 안 쓰일 뿐이다.

⛔ **되돌리겠다고 앞 번호 마이그레이션을 다시 돌리지 않는다.** §1 의 이유로, 그건 롤백이 아니라
감사 액션 목록을 깎는 것이다.
