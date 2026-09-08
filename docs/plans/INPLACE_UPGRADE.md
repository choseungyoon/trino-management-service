# PLAN — VM in-place Trino upgrade

> **상태**: 제품 방향 승인(D-020), 구현 전 검증 대기
>
> **대상**: Trino 477 기반 TMS, VM + systemd + Ansible 환경
>
> **소유**: Codex 계획·독립 리뷰 / Claude 구현·리뷰 수정 / Product Owner 결정·현장 검증

## Claude 시작 지침

이 문서는 전체 기능을 한 번에 구현하라는 요청서가 아니다.

1. `AGENTS.md`, `README.md`, `DECISIONS.md` D-020, `TODO.md`, `TEAMS.md`, 이 문서, 관련 설계·API·runbook, `TRINO_VERIFIED.md`를 먼저 읽는다.
2. Product Owner 또는 Codex가 지정한 **한 slice만** 구현한다. 다음 slice를 미리 scaffold하지 않는다.
3. Slice 0의 검증 결과가 `TRINO_VERIFIED.md`에 반영되기 전에는 배포 playbook이나 업그레이드 실행 버튼을 구현하지 않는다.
4. 새 결정이 필요하면 `[NEEDS-HUMAN-DECISION]`으로 표시하되, 현재 slice가 그 결정에 실제로 의존할 때만 중단한다.
5. 애플리케이션 코드 구현 후 기존 `develop` skill 절차대로 결정적 검사를 실행하고 Codex 독립 리뷰를 요청한다.
6. Claude는 `DECISIONS.md`를 임의로 바꾸지 않는다.

## 1. 운영자 결과

운영자는 새 VM 없이 기존 Trino cluster를 안전 순서대로 업그레이드하고, 같은 benchmark로 전후를 비교한 다음 traffic 복귀 또는 직전 release rollback을 선택할 수 있다.

완료된 작업에는 다음 증거가 한 화면에 남는다.

- 대상 cluster와 기존/목표 Trino version
- 사용한 Trino/JDK artifact의 파일명, 크기, SHA-256
- Gateway 차단·drain·node version/health 확인 결과와 시각
- 사전·사후 benchmark run과 비교 결과
- 단계별 actor, reason, 결과, 오류와 rollback 여부

## 2. 고정 제약

- TMS는 query path에 들어가지 않는다. TMS 중단과 다른 active cluster의 query 처리는 독립적이어야 한다.
- 첫 버전은 **cluster-wide in-place**만 지원한다. worker rolling upgrade, 혼합 version, Blue/Green, 자동 canary는 비범위다.
- 대상 backend를 Gateway에서 비활성화하고 실제 비활성 상태를 확인하기 전에는 drain이나 배포를 시작하지 않는다.
- drain 완료 전에는 benchmark나 배포를 시작하지 않는다. benchmark 종료 뒤에도 running query 0을 다시 확인한다.
- 모든 operational write는 관리자, non-blank reason, UI 확인, audit record를 요구한다.
- 실패하거나 상태를 알 수 없으면 traffic을 자동 복귀하지 않는다.
- UI는 playbook 경로, host, SSH 계정·password·private key를 입력받지 않는다. `/etc/tms` 설정과 기존 Ansible credential 경계를 사용한다.
- repository에는 artifact, credential, 내부 host/IP/URL을 넣지 않는다.
- 설정·catalog secret은 upgrade artifact에 포함하거나 TMS DB에 저장하지 않는다.
- Python 3.9 호환과 committed React bundle 규칙을 지킨다.

## 3. 재사용하고 새로 만들지 않는 것

| 필요 | 재사용 |
|---|---|
| Gateway traffic 차단·복귀 | `src/tms/clients/gateway.py`의 backend activate/deactivate |
| drain·안전 단계 | `src/tms/ops/sequence.py`, `service.py`, `repository.py`의 persisted state/event 패턴 |
| Ansible 실행·redaction·timeout | `src/tms/ops/ansible.py`, `src/tms/ops/process.py` |
| cluster/node 식별 | Gateway cluster 목록 + D-019 fleet discovery/inventory |
| benchmark 실행·비교 | `src/tms/bench/`의 query set, run, compare |
| 인증·감사 | 기존 admin guard와 audit repository |
| artifact 저장 | API `state_dir` 아래 전용 directory + DB metadata |

