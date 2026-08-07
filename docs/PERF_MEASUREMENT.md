# PERF_MEASUREMENT — NFR-PERF-03 부하 실측

**측정일**: 2026-08-06
**대상**: TMS collector 폴링이 Trino 코디네이터에 주는 CPU 부하
**기준**: NFR-PERF-03 — 코디네이터 CPU **1% 미만**

---

## 0. 결론

| 항목 | 값 |
|---|---|
| **TMS 폴링 증분** | **코어 1개 기준 0.60%p** |
| NFR-PERF-03 (1%) | ✅ **충족** |
| 여유 | 약 1.7배 |

**⚠️ 단, 이 수치를 그대로 프로덕션에 적용하지 말 것.** §4의 한계를 반드시 읽어야 한다.
특히 **기존 히스토리 프로젝트의 EventListener 부하와 합산해야 하며**(D-001), 본 측정에는 그것이 포함되어 있지 않다.

---

## 1. 측정 환경

| 항목 | 값 |
|---|---|
| Trino | 477 단일 노드 (코디네이터 = 워커), HTTPS + PASSWORD 인증, `file` 접근제어 |
| 하드웨어 | Apple Silicon, 8 코어, 16GB |
| 힙 | `-Xmx3G` |
| 폴링 주기 | **운영 설정 그대로** — query 5초 / JMX 15초 / info 30초 |
| collector | 실제 `ClusterPoller` (모의 없음) |

---

## 2. 방법 — 외삽이 아니라 직접 측정

엔드포인트별 비용을 재서 곱하는 방식은 **회차 간 편차가 컸다** (`QueryManager` MBean이 3.27 → 1.52 CPU ms/req로 2배 차이). 그래서 **실제 collector를 운영 주기로 돌리고 코디네이터 프로세스의 누적 CPU 증분을 직접 측정**했다.

**유휴 구간을 반드시 빼야 한다.** Trino는 요청이 없어도 announcer·heartbeat·GC로 CPU를 쓴다. 이를 차감하지 않으면 폴링 비용이 크게 과대평가된다.

90초 창을 유휴/폴링 교대로 각 2회 측정하고 중앙값을 취했다.

```
유휴 (폴링 없음) #1     CPU 1.420s / 90.6s = 코어1개 1.567%   (폴링 0회)
collector 폴링   #1     CPU 2.090s / 90.3s = 코어1개 2.315%   (폴링 28회)
유휴 (폴링 없음) #2     CPU 1.480s / 90.7s = 코어1개 1.632%   (폴링 0회)
collector 폴링   #2     CPU 1.880s / 90.0s = 코어1개 2.088%   (폴링 27회)

유휴 중앙값     : 1.600%
폴링 중 중앙값  : 2.201%
증분            : 0.602%p
```

> **참고: 유휴 상태 Trino가 이미 코어 1개의 1.6%를 쓴다.** "코디네이터 CPU 1% 미만"이라는 NFR은 **증분** 기준으로만 의미가 있으며, 절대값 기준이라면 TMS와 무관하게 애초에 불가능하다. 본 문서는 증분으로 해석한다.

---

## 3. 엔드포인트별 비용 (참고용 — 편차 있음)

| 엔드포인트 | CPU ms/req | 응답 |
|---|---|---|
| `GET /v1/info` | 0.59 | 184 B |
| `GET /v1/query?state=…` (유휴) | 1.15 | 2 B |
| MBean `CoordinatorNodeManager` | 1.44 | 665 B |
| MBean `Memory` | 1.69 | 2.7 KB |
| MBean `QueryManager` | 1.5 ~ 3.3 | **71 KB** |
| MBean `ClusterMemoryManager` | 0.99 | 940 B |

`QueryManager`가 311개 속성 71KB로 유독 크다. TMS는 이 중 4개만 쓴다.

---

## 4. ⚠️ 반박된 최적화 2건

Bolt 1에서 "검토 대상"으로 남겨둔 아이디어 두 개를 측정으로 확인한 결과, **둘 다 오히려 손해다.**

### (1) `/metrics` 묶음 조회 — **채택하지 않는다**

MBean 4종을 `/metrics` 1회로 줄이면 요청 수는 줄지만 CPU는 늘어난다.

| 방식 | CPU ms | 응답 |
|---|---|---|
| MBean 4종 개별 | **5.6** | 76 KB |
| `/metrics` 전체 | 10.5 | **1,010 KB** |
| `/metrics?name[]=` 2개만 | 5.4 | 248 B |

필터링해도 서버는 **전체 MBean 레지스트리를 순회**해 지표를 만든다. 응답 크기만 줄고 CPU는 줄지 않는다.
→ **현행 MBean 개별 조회를 유지한다.** `TRINO_VERIFIED.md` §T1-7의 "Bolt 2 검토 대상" 항목은 이로써 종결.

### (2) 속성 단위 조회 — **채택하지 않는다**

`QueryManager`가 71KB나 되니 필요한 4개 속성만 `/v1/jmx/mbean/{obj}/{attr}` 로 받으면 쌀 것 같았다. 반대였다.

| 방식 | CPU ms |
|---|---|
| MBean 전체 1회 | **1.52** |
| 필요한 4개 속성 개별 | 3.11 (**+104%**) |

**요청당 고정비용(TLS·인증·접근제어 검사)이 페이로드 비용을 압도한다.** 요청 수를 줄이는 쪽이 항상 유리하다.
→ **MBean 전체 조회를 유지한다.**

---

## 5. 이 수치의 한계 (프로덕션 적용 전 필독)

