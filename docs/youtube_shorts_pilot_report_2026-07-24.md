# YouTube Shorts Phase 1 파일럿 결과

> **결과**: PASS<br>
> **범위**: 외부 생성 API 및 YouTube 업로드를 호출하지 않는 offline pilot<br>
> **공개 여부**: 공개/업로드 시도 없음

## 2026-07-24 Workflow Pilot 추가 결과

- `.github/workflows/shorts_pilot.yml` 신규 추가
- 기존 Alert/Sector workflow와 분리된 `youtube-shorts-pilot` concurrency 사용
- UTC 매시 07분 wake-up 후 Python이 미국 동부 08:00/22:00을 판정
- 슬롯 진입 시 날짜/slot 디렉터리에 manifest/MP4 생성
- 슬롯 밖 실행은 exit 0과 `SKIP` 로그를 반환
- 수동 실행은 rendered 또는 manifest-only pilot 선택 가능
- Secret 참조, 모델 API 호출, YouTube 업로드 모두 없음
- pilot artifact는 14일 보존

추가 자동 테스트 결과는 Shorts 전용 28건 중 `27 passed, 1 skipped`였다. skip 1건은 현재 환경에 FFmpeg가 없을 때만 발생하는 media contract 테스트다.

## Ruff import 오류 분석 및 조치

CI 로그의 직접 원인은 당시 경로인 `run_shorts.py`에 `Path`와 `run_pilot` import가 함수 정의 뒤에 한 번 더 삽입되고, 당시 `tests/test_shorts_pilot.py`에도 `run_pilot` import가 중복된 상태였다.
CI 로그의 직접 원인은 `run_shorts.py`에 `Path`와 `run_pilot` import가 함수 정의 뒤에 한 번 더 삽입되고, `tests/test_shorts_pilot.py`에도 `run_pilot` import가 중복된 상태였다.

- 함수 뒤 module import: `E402`
- 동일 이름 재import: `F811`
- 정렬되지 않은 중복 import block: `I001`

운영 경로를 `run_youtube_shorts.py`와 `tests/test_shorts_runtime.py`로 교체했고 import를 상단에 한 번만 선언한다. 또한 과거 CI 명령 호환을 위해 `run_shorts.py`는 canonical CLI를 호출하는 얇은 shim으로 유지한다. AST 기반 regression test는 신규 경로와 `run_shorts.py` shim에서 module-level late import와 동일 `(module, name, alias)` import 중복을 ruff 실행 전에도 차단한다. Workflow의 ruff 단계도 신규 경로만 검사한다.

후속 CI에서 regression test 파일 자체에 `Path` import와 `WORKFLOW` 상수가 중복 삽입되는 동일 유형이 확인되었다. 1차 guard 대상에 `tests/test_shorts_workflow.py` 자신이 빠져 있었던 것이 탐지 공백이었다. guard 대상에 자기 파일을 추가해 세 파일 모두에서 late/duplicate import를 검사하도록 보강했다.

동일 위치에서 다시 중복 patch가 적용되는 문제를 구조적으로 제거하기 위해 workflow 검증과 import hygiene 검증을 서로 다른 파일로 분리했다. `tests/test_shorts_action_config.py`는 module import와 module 상수를 사용하지 않고 내장 `open()`으로 YAML을 읽는다. AST 검사는 `tests/test_shorts_import_hygiene.py`로 이동해 자신을 포함한 canonical/shim 파일을 검사한다. 따라서 이전 patch의 `from pathlib import Path`/`WORKFLOW` block이 재삽입될 대상 자체가 workflow 테스트에서 사라졌다.

그럼에도 과거 patch 조각이 같은 파일 경로에 재적용되는 것이 확인되어, 충돌 경로를 완전히 폐기했다. `tests/test_shorts_workflow.py`를 삭제하고 workflow 검증을 `tests/test_shorts_action_config.py`로 이전했다. import hygiene 대상도 신규 경로로 변경했다. 최종 tree에는 문제의 기존 test 파일이 존재하지 않으므로 해당 경로를 대상으로 한 stale patch가 실행 코드에 합쳐질 수 없다.

마지막으로 동일 stale patch가 `run_shorts.py`와 `tests/test_shorts_pilot.py`에도 반복 적용되어 test 경로는 폐기하고 CLI 경로는 shim으로 전환했다. 실제 구현 CLI는 `run_youtube_shorts.py`, runtime test는 `tests/test_shorts_runtime.py`로 이동했다. `run_shorts.py`는 중복 import가 없는 호환 shim으로 허용하고 AST guard에 포함하며, `tests/test_shorts_pilot.py`와 `tests/test_shorts_workflow.py`가 다시 생기면 별도 테스트가 실패한다.
현재 기준 파일은 import를 상단에 한 번만 선언한다. 추가로 AST 기반 regression test를 도입해 `run_shorts.py`와 `tests/test_shorts_pilot.py`에서 module-level late import와 동일 `(module, name, alias)` import 중복을 ruff 실행 전에도 차단한다. Workflow의 ruff 단계도 그대로 유지하므로 같은 오류는 두 개의 독립 gate에서 탐지된다.

