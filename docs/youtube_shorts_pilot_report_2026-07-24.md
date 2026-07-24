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