초기 버전에는 Celery/Redis, 별도 worker service, S3/Artifactory 연동, 범용 deployment engine, 범용 workflow DSL을 추가하지 않는다. 현재 단일 API 운영과 낮은 작업 빈도에서는 기존 실행기와 DB 단계 기록으로 충분하다. 프로세스가 사라진 실행은 이어서 추측하지 않고 `UNKNOWN/BLOCKED`로 전환한다.

## 4. 구현 선결 조건

### Slice 0 — 실제 환경 검증과 계약 확정

**운영자 결과**: Claude가 추측할 필요가 없는 검증된 upgrade/rollback 계약이 생긴다. 이 slice는 애플리케이션 코드를 변경하지 않는다.

개발 cluster에서 다음을 직접 검증하고 `TRINO_VERIFIED.md`에 명령·응답·결론·출처를 기록한다.

1. 지원할 `from_version → to_version` 한 조합과 각 version의 Java 요구사항
2. 업로드할 Trino distribution archive의 형식, 필수 경로, 안전한 압축 해제 규칙
3. 현재 systemd unit, install directory, data directory, config/catalog/plugin 경계
4. node별 stage → service stop → atomic release switch → service start 순서
5. coordinator와 worker의 안전한 stop/start 순서
6. 실패 지점별 직전 release 복구와 config/catalog/plugin 보존
7. `/v1/info`의 `nodeVersion.version`과 coordinator의 `system.runtime.nodes.node_version`으로 전 node version 수렴을 확인하는 방법
8. Gateway deactivate/activate 뒤 실제 backend 상태 확인과 다른 active backend 존재 확인
9. rollback 뒤 동일한 health/version/node 검증이 가능한지

**완료 조건**:

- 검증되지 않은 Java version, archive layout, 명령, 경로가 plan이나 code의 상수로 들어가지 않는다.
- upgrade와 rollback playbook의 입력·출력·idempotency·실패 계약이 문서화된다.
- 현재 `TODO.md`의 Catalog/Configuration/Safe Restart Ansible 현장 검증을 먼저 완료한다.
- 개발 cluster에서 current release 복구 연습이 성공한다.

## 5. 정상 작업 순서

아래 순서는 서버 상태 전이와 UI에서 모두 우회할 수 없어야 한다.

1. **Prepare**
   - 목표 Trino artifact와 필요할 때만 JDK artifact를 선택한다.
   - immutable SHA-256, 크기, archive 안전성, 선언 version을 확인한다.
   - 직전 known-good release와 rollback checkpoint가 없으면 시작을 거부한다.
   - inventory가 비어 있지 않고 모든 대상 node가 식별·접근 가능하며 현재 version drift가 없는지 확인한다.
   - 다른 cluster operation과 겹치지 않는지 확인한다.
2. **Capacity gate**
   - Gateway에 대상 외 active backend가 적어도 하나 있는지 확인한다.
   - 운영자가 낮은 traffic 시간대와 잔여 cluster 수용 가능성을 확인한다.
   - TMS는 추측성 capacity 계산이나 static weighted routing을 만들지 않는다.
3. **Stop intake**
   - 대상 backend를 deactivate한다.
   - Gateway 조회에서 inactive가 확인되지 않으면 중단한다.
4. **Drain**
   - fresh coordinator query snapshot으로 running query 0을 확인한다.
   - timeout이면 자동 강제 종료하지 않는다. 운영자가 기다리거나 작업을 취소한다.
5. **Baseline benchmark**
   - 선택한 query set을 대상 cluster에 실행하고 snapshot을 저장한다.
   - 모든 query가 종료된 뒤 running query 0을 다시 확인한다.
6. **Checkpoint and deploy**
   - node별 current release/config/catalog/plugin fingerprint와 복구 지점을 만든다.
   - artifact를 모든 node에 stage하고 checksum을 확인한 뒤 configured Ansible playbook으로 전환한다.
   - user input으로 host, shell fragment, playbook path를 만들지 않는다.
7. **Verify**
   - coordinator가 fresh GOOD이고 기대한 version인지 확인한다.
   - discovered coordinator/worker 수가 preflight snapshot과 일치하고 모든 `node_version`이 목표 version인지 확인한다.
   - resource group store와 필수 catalog health를 기존 health 계약으로 확인한다.
8. **Candidate benchmark**
   - baseline과 같은 query set snapshot·실행 조건으로 실행한다.
   - 실행 실패나 query mismatch가 있으면 traffic 복귀를 허용하지 않는다.
   - 초기 버전은 임의의 자동 성능 threshold를 만들지 않는다. 기존 compare 결과를 보여 주고 운영자가 판단한다.