| # | 한계 | 영향 방향 |
|---|---|---|
| L1 | **단일 노드다.** 워커 12대 클러스터가 아니다 | 불확실. `/v1/query`·노드 카운트는 코디네이터 인메모리 상태를 읽으므로 워커 수에 크게 좌우되지 않을 것으로 보이나 **미확인** |
| L2 | **유휴 클러스터다.** 실행 중 쿼리가 사실상 0 | **과소평가.** `/v1/query` 비용은 동시 실행 쿼리 수에 비례한다. 운영 실측 3,493 B/쿼리 기준, 동시 200건이면 응답이 700KB가 되어 직렬화 비용이 커진다 |
| L3 | **하드웨어가 다르다.** Apple Silicon vs 사내 VM | 불확실 |
| L4 | **기존 EventListener 부하가 빠져 있다** | **과소평가.** NFR-PERF-03은 합산 기준이어야 한다 (D-001) |
| L5 | 측정 창이 90초 × 2회로 짧다 | 편차 존재. 다만 여유가 1.7배라 결론은 견고 |
| L6 | OPA 접근제어가 아닌 `file` 접근제어 | **과소평가.** OPA 도입 시 `filterViewQueryOwnedBy` 가 distinct 사용자 수만큼 OPA 질의를 유발한다 (§T3-5) |

> **L2 + L4 + L6이 모두 같은 방향(과소평가)이다.** 0.60%p는 **하한선**으로 읽어야 한다.

---

## 6. 프로덕션에서 다시 측정할 것

Bolt 2 DoD 항목이다. 아래를 실측하기 전까지 NFR-PERF-03 충족은 **잠정**이다.

- [ ] 실제 클러스터에서 collector on/off 시 코디네이터 CPU 차이 (피크 시간대)
- [ ] **기존 히스토리 프로젝트 EventListener 부하와 합산**
- [ ] 피크 동시 실행 쿼리 수에서의 `/v1/query` 응답 크기와 CPU (`WORKLOAD_PROFILE.md` W2 수집과 병행)
- [ ] OPA 접근제어 도입 시 재측정

### 6-1. 측정 절차 (프로덕션)

> **`measure_coordinator_load.py` 를 프로덕션에 쓰지 않는다.** 그 스크립트는 별도 폴러를 하나 더 띄워 측정하므로, 이미 collector가 돌고 있는 프로덕션에서는 **부하를 이중으로 얹는다.** 프로덕션에서는 이미 돌고 있는 collector를 껐다 켜서 차이를 본다.

**전제**: 아래는 **코디네이터 호스트에서** 실행한다. collector를 잠시 멈춰도 **쿼리는 전혀 영향받지 않는다** (NFR-ISOLATION). TMS 화면이 그동안 stale로 표시될 뿐이다.

```bash
# 코디네이터 PID
PID=$(pgrep -f 'io.trino.server.TrinoServer' | head -1)

# CPU 초 스냅샷 (utime+stime, 초 단위)
cpu() { awk -v c=$(getconf CLK_TCK) '{print ($14+$15)/c}' /proc/$PID/stat; }

# 동시 실행 쿼리 수도 같이 기록한다 - 이 수가 곧 /v1/query 비용이다
running() { curl -sk -u "tms-svc:$TMS_TRINO_PASSWORD" \
  "https://127.0.0.1:8443/v1/jmx/mbean/trino.execution:name=QueryManager" \
  | python3 -c "import sys,json;print({a['name']:a.get('value') for a in json.load(sys.stdin)['attributes']}['RunningQueries'])"; }
```

**측정** — 피크 시간대에, 창 하나당 10분, 3회 반복한다.

```bash
# A) collector ON
echo "running=$(running)"; S=$(cpu); sleep 600; echo "ON  $(echo "$(cpu) - $S" | bc) cpu-sec"

# B) collector OFF  (TMS만 잠시 눈을 감는다. 쿼리는 무관)
sudo systemctl stop tms-collector
echo "running=$(running)"; S=$(cpu); sleep 600; echo "OFF $(echo "$(cpu) - $S" | bc) cpu-sec"
sudo systemctl start tms-collector
```

**판정**

```
추가 부하(%p) = (ON_cpu_sec - OFF_cpu_sec) / 600 / 코어수 × 100
```

| 결과 | 판정 |
|---|---|
| NFR-PERF-03 예산 이내 | 충족 확정. 이 문서 §1의 "잠정" 표기를 제거한다 |
| 예산 초과 | 아래 **초과 시 대응 순서**를 위에서부터 적용 |

> **⚠️ ON/OFF 두 창의 `running` 수가 비슷해야 비교가 성립한다.** 부하가 크게 달라진 창끼리 비교하면 collector가 아니라 사용자 트래픽 차이를 재게 된다. 차이가 크면 그 회차는 버리고 다시 측정하라.
>
> **⚠️ 기존 EventListener 부하와 합산해야 한다** (D-001). TMS collector만 예산 안에 들어와도, 히스토리 프로젝트의 EventListener와 합쳐 초과하면 NFR-PERF-03 위반이다. 두 시스템을 함께 껐다 켜는 창을 하나 더 두는 것이 가장 정확하다.

### 6-2. 초과 시 대응 순서 (설계에 이미 구현됨)
1. `collector.query_poll_interval_seconds` 상향 (5초 → 10초). 자동 백오프도 동작한다
2. `collector.jmx_poll_interval_seconds` 상향 (15초 → 30초)
3. 그래도 안 되면 헬스 테스트 축소 — 단 H-01/H-02는 `/v1/info`(PUBLIC)라 비용이 가장 싸다

---

## 7. 재현

`tests/integration/README-trino.md` 의 Trino 구성 후:

```bash
python3 scripts/measure_coordinator_load.py --coordinator https://127.0.0.1:8443 \
  --user tms-svc --insecure --window 90 --rounds 2
```
