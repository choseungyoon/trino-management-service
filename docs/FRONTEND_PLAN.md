# FRONTEND_PLAN — 화면 × 필요한 API

> **무엇인가**: D-016(React 19 SPA 전환)의 1단계 산출물. 화면 12개가 각각 무엇을 그리고, 어떤 API 가 있어야 하고, `views.py` 의 어떤 로직이 넘어가는지의 목록이다.
>
> **왜 이게 먼저인가**: 작업량의 대부분이 화면이 아니라 **API** 다. 그리고 API 는 프론트보다 오래 산다 — 어떤 프레임워크를 쓰든, 심지어 안 바꾸더라도 필요하다.
>
> ✅ **2026-08-26: API 는 전부 만들었다.** 기존 14 + 새 47 = **61개**, `src/tms/api/routes/` 에 기능별 모듈로. 화면별 표의 "API" 열은 이제 무엇을 부르면 되는지의 목록이다. 다음은 §6 의 2단계(Vite + React 스캐폴드).
>
> **근거**: `DECISIONS.md` D-016 · `docs/API_R1.md`(기존 12개의 규약)

---

## 0. 숫자

| | |
|---|---|
| web 라우트 | **69** (GET 36 / POST 33) |
| 기존 JSON API | **14** — R1 범위(쿼리·헬스·감사·인증)뿐 |
| **만든 API** | ✅ **47** — `src/tms/api/routes/` |
| **합계** | **61** |
| `views.py` 공개 함수 | **23** — 이 중 몇이 JS 로 가는지가 숨은 비용 |
| `formatting.py` 필터 | **11** — 전량 JS 로 |

**web 라우트 69 대 API 40 — 왜 줄어드나:**

| 소멸하는 것 | 개수 | 어디로 |
|---|---|---|
| htmx 조각 (`/row`, `/edit`, `/delete` 확인, `/benchmark/clusters`) | 8 | **컴포넌트 상태** |
| 확인 폼 GET (`/kill`, `/audit/export`, `/health/tests/{id}`) | 3 | 모달 |
| 프레임 라우트 (리다이렉트 2 · 테마 · 로그인 폼) | 4 | 클라이언트 라우팅 |
| 기존 API 로 이미 덮이는 화면 (Overview · Queries) | 나머지 | ✅ 그대로 |

---

## 1. 화면별

각 행의 **API** 열이 새로 만들 것이다. `✅` 는 이미 있는 것.

### Overview `/`

| | |
|---|---|
| 지금 | 클러스터 카드 — 워커 수, 실행/대기 쿼리, 실패율, 헬스 롤업, staleness |
| 원하는 것 | 자동 갱신 (지금은 전체 리로드) |
| API | ✅ `GET /clusters` · ✅ `GET /clusters/{c}/health` · ✅ `GET /clusters/{c}/queries` |
| 넘어가는 로직 | **`cluster_summary`** — ⛔ `active_workers` 를 노드가 아니라 워커로 센다. 백엔드는 코디네이터를 포함해 세므로 12워커 클러스터가 13을 보고한다. 그대로 그리면 버그처럼 보인다. **`state_counts`** |

### Live Queries `/queries`, `/clusters/{c}/queries`

| | |
|---|---|
| 지금 | 필터 칩(전체/실행/대기/장기), 정렬 표, 상세, kill |
| 원하는 것 | 폴링 중 스크롤·선택 유지, 낙관적 kill |
| API | ✅ 3개 모두 있음 |
| 넘어가는 로직 | **`query_chips`** (칩이 곧 요약 — 개수가 KPI 다) · `expand_state_filter` |
| 소멸 | `GET .../kill` (확인 폼 → 모달) |

⛔ **권한 저하를 빈 목록과 구분해야 한다.** `file` 접근제어에서 권한 거부가 **빈 목록으로** 도착한다. H-09 자가진단 결과가 API 응답에 실려야 하고, 프론트가 "쿼리 없음" 과 다르게 그려야 한다.

### Health `/clusters/{c}/health`

| | |
|---|---|
| 지금 | 테스트 9개, 롤업, 임계값 편집, 이벤트 이력 |
| 원하는 것 | 테스트별 인라인 편집 |
| API | ✅ `GET /clusters/{c}/health` · **`GET /clusters/{c}/health/events`** · **`PUT /clusters/{c}/health/tests/{id}`** · **`PUT /clusters/{c}/health/rollup`** |
| 넘어가는 로직 | ⛔ **`test_observed_text`** — 테스트마다 관측값의 모양이 달라 문장을 각각 만든다. H-03 의 `{active_workers, expected_workers, planned_out, unplanned_missing}` 를 "10 of 12 active · 1 draining (planned) · 1 missing unplanned" 로. **planned/unplanned 구분이 그 테스트의 존재 이유**다. 일반 `str(value)` 로 바꾸면 장애 중인 운영자에게 raw dict 이 뜬다. **`health_view`** |

### Workload `/clusters/{c}/workload`

