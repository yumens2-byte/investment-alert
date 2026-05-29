# YouTube 접근성 점검 (2026-05-03)

## 점검 요약
- 점검 기준: 제공된 `run_diagnostics.py`/`run_alert.py` 로그.
- 총 7개 채널 중 1개만 RSS 접근 정상(HTTP 200), 6개 채널은 접근 실패(HTTP 404/500).
- 따라서 현재 YouTube 입력 커버리지는 **심각 저하 상태**로 판단.


## 운영 영향
- Macro-News의 YouTube 이벤트 입력이 0건으로 수렴할 가능성이 높아,
  `youtube_channel_failures` 경고가 반복 발생.
- 현재는 정책상 `NONE` 판정으로 발행 스킵되며, 장애는 아니지만 감지 커버리지 저하가 큼.

## 즉시 권장 조치
1. `YOUTUBE_CHANNELS`의 채널 ID 재검증 (채널 홈 URL 기준 최신 UC ID 재수집).
2. 404 채널 우선 정정 후 재실행 (`run_diagnostics.py` → `run_alert.py`).
3. 500 채널은 1~2시간 간격 재시도해 일시 장애 여부 확인.
4. 임시로 정상 채널(현재 김단테) 외 대체 채널 2~3개 추가.

## 재점검 기준
- 목표: 7채널 중 최소 5채널 이상 HTTP 200 복구.
- 복구 확인 후 `youtube_channel_failures` 경고 빈도 하락 여부 모니터링.
