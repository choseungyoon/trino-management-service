# RUNBOOK — 주말 작업분 배포 (R1 운영 중 → Workload · Gateway · 안전 재시작 · Fleet)

> **대상**: 이미 R1 이 배포되어 돌고 있는 사내 환경.
> **처음 설치**라면 이 문서가 아니라 `deploy.md` 를 본다.
> **소요**: 순수 배포 30분. 다만 **§5 와 §6 은 타 팀 협조가 필요**하므로 오늘 안 끝날 수 있다 — 안 끝나도 §1~§4 만으로 기능 대부분이 켜진다.

---

## 0. 이번에 들어가는 것

| 기능 | 화면 | 필요한 것 |
|---|---|---|
| **FR-WORKLOAD** 리소스 그룹 | Workload | 마이그레이션 003 + `jmxExport` (§4) |
| **FR-GATEWAY** 백엔드/라우팅 조회 | Gateway | Gateway API 계정 (§5) |
| **FR-CO-02/03/04** 안전 재시작 | Safe Restart | 마이그레이션 004~007 + **Gateway 필수** (§5) |
| **FR-FL-01/03** 노드 인벤토리·graceful shutdown | Fleet | 마이그레이션 008~009 + 인벤토리 (§4) + 워커 권한 (§6) |

**의존성 하나만 기억하면 된다: 안전 재시작은 Gateway 없이는 아예 켜지지 않는다.** 유입을 끊을 방법이 없는 재시작은 실행 중 쿼리를 전멸시키므로, 화면이 버튼 대신 이유를 표시한다.

---

## 1. ⭐ 먼저 — 지금 상태를 기록해 둔다

롤백 판단을 위해서다. 30초 걸린다.

```bash
cd /etc/trino-management-service && git rev-parse --short HEAD | tee /tmp/tms-rollback-to.txt
sudo systemctl is-active tms-api tms-collector
```

---

## 2. 코드 배치

```bash
cd /etc/trino-management-service
sudo -u tms git pull
sudo -u tms /etc/trino-management-service/venv/bin/pip install \
  --index-url https://<artifactory-host>/artifactory/api/pypi/<pypi-remote>/simple /etc/trino-management-service
```

> **아직 재시작하지 않는다.** 마이그레이션이 먼저다.

---

## 3. ⛔ 마이그레이션 — 이것을 빠뜨리면 조용히 깨진다

```bash
cd /etc/trino-management-service
for f in 003_snapshot_kinds \
         004_restart_sequence 005_restart_sequence_grants \
         006_cluster_restart_action 007_restart_event_output_level \
         008_snapshot_kind_fleet 009_node_shutdown_action; do
  echo "── $f"
  psql -h <db-host> -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/$f.sql
done
```

전부 재실행 안전하다. 이미 적용된 것은 건너뛴다.

> **⚠️ 왜 이걸 이렇게 강조하는가**
> 새 스냅샷 종류나 감사 액션이 DB 제약에 없으면 **collector 가 거부당하고, 로그만 남기고 계속 돈다**(저장 실패가 폴링을 멈추면 안 되므로). 증상은 오류가 아니라 **영원히 비어 있는 화면**이다.
> 개발 중 이 함정에 **두 번** 빠졌다. 그래서 코드-DB 대조 테스트를 붙였지만, 그건 코드 쪽 실수만 막는다. **적용은 사람이 해야 한다.**

### 3-1. append-only 권한 확인 (005 검증)

진행 로그는 감사 기록과 같은 등급이다. `tms_app` 으로 접속해 **둘 다 실패해야** 정상이다.

```sql
UPDATE restart_sequence_event SET message = 'tampered';   -- 실패해야 한다
DELETE FROM restart_sequence_event;                       -- 실패해야 한다
```

---

## 4. 설정

`config/config.yaml` 에 아래를 추가한다. **`fleet` 과 `cluster_ops` 는 오늘 안 켜도 된다** — 나중에 켜도 나머지는 잘 돈다.

### 4-1. Workload (선행: `jmxExport`)

```yaml
workload:
  enabled: true
  poll_interval_seconds: 15
```