9. **Decision**
   - 운영자가 결과를 보고 `traffic 복귀` 또는 `직전 release rollback`을 별도 확인한다.
10. **Restore traffic**
    - reactivation 직전에 fresh health/version/node/benchmark gate를 다시 평가한다.
    - Gateway activate 후 active 상태를 확인하고 완료한다.

## 6. 실패·취소·rollback 규칙

| 시점 | 허용 동작 | 금지 동작 |
|---|---|---|
| Gateway deactivate 전 | 취소 | 배포 |
| deactivate 후, deploy 전 | 기다리기·재시도·취소 후 기존 release health 확인 및 traffic 복귀 | 강제 query 종료 |
| deploy 시작 후 | 단계 재시도 또는 직전 known-good rollback | 단순 취소·검증 없는 traffic 복귀 |
| health/version 실패 | 원인 수정 후 재검증 또는 rollback | benchmark·traffic 복귀 |
| benchmark 실패/회귀 | 재실행 또는 rollback | 자동 성공 처리 |
| TMS/Ansible 상태 UNKNOWN | 현장 상태 reconcile 후 재시도/rollback | 마지막 명령을 자동 재전송 |
| rollback 실패 | backend inactive 유지, 수동 복구 runbook 안내 | 다른 cluster 영향·자동 traffic 복귀 |

Rollback은 원래 작업의 역순 버튼이 아니라 동일하게 감사되는 새 안전 작업이다.

- 즉시 rollback은 해당 operation의 `from_release`만 선택할 수 있다.
- 과거 release rollback은 그 release가 `known_good`이고 artifact·node checkpoint가 보존된 경우에만 새 upgrade operation으로 시작한다.
- 외부 metastore 데이터나 다른 서비스의 schema 변경은 TMS rollback 대상이 아니다. upgrade benchmark는 read-only여야 한다.
- 성공 후 artifact/checkpoint 삭제 정책은 첫 구현 범위가 아니다. 운영자가 직접 지우는 UI도 만들지 않는다.

## 7. 최소 데이터 모델

기존 migration 관례를 따라 다음 네 개의 목적만 저장한다. 범용 workflow schema로 일반화하지 않는다.

### `upgrade_artifact`

- kind: `trino` 또는 `jdk`
- 원래 파일명, 선언 version, byte size, SHA-256, 내부 storage key
- uploaded_by/at와 validation 결과
- archive byte는 DB가 아니라 `<state_dir>/upgrade-artifacts/<sha256>`에 atomic rename으로 저장

### `cluster_release`

- cluster, Trino artifact, 선택적 JDK artifact
- 실제 관측 version과 non-secret config/catalog/plugin fingerprint
- checkpoint reference, `known_good`, 생성 actor/time
- `known_good`은 성공한 검증과 traffic 복귀 뒤에만 설정

### `upgrade_operation`

- cluster, from/to release, benchmark query set snapshot과 baseline/candidate run ID
- 현재 phase, last error, actor, reason, timestamps
- 한 cluster에 active operation 하나라는 DB 제약

### `upgrade_event`

- operation, append-only sequence, phase/action/result, actor, reason, safe redacted detail, timestamp
- 상태 변경과 event/audit 삽입은 한 transaction에서 수행

필요 migration은 현재 028 다음 번호를 사용하고 API role grants를 별도 migration으로 관리한다.

## 8. Artifact 경계

Upload는 운영 서버의 신뢰 경계다. 다음 검사는 생략하지 않는다.

- admin만 upload 가능, upload 자체도 audit
- streaming 수신, 설정된 최대 크기 초과 시 즉시 중단
- temp file에 기록하면서 SHA-256 계산, 성공 시 digest 이름으로 atomic rename
- absolute path, `..`, archive 밖을 가리키는 symlink/hardlink, 중복 path, 비정상 파일 수·압축 비율 거부
- 사용자가 보낸 filename을 filesystem path로 사용하지 않음
- 검증 실패·중단 upload의 temp file 정리
- 같은 digest는 byte를 중복 저장하지 않음
- artifact 안의 `etc`/credential 허용 여부는 Slice 0에서 확정한다. 확정 전에는 distribution과 설정을 합친 bundle을 허용하지 않는다.
- 인터넷 download, Maven/GitHub/Artifactory URL 입력, 개별 plugin JAR upload는 초기 비범위

## 9. Ansible 계약

D-009의 실행 경계를 그대로 확장한다.

