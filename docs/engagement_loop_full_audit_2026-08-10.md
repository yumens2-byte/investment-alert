# Engagement Loop 전수 테스트·회귀 점검 보고서

> 점검일: 2026-08-10
>
> 대상 기준: `d3bee44` 이후 신규 Supabase engagement-loop 기반
>
> 결론: 기존 파이프라인 회귀는 발견되지 않았고, 신규 파이프라인에서 발견한 7개 방어 누락과 Python 3.11 CI import 회귀를 수정했다. 실제 Supabase 적용 검증은 관리자 credential이 없어 미수행 상태다.

## 1. 전수 테스트 결과

| 점검 | 결과 | 판정 |
|---|---:|---|
| 전체 pytest, coverage 제외 | 553 passed, 1 skipped(수정 전 기준선) | 기존 파이프라인 회귀 없음 |
| 수정 후 전체 pytest + coverage | 557 passed, 1 skipped | CI import 보완 포함 최종 회귀 없음 |
| 전체 pytest, 프로젝트 coverage gate | 통과 | 전체 80% gate 유지 |
| 신규 패키지 집중 테스트 | 27 passed | 신규 모델·저장소·migration·CI import 방어 통과 |
| 신규 패키지 coverage | 98.73% | 목표 90% 이상 |
| Ruff lint/format | 통과 | 정적 품질 이상 없음 |
| compileall | 통과 | 신규 Python 문법·import 이상 없음 |
| CI preflight | 8 passed | 기존 collector/sector 핵심 회귀 없음 |

기존 `shorts` 미디어 렌더링 테스트 1건은 환경 의존으로 기존과 동일하게 skip됐다. 실패나 신규 skip은 없다.

## 2. 신규 파이프라인에서 발견·수정한 사항

### F-01 판정 기준 동시 변경 가능성 — 수정

기존 trigger는 `OLD.status <> 'planned'`일 때만 criterion 변경을 막았다. 따라서 `planned → open` 전이와 criterion 변경을 한 SQL에 함께 담으면 공개 기준을 바꿀 수 있었다.

**수정:** OLD 또는 NEW가 planned가 아닌 순간 criterion 변경을 차단한다. planned 상태 안에서 초안을 다듬는 경우만 허용한다.

### F-02 비정상 상태 역행 — 수정

DB check는 status 값의 철자만 제한하고 `open → planned`, `closed → open` 같은 역행을 막지 않았다.

**수정:** trigger에 허용 상태 전이 표를 적용하고 종결 상태는 자기 상태 외 전이를 금지했다.

### F-03 감사 이벤트 변조 가능성 — 수정

`append_event()`가 일반 upsert를 사용해 동일 `event_id`의 기존 감사 이벤트를 갱신할 수 있었다.

**수정:** repository는 `ignore_duplicates=True`로 충돌 시 DO NOTHING을 요청하고, DB trigger는 service role을 포함한 모든 UPDATE를 거부한다.

### F-04 기존 Supabase sequence까지 광범위하게 권한 부여 — 수정

migration의 `ALL SEQUENCES IN SCHEMA public`은 신규 파이프라인이 필요하지 않은 기존 sequence까지 service role grant 범위에 포함했다.

**수정:** 신규 identity sequence 3개만 명시적으로 grant한다.

### F-05 승인 없는 published row 가능성 — 수정

기존 constraint는 X post ID와 발행 시각만 있으면 승인 hash나 승인자 없이 `published`가 될 수 있었다.

**수정:** `approved`와 `published`는 승인 hash가 본문 hash와 일치하고 승인자·승인 시각·만료 시각이 모두 존재해야 한다.

### F-06 조회 장애와 데이터 부재 혼동 — 수정

`get_loop()`가 Supabase 장애에서도 `None`을 반환해 “루프가 없음”으로 오인하고 후속 생성을 시도할 수 있었다.

**수정:** 실제 row 부재에만 `None`을 반환하고 DB 오류는 `RepositoryUnavailableError`로 fail-closed 처리한다.

### F-07 비정상 Fact와 활성 loop 불완전성 — 수정

NaN/Infinity 값, 수집 시점보다 미래인 `as_of`, criterion이 3개가 아닌 활성 loop를 도메인 모델이 허용했다.

**수정:** Fact 유한값·시간 순서를 검증하고 `OPEN` 이후 상태는 정확히 3개 criterion을 요구한다.

### F-08 GitHub Actions Python 3.11 import 실패 — 수정

GitHub Actions가 `pytest tests/ ...` console script로 실행될 때 저장소 루트가 import path로 확정되지 않았고, `tests/engagement_loop`도 명시적인 test package가 아니어서 `engagement_loop` import가 collection 단계에서 실패했다. 그 결과 신규 테스트가 실행되지 않은 채 전체 coverage도 22.68%로 중단됐다.

**수정:** `pytest.ini`에 `pythonpath = .`을 지정해 console script와 `python -m pytest`의 경로 차이를 제거하고, `tests/engagement_loop/__init__.py`를 추가해 테스트 모듈을 `tests.engagement_loop.*` namespace로 고정했다. CI와 동일한 `pytest tests/ -v --cov-fail-under=80` 명령을 별도로 실행해 collection과 coverage gate 통과를 확인했다.

## 3. 기존 파이프라인 영향 점검

- 신규 변경은 `engagement_loop/`, 전용 migration과 테스트에 한정했다.
- 기존 alert, sector, weekly news, Shorts 실행 파일과 workflow를 수정하지 않았다.
- 수정 후 전체 558개 테스트 수집 결과에서 신규 27개를 제외한 기존 테스트 531개가 모두 회귀 없이 수행됐고, 그중 기존 환경 의존 테스트 1개만 skip됐다.
- `scripts/ci_preflight.sh`의 collector fallback, event scarcity guard, sector rotation 핵심 테스트가 모두 통과했다.
- 신규 migration은 기존 테이블에 FK·trigger·RLS를 추가하지 않는다.

## 4. 아직 검증하지 못한 외부 항목

다음 항목은 repository 내부 자동 테스트만으로 검증할 수 없다.

1. 실제 Supabase PostgreSQL에서 migration 전체 실행과 rollback
2. 생성된 identity sequence의 실제 이름 및 service role grant 확인
3. anon/authenticated key로 6개 테이블 접근이 실제 거부되는지 확인
4. service role로 loop/fact/event round-trip이 동작하는지 확인
5. PostgREST가 `ignore_duplicates=True`를 `resolution=ignore-duplicates`로 전달하는지 실제 프로젝트 확인

운영 적용 전 test Supabase project에서 위 5개 smoke test를 수행해야 한다. 이 검증 전에는 `ENGAGEMENT_LOOP_ENABLED=false`, `ENGAGEMENT_LOOP_AUTO_PUBLISH=false`를 유지한다.

## 5. 실행 명령

```bash
python -m pytest tests/ --no-cov -q
python -m pytest tests/
python -m pytest tests/engagement_loop/ -o addopts='' \
  --cov=engagement_loop --cov-report=term-missing --cov-fail-under=90
ruff format --check engagement_loop tests/engagement_loop
ruff check engagement_loop tests/engagement_loop
python -m compileall -q engagement_loop
bash scripts/ci_preflight.sh
git diff --check
```