> **⛔ `resource-groups.json` 의 모든 그룹에 `"jmxExport": true` 가 필요하다.** 없는 그룹은 TMS 에 **보이지 않는다** (`TRINO_VERIFIED.md` §T1-4). 화면이 "활동한 그룹만 나온다"고 명시하지만, jmxExport 누락과 지연 생성은 화면에서 구별되지 않는다.
>
> **부하 주의**: 폴링마다 MBean 열거 1회 + 그룹당 읽기 1회다. NFR-PERF-03 프로덕션 실측(`NEXT_STEPS.md` W-1) 전이면 `poll_interval_seconds` 를 30 이상으로 시작하라.

### 4-2. Fleet

```yaml
fleet:
  enabled: true
  poll_interval_seconds: 60
  inventories:
    <클러스터1>: /etc/tms/ansible/cluster1.ini
    <클러스터2>: /etc/tms/ansible/cluster2.ini
  node_url_template: "https://{address}:8443"   # ← 실제 워커 포트/스킴
```

- TMS 는 인벤토리의 `[coordinator]` / `[workers]` 섹션만 읽는다. **파일을 실행하지 않는다.**
- **⚠️ TMS 호스트에서 워커 HTTP 포트에 도달할 수 있어야 한다.** 지금까지 TMS 는 코디네이터만 봤으므로 방화벽이 막고 있을 수 있다. 막혀 있으면 전 워커가 "No answer" 로 뜬다.
- `node_url_template` 의 포트·스킴이 틀리면 **똑같이 전 노드가 "No answer"** 다 — 장애처럼 보이지만 오타다.

```bash
# 배포 전에 손으로 확인하는 게 제일 빠르다 (인증 불필요, PUBLIC)
curl -sk https://<워커주소>:8443/v1/info
```

### 4-3. 재시작 실행 방식 — **오늘은 `manual` 을 권한다**

```yaml
cluster_ops:
  restart_mode: manual        # 기본값
```

`manual` 이면 TMS 가 게이트(빈 클러스터 확인 → 헬스 확인)를 지키고 재시작은 사람이 한다. **추가 권한이 전혀 필요 없고, 사고를 막는 부분은 자동/수동이 완전히 동일하다.** 먼저 이 모드로 한 번 돌려본 뒤 자동화를 결정하라 — 자동 실행은 **TMS 호스트가 전 Trino 노드에 SSH 접근을 갖는다**는 뜻이고, 이는 보안 결정이다 (D-009).

---

## 5. Gateway 연결 `[타 팀 협조]`

**안전 재시작의 전제 조건이다.**

```yaml
gateway:
  enabled: true
  base_url: "https://<gateway-host>:<port>"
  user: "<API 역할 계정>"
  poll_interval_seconds: 30
```

비밀번호는 `/etc/tms/tms.env` 의 `TMS_GATEWAY_PASSWORD` 로 넣는다 (`config.yaml` 에 쓰지 않는다).

- [ ] `API` 역할 계정 발급 — 상세 요청 내용은 `runbooks/gateway-config-request.md`
- [ ] **⛔ "읽기 전용" 역할은 없다.** 목록 조회에 필요한 `API` 역할은 **백엔드 변경 권한을 포함한다.** `tms-svc` 와 동급으로 보호할 것 (`/etc/tms/tms.env`, 600)
- [ ] Gateway 에 **TLS 활성** 확인 — 없으면 인증 자체가 동작하지 않는다

---

## 6. Graceful shutdown 권한 `[타 팀 협조 · 오늘 안 되어도 됨]`

Fleet 화면은 이것 없이도 **보인다.** 안 되는 건 shutdown 버튼뿐이다.

`PUT /v1/info/state` 는 `MANAGEMENT_WRITE` 이고, 로컬 실측에서 TMS 계정은 **403 "Management only resource"** 를 받았다.

- [ ] **모든 워커에** `etc/access-control.properties`(OPA 설정) 배포 — 문서 원문: *"These configuration must be present on all workers."*
- [ ] Rego 정책에 TMS 계정의 **`WriteSystemInformation`** 허용 규칙 추가
- [ ] **⚠️ 미해소 G-4**: 공식 문서는 graceful shutdown 에 대해 `allow-all`/`file` 만 언급하고 **OPA 를 언급하지 않는다.** OPA 로 인가된다는 결론은 소스 근거다 — **워커 한 대로 먼저 실증할 것**
- [ ] **⚠️ 신규 실패 모드**: 워커의 OPA 가 죽으면 shutdown 이 거부된다. 워커 OPA 헬스를 감시 대상에 넣을 것

---

## 7. ⭐ 재시작 전 검증