| | |
|---|---|
| 지금 | 리소스 그룹별 실행/대기, 병목 진단, 정렬 |
| 원하는 것 | 클라이언트 정렬 (지금은 서버 왕복) |
| API | **`GET /clusters/{c}/workload`** |
| 넘어가는 로직 | `bottleneck_text` · `flatten_groups`(깊이 우선 — 표가 트리로 읽히도록) · `order_groups` · `column_label` |

### Resource Groups `/clusters/{c}/resource-groups`

| | |
|---|---|
| 지금 | 설정 트리 + 실행 상태 대조, 행 인라인 편집, 셀렉터 추가/삭제, 변경 이력, 되돌리기 |
| 원하는 것 | 지금도 htmx 로 하고 있다 — **가장 앱에 가까운 화면** |
| API | **`GET /clusters/{c}/resource-groups`** · **`POST/PUT/DELETE .../groups[/{row_id}]`** · **`POST/DELETE .../selectors[/{id}]`** · **`GET .../history`** · **`POST .../history/{revision_id}/revert`** (7개) |
| 넘어가는 로직 | 없음 — 검증 규칙(V1~V11 · W1~W5)은 **서버에 남는다** |
| 소멸 | htmx 조각 4개 (`/row`, `/edit`, `/delete`, `/selectors/{id}/delete`) |

⛔ **검증을 클라이언트로 옮기지 않는다.** 이 화면의 쓰기는 프로덕션 쿼리 수용 제어를 10초 안에 바꾸고 재시작이라는 관문이 없다. 클라이언트 검증은 편의일 뿐이고 서버가 다시 본다.

### Fleet `/clusters/{c}/fleet`

| | |
|---|---|
| 지금 | 노드 인벤토리, 미조인 워커 식별, graceful shutdown, 작업 실행 이력 |
| 원하는 것 | **작업 로그 스트리밍** (지금은 폴링) |
| API | **`GET /clusters/{c}/fleet`** · **`POST .../identify`** · **`POST .../nodes/{host}/shutdown`** · **`GET /fleet/jobs`** · **`GET /fleet/jobs/{run_id}`** (5개) |
| 넘어가는 로직 | 없음 |

로그 스트리밍은 **SSE** 를 권한다 — 단방향이고, `EventSource` 가 세션 쿠키를 그대로 싣고, WebSocket 처럼 별도 인증 경로를 만들지 않는다.

### Safe Restart `/clusters/{c}/restart`, `/restarts/{id}`

| | |
|---|---|
| 지금 | 6단계 시퀀스, 진행 콘솔, 단계별 액션 |
| 원하는 것 | **진행 콘솔 라이브** (지금은 tms.js 가 패널 교체) |
| API | **`GET /clusters/{c}/restart`** · **`POST /restarts`** · **`GET /restarts/{id}`** · **`POST /restarts/{id}/{step}`** (force-drain·restart·restarted·complete·abort) · **`GET /restarts/{id}/events`**(SSE) (5개) |
| 넘어가는 로직 | 없음 — 단계 전이는 **서버가 소유한다** |

⛔ **단계 순서를 클라이언트가 정하지 않는다.** 안전 시퀀스를 건너뛰는 경로가 생기면 안 된다. 프론트는 서버가 허용한 액션만 보여준다.

### Gateway `/gateway`

| | |
|---|---|
| 지금 | 백엔드 목록, 라우팅 그룹, 활성 상태 |
| API | **`GET /gateway`** |
| 넘어가는 로직 | 없음 |

### Benchmark `/benchmark`, `/benchmarks/*`

| | |
|---|---|
| 지금 | 클러스터 다중 선택 + 실행, 쿼리 세트 CRUD, 실행 결과, 비교, 쿼리별 이력 + 추세 차트 |
| 원하는 것 | **차트 줌·브러시·구간 선택** — D-011 재검토가 지목한 바로 그 지점 |
| API | **`GET /benchmark`** · **`POST /benchmark`** · **`GET/POST /benchmark/sets`** · **`GET/PUT/DELETE /benchmark/sets/{key}`** · **`POST/PUT/DELETE .../queries[/{name}]`** · **`GET .../queries/{name}/history`** · **`GET /benchmarks/{id}`** · **`POST /benchmarks/{id}/abort`** · **`GET /benchmarks/{a}/compare/{b}`** (11개) |
| 넘어가는 로직 | **`chart.py` 전량**(축 계산·"점 하나면 안 그린다") · `benchmark_query_rows`(중앙값 접기) · `query_history_chart` · `comparison_rows` |
| 소멸 | `GET /benchmark/clusters`(htmx 폴링 조각) · `GET /clusters/{c}/benchmark`(리다이렉트) |

차트 라이브러리는 **uPlot** 또는 **ECharts** — 둘 다 프레임워크 무관이므로 별도 결정이다.

### Work Board `/work`

| | |
|---|---|
| 지금 | 칸반 컬럼, 요청 등록, 댓글, 상태 이동 |
| 원하는 것 | **드래그로 상태 이동** |
| API | **`GET /work`** · **`POST /work`** · **`GET /work/{key}`** · **`POST /work/{key}/comment`** · **`PUT /work/{key}/status`** · **`GET /work.md`**(그대로 유지 — 사외에서 읽는 파일) (6개) |
| 넘어가는 로직 | `status_label` · `status_choices` · `kind_chips` · `work_item_row` · `work_timeline` |