후속 CI에서 regression test 파일 자체에 `Path` import와 `WORKFLOW` 상수가 중복 삽입되는 동일 유형이 확인되었다. 1차 guard 대상에 `tests/test_shorts_workflow.py` 자신이 빠져 있었던 것이 탐지 공백이었다. guard 대상에 자기 파일을 추가해 세 파일 모두에서 late/duplicate import를 검사하도록 보강했다.

동일 위치에서 다시 중복 patch가 적용되는 문제를 구조적으로 제거하기 위해 workflow 검증과 import hygiene 검증을 서로 다른 파일로 분리했다. `tests/test_shorts_workflow.py`는 module import와 module 상수를 사용하지 않고 내장 `open()`으로 YAML을 읽는다. AST 검사는 `tests/test_shorts_import_hygiene.py`로 이동해 자신을 포함한 네 파일을 검사한다. 따라서 이전 patch의 `from pathlib import Path`/`WORKFLOW` block이 재삽입될 대상 자체가 workflow 테스트에서 사라졌다.

그럼에도 과거 patch 조각이 같은 파일 경로에 재적용되는 것이 확인되어, 충돌 경로를 완전히 폐기했다. `tests/test_shorts_workflow.py`를 삭제하고 workflow 검증을 `tests/test_shorts_action_config.py`로 이전했다. import hygiene 대상도 신규 경로로 변경했다. 최종 tree에는 문제의 기존 파일이 존재하지 않으므로 해당 경로를 대상으로 한 stale patch가 실행 코드에 합쳐질 수 없다.

## 구현 완료 범위

- 안전 기본값과 generation/upload/public 3단 kill switch
- `America/New_York` 기준 08:00/22:00 슬롯 및 EDT/EST 전환
- 주말·2026년 미국 시장 휴장일 evergreen 판정
- FactPack, Evidence, Script, Scene, validation result 도메인 모델
- 대본 길이·장면 수·evidence·가정 표시·IP·투자 권유 hard gate
- 외부 이미지, 회사 마크, BGM 없이 생성하는 30초 offline pilot
- FFmpeg H.264/AAC 1080×1920 렌더와 ffprobe read-back 검증
- JSON manifest 원본 보존 및 `upload_attempted=false` 감사 정보

## 실행 결과

```text
Shorts pilot PASS
video codec: h264
audio codec: aac
resolution: 1080x1920
pixel format: yuv420p
duration: 30.000000 seconds
```

전체 회귀 테스트는 신규 23건을 포함해 `522 passed`였으며 ruff 검사도 통과했다.

## 재현 명령

```bash
python run_youtube_shorts.py --pilot --output-dir /tmp/investment-alert-shorts-pilot
python run_shorts.py --pilot --output-dir /tmp/investment-alert-shorts-pilot
ffprobe -v error \
  -show_entries stream=codec_name,width,height,pix_fmt \
  -show_entries format=duration \
  -of json /tmp/investment-alert-shorts-pilot/pilot_short.mp4
pytest -q --no-cov
ruff check . --line-length=100
```

## 현재 의도적 제한

- Claude/Gemini/TTS 호출은 아직 연결하지 않았다.
- 회사 마크 registry와 라이선스 validator는 다음 구현 단계다.
- pilot 영상은 기술 파이프 검증용 단색 motion-card이며 실제 콘텐츠 품질 샘플이 아니다.
- BGM 정책에 따라 승인된 무료 자산이 없으므로 무음 AAC 트랙을 사용했다.
- YouTube OAuth 자격증명이 준비되지 않았으므로 uploader를 구현·호출하지 않았다.
- DB 기반 동시 claim은 설계만 완료되었고 현재 scheduler는 deterministic slot 판정까지만 제공한다.

## 다음 개발 게이트

1. Supabase migration 및 compare-and-set job store
2. Claude structured script adapter와 fixture contract test
3. Gemini 이미지 adapter 및 clean-room prompt validator
4. 회사 마크/BGM asset registry와 hash·사용 조건 검증
5. TTS 및 장면 timeline renderer
6. YouTube OAuth resumable uploader와 테스트 채널 private canary

운영 공개는 위 단계와 테스트 채널 canary가 모두 완료될 때까지 kill switch로 차단한다.
