# YouTube Shorts 파이프라인 충돌 분석 및 격리 검증 보고서

> **결과**: PASS<br>
> **범위**: 분석 → 격리 설계 → 상세설계 반영 → 개발 → 파일럿 → 전수 회귀<br>
> **보호 대상**: Alert, Sector Alert, Weekly News 및 기존 publisher/collector 전체

## 1. 관찰된 충돌

충돌은 기존 비즈니스 로직 간 runtime dependency가 아니라, Shorts PR에 과거 patch 조각이 반복 합쳐지면서 발생한 source assembly 충돌이었다.

| 폐기 경로 | 반복 삽입 내용 | 결과 |
|---|---|---|
| `run_shorts.py` | `Path`, `run_pilot` import block | E402, F811 |
| `tests/test_shorts_pilot.py` | `run_pilot` import | I001, F811 |
| `tests/test_shorts_workflow.py` | `Path`, `WORKFLOW` prefix | E402 및 중복 정의 |

기존 `run_alert.py`, `run_sector_alert.py`, collector, detector, publisher에는 Shorts import가 없었고 오류도 없었다.

## 2. 격리 설계

stale patch가 파일 내용과 결합하지 못하도록 충돌 대상 경로를 재사용하지 않는다.

```text
run_shorts.py                 → 삭제
run_youtube_shorts.py         → 신규 canonical CLI

tests/test_shorts_pilot.py    → 삭제
tests/test_shorts_runtime.py  → 신규 runtime/pilot tests

tests/test_shorts_workflow.py     → 삭제
tests/test_shorts_action_config.py → 신규 workflow configuration tests
```

GitHub Actions는 신규 경로만 실행하고 `tests/test_shorts_*.py` 전체를 테스트한다. AST guard는 canonical 파일만 검사하고 구 경로가 다시 나타나면 실패한다.

## 3. 기존 비즈니스 무영향 경계

- 기존 workflow 파일은 수정하지 않는다.
- Shorts workflow concurrency는 `youtube-shorts-pilot`로 독립한다.
- Shorts workflow는 `contents: read`만 사용하고 Secret을 참조하지 않는다.
- `SHORTS_ENABLED`, `SHORTS_UPLOAD_ENABLED`, `SHORTS_PUBLIC_ENABLED`는 false다.
- 기존 DB migration, Alert entrypoint, publisher, collector를 변경하지 않는다.
- 현재 pilot은 모델 API, Supabase write, YouTube upload를 호출하지 않는다.

## 4. 상세 검증 항목

### Source assembly

- 구 경로 3개 부재
- Python 전 파일 AST parse
- Shorts canonical 파일 late/duplicate import 검사
- Ruff 전체 검사 및 `--fix` 후 working tree 무변경 확인

### Workflow

- YAML parser 통과
- 신규 CLI만 참조
- Shorts test wildcard 실행
- 기존 entrypoint 참조 없음
- Secret 참조 없음

### Pilot

- 슬롯 안 manifest 생성
- 슬롯 밖 exit 0 safe skip
- `upload_attempted=false`
- FFmpeg 가능 환경에서 media contract 검사

### Regression

- repository 전체 pytest 및 coverage 80% gate
- 기존 핵심 `scripts/ci_preflight.sh`
- 기존 workflow/source 파일 diff 없음

## 5. 완료 기준

- [x] 세 충돌 경로 폐기
- [x] canonical 경로로 workflow/문서/test 전환
- [x] Shorts lint/test 통과
- [x] pilot due/skip 통과
- [x] 전체 repository 회귀 통과
- [x] 기존 핵심 preflight 통과
- [x] 기존 비즈니스 파일 변경 0

본 보고서의 PASS는 offline pilot 격리와 source 충돌 해소를 의미한다. YouTube production readiness는 별도 R1~R6 종료 조건을 계속 따른다.