- 설정된 절대 playbook 경로와 cluster별 inventory file만 사용한다.
- inventory가 비거나 대상 node가 preflight snapshot과 다르면 시작하지 않는다.
- extra vars는 TMS가 생성한 operation ID와 검증된 state-dir 내부 artifact/checkpoint reference만 허용한다.
- `shell=True`, 사용자 입력 command fragment, host 목록 argv 전달을 금지한다.
- node별 stage/checksum을 모두 끝내기 전에는 release switch를 시작하지 않는다.
- playbook은 operation ID 기준으로 재실행 가능해야 하고 이미 끝난 안전 단계를 식별해야 한다.
- stdout/stderr는 기존 redaction을 거쳐 event에 제한적으로 저장한다.
- TMS는 exit code만으로 성공 판정하지 않고 Gateway, fresh health, `/v1/info`, `system.runtime.nodes`, benchmark를 별도로 확인한다.

초기에는 configured upgrade playbook 하나가 `preflight`, `upgrade`, `rollback`, `status` action을 받는 정도면 충분하다. 범용 remote execution UI는 만들지 않는다.

## 10. UI/API 최소 범위

### UI

- `Upgrade` 화면: cluster 선택, release/artifact, preflight 결과, 단계 timeline, benchmark 비교, traffic 복귀/rollback action
- 위험 action에는 대상 cluster와 version을 다시 보여 주는 confirmation
- backend inactive, fresh/stale health, current/target version, running query 수를 한 화면에서 구분
- 실패 시 generic success toast 대신 현재 안전 상태와 다음 허용 동작 표시
- artifact upload progress와 checksum 표시

### API

- artifact upload/list/detail
- cluster release history/detail
- preflight create/read
- upgrade operation start/read/events
- 현재 단계가 허용하는 retry, restore-traffic, rollback, safe-cancel action만 제공

서버가 transition을 검증한다. UI에서 버튼을 숨기는 것만으로 안전을 보장하지 않는다. write endpoint는 기존 admin/reason/audit 패턴을 재사용한다.

## 11. 구현 slices

### Slice 1 — Artifact registry

**운영자 결과**: 검증된 Trino/JDK artifact를 upload하고 checksum과 metadata를 조회할 수 있다. cluster에는 아무 변경도 없다.

**주요 AC**:

- trust-boundary 검사를 통과한 파일만 immutable 저장된다.
- 동일 digest upload는 storage를 중복하지 않는다.
- 실패·중단 upload가 완성 artifact처럼 보이지 않는다.
- admin/reason/audit와 저장 공간 오류가 테스트된다.
- 삭제 UI/API는 만들지 않는다.

### Slice 2 — Read-only preflight와 release bootstrap

**운영자 결과**: 첫 upgrade 전에 현재 known-good release와 복구 가능성, 대상 artifact, Gateway/cluster/node/Ansible 준비 상태를 확인할 수 있다.

**주요 AC**:

- preflight는 cluster를 변경하지 않는다.
- current version·node set·artifact/checkpoint가 일치하지 않으면 실패한다.
- 다른 active Gateway backend가 없거나 inventory가 비거나 node가 누락되면 실패한다.
- 결과는 fresh 시각과 실패 이유를 표시하며, 오래된 결과로 upgrade를 시작할 수 없다.

### Slice 3 — 완전한 단일 upgrade + 즉시 rollback

**운영자 결과**: 하나의 cluster를 고정 순서로 upgrade하고 benchmark 뒤 traffic 복귀 또는 직전 release rollback을 완료한다.

이 slice는 가장 작은 **파괴적 완성 단위**다. Gateway 차단부터 fresh 검증, benchmark, rollback, audit, UNKNOWN 복구까지 모두 준비되기 전에는 운영 UI에 start action을 노출하지 않는다.

**주요 AC**:

- 서버 transition test가 각 단계 건너뛰기를 거부한다.
- 단계 실패 후 Gateway backend는 inactive 상태다.
- baseline/candidate가 같은 query snapshot을 사용하지 않으면 traffic 복귀를 거부한다.
- 모든 node의 목표 version과 fresh GOOD health를 확인해야 traffic 복귀가 가능하다.
- TMS 재시작 뒤 실행 상태는 자동 재개되지 않고 reconcile 전까지 blocked다.
- 즉시 rollback은 from_release로만 가능하고 rollback 뒤 version/health/node 확인을 통과해야 복귀 가능하다.

### Slice 4 — Known-good history rollback

**운영자 결과**: 과거 successful known-good release를 골라 동일한 full sequence로 되돌릴 수 있다.

