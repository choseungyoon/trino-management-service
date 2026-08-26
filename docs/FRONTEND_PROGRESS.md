# FRONTEND_PROGRESS — 전환 진행 상황

> **자동 갱신 아님.** 작업하면서 손으로 적는다. 계획은 `FRONTEND_PLAN.md`, 결정은 `DECISIONS.md` D-016.
>
> **다음 세션은 여기부터 읽는다.** 무엇이 끝났고 무엇이 남았고, 옮기면서 발견한 것이 무엇인지.

## 단계

| | | |
|---|---|---|
| 1 | JSON API 47개 | ✅ 완료 |
| 2 | Vite + React 스캐폴드, FastAPI 정적 서빙 | ✅ 완료 |
| 3 | 화면 12개 이전 | 🔄 **2 / 12** |

## 화면

| 화면 | 상태 | 비고 |
|---|---|---|
| Overview | ✅ | `/app/` |
| Live Queries | ✅ | 칩 필터 · kill 다이얼로그 |
| Health | ⬜ | H-03 문장 만들기가 서버로 가야 한다 (아래) |
| Workload | ⬜ | |
| Resource Groups | ⬜ | 가장 인터랙티브. 트리 인라인 편집 |
| Fleet | ⬜ | 로그 스트리밍은 SSE 권장 |
| Safe Restart | ⬜ | 진행 콘솔 라이브 |
| Gateway | ⬜ | 읽기 전용, 쉬움 |
| Benchmark | ⬜ | 차트 포함. uPlot / ECharts 선택 필요 |
| Query Sets | ⬜ | Benchmark 와 한 덩어리 |
| Work Board | ⬜ | 칸반 |
| Audit | ⬜ | |

## 옮기면서 정한 것

**`views.py` 로직은 옮기기 전에 "API 가 해야 하나"를 먼저 묻는다.** 지금까지 둘을 그렇게 없앴다:

| 원래 | 어디로 | 왜 |
|---|---|---|
| `views.work_timeline` | `work/items.py` | 댓글과 상태 변경을 엮는 건 화면이 아니라 무엇을 보여줄지의 규칙이다 |
| `views.cluster_summary` | `GET /api/v1/overview` | `active_workers` 를 워커로 세는 건 표현이 아니라 정확성이다 |

**남은 후보** — 다음에 해당 화면을 옮길 때 같은 질문을 한다:

- ⛔ **`test_observed_text`** (Health). H-03 의 `{active_workers, expected_workers, planned_out, unplanned_missing}` 를 문장으로 만든다. **planned/unplanned 구분이 그 테스트의 존재 이유**다. 서버로 올리는 쪽을 강하게 권한다
- `bottleneck_text`, `order_groups`, `flatten_groups` (Workload) — 병목 판정은 숫자에 대한 판단이지 서식이 아니다
- `benchmark_query_rows`, `query_history_chart`, `comparison_rows` (Benchmark) — 차트 라이브러리를 쓰면 집계만 남는다

## 옮기면서 찾은 버그

| | |
|---|---|
| **401 이 500 으로** | 세션 없이 `/api/v1/` 를 치면 500 이 났다. `Unauthenticated` 핸들러가 `/api/` 에서 예외를 다시 던졌고, Starlette 은 핸들러 안의 예외에 핸들러 조회를 다시 하지 않는다. **SPA 가 만료된 세션으로 마주칠 첫 응답이 이거였다** |
| Status 매핑 누락 | 쿼리 상태가 전부 UNKNOWN 으로 그려졌다. 서버의 `status_class` 는 RUNNING/QUEUED/SUCCEEDED/FAILED 까지 한 어휘로 접는다 |
| 데모 데이터 모순 | H-03 이 11워커 클러스터에 12를 보고 → 화면에 `12/11`. 그 규칙이 막으려는 것과 똑같이 보인다 |

## 화면을 옮길 때의 규칙

1. **`tms.css` 가 이긴다.** 클래스 이름을 지어내지 않는다 — 서버 렌더 템플릿에서 실제 이름을 확인하고 쓴다. 세 번 지어냈다가 세 번 고쳤다
2. 네이티브 요소를 먼저 본다. `<dialog>` 는 포커스 트랩·Esc·배경 비활성을 공짜로 준다
3. ⛔ 상태는 **색만으로 표현하지 않는다.** 단어를 항상 함께
4. ⛔ staleness 는 **서버가 정한다.** 봉투의 `stale` 을 보여줄 뿐
5. ⛔ 쓰기는 사유·감사·권한을 **서버가 강제한다.** 클라이언트 검증은 편의

## 남은 것

- `src/tms/web/` 삭제는 **12개가 다 끝난 뒤**. 그때 `/app` → `/` 로 옮긴다
- `tms.css` 는 아직 `web/static/` 에 있고 프론트가 상대경로로 읽는다. web/ 을 지울 때 같이 옮긴다
- 차트 라이브러리 미결 (uPlot vs ECharts)
- 빌드 산출물 커밋이 전제다. **프론트를 고치면 `npm --prefix frontend run build` 하고 같이 커밋한다**