```bash
read -rs TMS_TRINO_PASSWORD && export TMS_TRINO_PASSWORD
/etc/trino-management-service/venv/bin/tms-config-check --config /etc/trino-management-service/config/config.yaml -v
unset TMS_TRINO_PASSWORD
```

이번 릴리스에서 검사 항목이 늘었다. **특히 이 두 줄을 확인하라.**

```
[  ok  ] 마이그레이션      필요한 스키마가 모두 적용되어 있다
[  ok  ] fleet 인벤토리    <클러스터>: 노드 13개 (워커 12)
```

- `[ FAIL ] 마이그레이션 누락` → §3 을 다시 하라. **이 실패는 런타임에는 조용하다.**
- `fleet 인벤토리` 의 워커 수가 `expected_workers` 와 다르면 경고가 뜬다 — 둘 중 하나가 틀린 것이다.

종료 코드 0=정상, 1=문제, 2=설정 로드 실패.

---

## 8. 기동

```bash
sudo systemctl restart tms-collector
sleep 20
sudo systemctl restart tms-api
systemctl is-active tms-collector tms-api
```

collector 를 먼저 올린다 — API 는 collector 가 쓴 스냅샷을 읽을 뿐이다.

---

## 9. 동작 확인 (브라우저)

순서대로 확인한다. **위에서 막히면 아래는 볼 필요 없다.**

| # | 화면 | 기대 | 아니면 |
|---|---|---|---|
| 1 | Overview | 기존과 동일 | 회귀. §11 롤백 |
| 2 | **Workload** | 리소스 그룹 트리. 컬럼 클릭 시 랭킹 | 비었으면 → 마이그레이션 003, `jmxExport`, 또는 아직 아무 그룹도 쿼리를 안 받음 |
| 3 | **Gateway** | 백엔드 목록 + TMS 클러스터 대응 | 없으면 → §5 |
| 4 | **Fleet** | 노드 목록, 코디네이터 `Active` + 버전 | 전부 "No answer" → §4-2 (포트·스킴·방화벽) |
| 5 | **Safe Restart** | 6단계 미리보기 + 사유 입력 | "Restarts are not available" → §5 (Gateway) |

### 9-1. 첫 재시작은 이렇게

**⛔ 첫 실전 재시작은 한가한 시간에, 예비 클러스터로.** 시퀀스가 막아주는 것은 실수지 계획 부족이 아니다.

1. Safe Restart → 사유 입력 → 시작 → **즉시 Gateway 화면에서 해당 백엔드가 비활성인지 확인**
2. 드레인이 도는 것을 진행 로그에서 확인
3. **끝까지 갈 생각이 없으면 "Stop and put the cluster back"** — 중단은 트래픽을 반드시 되돌린다
4. 되돌아온 것을 Gateway 화면에서 다시 확인

---

## 10. 확인해서 알려주면 다음 작업이 열리는 것

배포와 무관하지만, 확인해 주면 막힌 항목이 풀린다. 상세는 `docs/NEXT_STEPS.md`.

- `system.runtime.nodes` 조회 권한(`ExecuteQuery`)을 TMS 에 줄 것인가 → **권고: 주지 않는다**
- Gateway 라우팅을 least-loaded 로 바꿀 것인가 (`NEXT_STEPS.md` W-7)
- NFR-PERF-03 프로덕션 실측 (`NEXT_STEPS.md` W-1) — Workload 폴링 주기를 낮출 근거

---

## 11. 롤백

```bash
sudo systemctl stop tms-api tms-collector
cd /etc/trino-management-service
sudo -u tms git checkout $(cat /tmp/tms-rollback-to.txt)
sudo -u tms /etc/trino-management-service/venv/bin/pip install --index-url <...> /etc/trino-management-service
sudo systemctl start tms-collector tms-api
```

**마이그레이션은 되돌리지 않는다.** 전부 추가(테이블·제약 확장)이고, 이전 코드는 새 테이블을 모르는 채로 정상 동작한다. `audit_action` 은 어떤 경우에도 DROP 하지 않는다 (FR-AA-04).

> **TMS 가 완전히 죽어도 모든 쿼리는 정상 실행된다** (NFR-ISOLATION). 급하면 망설이지 말고 내려라. **단 하나의 예외**: 안전 재시작이 진행 중이면 그 클러스터는 Gateway 에서 비활성 상태다. TMS 를 내리기 전에 시퀀스를 끝내거나 중단하라. 이미 내렸다면 Gateway UI 에서 직접 백엔드를 활성화하면 된다.
