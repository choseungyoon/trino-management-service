# FRONTEND_PROGRESS — 전환 진행 상황

> **자동 갱신 아님.** 작업하면서 손으로 적는다. 계획은 `FRONTEND_PLAN.md`, 결정은 `DECISIONS.md` D-016.
>
> **다음 세션은 여기부터 읽는다.** 무엇이 끝났고 무엇이 남았고, 옮기면서 발견한 것이 무엇인지.

## 단계

| | | |
|---|---|---|
| 1 | JSON API 47개 | ✅ 완료 |
| 2 | Vite + React 스캐폴드, FastAPI 정적 서빙 | ✅ 완료 |
| 3 | 화면 12개 이전 | 🔄 **9 / 12** — 남은 것은 Resource Groups · Fleet · Safe Restart |

## 화면

| 화면 | 상태 | 비고 |
|---|---|---|
| Overview | ✅ | `/app/` |
| Live Queries | ✅ | 칩 필터 · kill 다이얼로그 |
| Health | ✅ | `test_observed_text` → `health/observed.py` 로 이동 완료 |
| Workload | ✅ | 정렬은 클라이언트. `bottleneck_text` → 서버 |
| Resource Groups | ⬜ | 가장 인터랙티브. 트리 인라인 편집 |
| Fleet | ⬜ | 로그 스트리밍은 SSE 권장 |
| Safe Restart | ⬜ | 진행 콘솔 라이브 |
| Gateway | ✅ | |
| Benchmark | ✅ | 클러스터 다중 선택 · 실행 상세 · 비교 · 추이 차트 |
| Query Sets | ✅ | 세트 목록 · 세트 편집 · 쿼리 이력 |
| Work Board | ✅ | 보드 + 항목 상세 |
| Audit | ✅ | |

## 옮기면서 정한 것

**`views.py` 로직은 옮기기 전에 "API 가 해야 하나"를 먼저 묻는다.** 지금까지 둘을 그렇게 없앴다:

| 원래 | 어디로 | 왜 |
|---|---|---|
| `views.work_timeline` | `work/items.py` | 댓글과 상태 변경을 엮는 건 화면이 아니라 무엇을 보여줄지의 규칙이다 |
| `views.cluster_summary` | `GET /api/v1/overview` | `active_workers` 를 워커로 세는 건 표현이 아니라 정확성이다 |
| `views.bottleneck_text` | `collector/resourcegroups.py` | 진단 코드를 만드는 곳 옆에 문장을 둔다. 사유를 하나 추가하는 데 프론트 릴리스가 필요하면 안 된다 |
| `views.test_observed_text` | `health/observed.py` | 어떤 테스트가 있고 그 숫자가 뭘 뜻하는지는 서버 지식이다. 세그먼트 `[{text, strong}]` 로 돌려주고 클라이언트는 강조만 복원한다 |
| `views.query_history_chart` | `bench/trend.py` | 픽셀은 없다. **어떤 실행이 한 점으로 묶이는지와 한 실행의 대푯값이 무엇인지**가 서버 지식이고, 그릴 수 있는지(`drawable`)도 마찬가지다 |
| `views.benchmark_query_rows` | `bench/compare.py` 의 `query_rows` | 반복 실행을 중앙값으로 접는 건 숫자에 대한 결정이다. `GET /api/v1/benchmarks/{id}` 가 `by_query` 로 같이 준다 |

**남은 후보** — 다음에 해당 화면을 옮길 때 같은 질문을 한다:

- `order_groups` / `flatten_groups` (Workload) — **클라이언트에 남겼다.** 트리를 표로 펴는 것과 정렬은 화면이 하는 일이고, 정렬은 브라우저가 이미 들고 있는 숫자에 대한 질문이다
- ~~`benchmark_query_rows`, `query_history_chart`~~ — **옮겼다.** 아래 표 참조. `comparison_rows` 는 verdict → CSS 클래스 매핑뿐이라 클라이언트에 남겼다

## 옮기면서 찾은 버그

| | |
|---|---|
| **401 이 500 으로** | 세션 없이 `/api/v1/` 를 치면 500 이 났다. `Unauthenticated` 핸들러가 `/api/` 에서 예외를 다시 던졌고, Starlette 은 핸들러 안의 예외에 핸들러 조회를 다시 하지 않는다. **SPA 가 만료된 세션으로 마주칠 첫 응답이 이거였다** |
| Status 매핑 누락 | 쿼리 상태가 전부 UNKNOWN 으로 그려졌다. 서버의 `status_class` 는 RUNNING/QUEUED/SUCCEEDED/FAILED 까지 한 어휘로 접는다 |
| 데모 데이터 모순 | H-03 이 11워커 클러스터에 12를 보고 → 화면에 `12/11`. 그 규칙이 막으려는 것과 똑같이 보인다 |

## 내비게이션

**만든 화면만 나열한다.** 나머지는 아직 서버 렌더 `/` 에 있고, 눌렀는데 Overview 로 돌아오는 링크는 없는 것만 못하다. 화면을 옮길 때마다 한 줄씩 추가한다.

## 옮기면서 고친 것

| | |
|---|---|
| **Workload 빈 화면** | 활성화됐는데 행이 없으면 아무것도 안 그려졌다. "고장" 처럼 읽힌다 — 빈 상태를 넣었다 (그룹은 지연 생성되므로 정상 상태다) |
| **정렬 헤더가 회색 버튼** | `.sortable` 은 `<a>` 용으로 쓰였는데 클라이언트가 되면서 `<button>` 이 됐다. tms.css 에 버튼 리셋을 넣었다 |
| **요약 숫자가 빈칸** | 라벨 아래 빈칸은 0 으로 읽힌다. 없는 값은 em dash |

## 화면을 옮길 때의 규칙

1. **`tms.css` 가 이긴다.** 클래스 이름을 지어내지 않는다 — 서버 렌더 템플릿에서 실제 이름을 확인하고 쓴다. 세 번 지어냈다가 세 번 고쳤다
2. 네이티브 요소를 먼저 본다. `<dialog>` 는 포커스 트랩·Esc·배경 비활성을 공짜로 준다
3. ⛔ 상태는 **색만으로 표현하지 않는다.** 단어를 항상 함께
4. ⛔ staleness 는 **서버가 정한다.** 봉투의 `stale` 을 보여줄 뿐
5. ⛔ 쓰기는 사유·감사·권한을 **서버가 강제한다.** 클라이언트 검증은 편의

## 남은 것

- `src/tms/web/` 삭제는 **12개가 다 끝난 뒤**. 그때 `/app` → `/` 로 옮긴다
- `tms.css` 는 아직 `web/static/` 에 있고 프론트가 상대경로로 읽는다. web/ 을 지울 때 같이 옮긴다
- ~~차트 라이브러리 미결~~ — **안 쓴다.** 인라인 SVG (`components/LineChart.tsx`). 점 몇 개와 직선이고 숫자는 서버가 이미 집계해서 준다. 줌·브러시가 필요해지면 그때 라이브러리가 그 아래만 대체한다
- 빌드 산출물 커밋이 전제다. **프론트를 고치면 `npm --prefix frontend run build` 하고 같이 커밋한다**