### Audit `/audit`

| | |
|---|---|
| 지금 | 검색, 필터 칩, CSV 내보내기 |
| API | ✅ `GET /audit` · ✅ `GET /audit/export` · **`POST /audit/export`**(사유 필요) |
| 넘어가는 로직 | `audit_chips` |

### Account / Auth

| | |
|---|---|
| API | ✅ `POST /login` · ✅ `POST /logout` · ✅ `GET /me` · ✅ `PUT /password` · ✅ `GET /links` |
| 소멸 | `POST /ui/theme`(쿠키 → 클라이언트 상태) |

---

## 2. 합계

| 기능 | 새 API |
|---|---|
| Benchmark | 11 |
| Resource Groups | 7 |
| Work Board | 6 |
| Fleet | 5 |
| Restart | 5 |
| Health | 3 |
| Workload | 1 |
| Gateway | 1 |
| Audit | 1 |
| **합계** | **40** |

기존 12 + 새 40 = **약 52개.** 화면당 3~4개다.

---

## 3. API 규약 — 기존 12개를 따른다

`docs/API_R1.md` 가 이미 정한 것을 바꾸지 않는다.

| | |
|---|---|
| 봉투 | `{collected_at, stale, data}` — **staleness 를 정직하게** 싣는다. 30초 넘으면 stale |
| 오류 | `{error: {code, message}}`. 메시지는 **운영자가 읽는 문장** |
| 쓰기 | `reason` 없으면 **400**. 감사 저장소 다운이면 **503** — 이건 의도된 동작이고 화면이 그렇게 말해야 한다 |

### ⛔ 바뀌면 안 되는 것

| | |
|---|---|
| **인증** | 서버가 설정하는 `HttpOnly` · `Secure` · `SameSite=strict` **세션 쿠키를 유지한다.** `localStorage` 토큰으로 바꾸지 않는다 — 같은 출처라 `fetch` 에 그냥 실린다 |
| **쓰기 3종** | `reason` 필수 · 감사 기록 · admin 한정. **서비스 계층이 강제한다** |
| **staleness** | 없는 데이터를 정상으로 그리지 않는다. UNKNOWN 이 GOOD 을 이긴다 |
| **NFR-ISOLATION** | TMS 가 죽어도 쿼리는 돈다. UI 가 무엇이 되든 쿼리 경로에 들어가지 않는다 |

---

## 4. 서버에 남는 것

전환 후에도 파이썬이 소유한다. **테스트 878개 중 대부분이 여기에 붙어 있다.**

- 서비스 계층 전량 (`api/services.py` 24 · `ops/service.py` 10 · `fleet/service.py` 10 · `bench/service.py` 16 · `work/service.py` 7)
- 검증 규칙 — 리소스 그룹 V1~V11 · 벤치마크 허용 구문 · 안전 시퀀스 단계 전이
- 컬렉터·헬스 테스트·감사
- `tms-work-export` (사외에서 보드를 읽는 유일한 경로)

## 5. 사라지는 것

`src/tms/web/` — Jinja 템플릿 41개, `routes.py` 1,555줄, `views.py`, `formatting.py`, `chart.py`, `tms.js` 295줄, `htmx.min.js`.

**`views.py` 23개 함수 중 실제로 JS 로 가는 것은 위 표에 표시한 것들뿐이다.** 나머지는 API 응답이 이미 그 모양이면 필요 없어진다 — **API 를 설계할 때 이 판단을 같이 한다.**

---

## 6. 순서

1. **API 를 먼저, 파이썬 테스트와 함께.** 화면 없이도 `curl` 로 검증된다
2. 그 다음 Vite + React 스캐폴드, FastAPI 정적 서빙
3. 화면을 옮긴다. **병행 운영하지 않는다** — 두 벌 유지가 이 전환이 피하려는 것이다

### 화면을 그릴 때 (3단계)

**`frontend-design` 스킬을 쓴다** (사용자 지시, 2026-08-26). 다만 이 프로젝트에는 이미 디자인 시스템이 있다 — 우선순위는 이렇다:

1. **`web/static/tms.css` 가 이긴다.** 토큰·타이포·상태 색·차트 시리즈 램프가 전부 거기 있고, 승인된 디자인은 `docs/archive/mockups-r1.html` 이다
2. `PRODUCT.md` 의 Brand Commitments — 다크 기본 + 라이트 토글, Trino 마젠타 액센트, Datadog/SEP 수준의 완성도, Cloudera Manager 처럼 낡아 보이지 않기
3. 그 위에서 스킬의 판단을 쓴다 — 레이아웃, 상호작용, 빈 상태, 오류 문구

⛔ **화면 문구는 영어다.** 상태를 색만으로 표현하지 않는다(아이콘 + 텍스트). 두 테마 모두 WCAG AA.

⛔ **사내 마이그레이션 `018`/`019` 와 벤치마크 실환경 검증이 1번보다 먼저다.** 벤치마크 API 설계가 그 실측에 기댄다.