**주요 AC**:

- artifact나 checkpoint가 없는 history는 조회만 가능하고 선택할 수 없다.
- history rollback도 새 operation, reason, confirmation, audit를 가진다.
- arbitrary version 문자열이나 upload만 된 미검증 release는 rollback 대상이 아니다.

## 12. 예상 변경 파일

실제 이름은 기존 구조와 충돌하지 않는 선에서 최소화한다.

| 영역 | 예상 |
|---|---|
| migrations | `migrations/029_upgrade.sql`, `migrations/030_upgrade_grants.sql` |
| config | `src/tms/config.py`, `config.example.yaml`의 state dir/크기/configured playbook |
| domain/service | 기존 `src/tms/ops/` 아래 upgrade state·repository·service 추가 |
| process | 기존 `src/tms/ops/ansible.py`, `process.py` 재사용 또는 최소 확장 |
| clients | 기존 Gateway/Fleet/Health/Benchmark service 호출 |
| API | `src/tms/api/routes/upgrade.py`, app router 등록 |
| UI | `frontend/src/screens/Upgrade.tsx`, API client/type/nav 최소 변경 |
| bundle | `src/tms/ui/assets/` rebuild 결과 |
| tests | state transition, repository, API guard, artifact validation, Ansible command, UI flow |
| docs | API, configuration, operator usage, upgrade/rollback runbook, `TRINO_VERIFIED.md` |

새 top-level package나 별도 daemon은 실제 병목이 확인되기 전에는 만들지 않는다.

## 13. 결정적 검사

각 slice는 repository의 표준 명령에 더해 해당 검사를 남긴다.

- artifact archive traversal/link/bomb/partial upload/digest tests
- DB unique active-operation constraint와 append-only event tests
- 모든 불법 state transition의 table-driven unit test
- admin/reason/audit 누락 API tests
- Gateway inactive 미확인, running query 존재, stale health, node/version mismatch 차단 tests
- user input이 subprocess argv/playbook path/host list로 들어가지 않는 test
- TMS restart/UNKNOWN과 playbook partial failure recovery tests
- 같은 benchmark snapshot 비교와 실패 시 traffic restore 거부 test
- rollback from_release 제한과 검증 실패 test
- `npm --prefix frontend run build` 및 committed bundle diff 확인

GitHub CI는 DB·mock Gateway/Trino·mock Ansible로 결정적 자동 검사를 수행한다. 실제 SSH/systemd/Ansible/Gateway/Trino upgrade 성공은 CI가 대체하지 않으며 현장 검증 항목으로 남긴다.

## 14. 현장 검증

개발 cluster에서 아래 순서로 한 번씩 재현하고 증거를 runbook에 남긴다.

1. 정상 upgrade와 traffic 복귀
2. running query가 있는 drain 대기와 안전 취소
3. node 하나 stage 실패
4. 일부 node만 전환된 상태에서 재시도/rollback
5. coordinator 기동 실패 또는 bad config/catalog
6. version/node count mismatch
7. benchmark query 실패와 성능 회귀 표시
8. upgrade 중 TMS 재시작 후 UNKNOWN reconcile
9. rollback 성공과 rollback 실패
10. 다른 active backend가 없는 경우 시작 거부

운영 cluster 적용 전 Product Owner가 결과와 실제 maintenance window를 승인한다.

## 15. 완료 정의

- D-020과 이 문서의 순서를 우회하는 API/UI 경로가 없다.
- 실패·unknown·stale 상태가 healthy/success로 표시되지 않는다.
- target cluster가 inactive인 동안만 배포와 benchmark가 실행된다.
- traffic 복귀는 fresh health, node/version 수렴, 같은-set benchmark, 명시적 관리자 확인을 모두 요구한다.
- 직전 known-good rollback이 개발 cluster에서 재현된다.
- CI, frontend build, migration/grant 검사와 Codex 독립 리뷰가 통과한다.
- setup, usage, upgrade, rollback 문서가 운영자 관점에서 한 흐름으로 읽힌다.

## 16. 의도적으로 미룬 것

- Blue/Green, rolling/canary, 자동 성능 threshold, 자동 rollback
- artifact delete/retention UI, 외부 artifact repository 연동, network download
- 범용 provision/deployment/workflow 플랫폼
- SSH credential 관리, arbitrary playbook/command 실행
- extra VM capacity 계산과 static weighted routing

이 항목은 실제 운영 한계가 확인되거나 Product Owner가 별도 결정을 내릴 때만 추가한다.
