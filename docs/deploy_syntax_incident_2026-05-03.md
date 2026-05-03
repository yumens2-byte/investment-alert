# 배포 오류 점검 메모 (2026-05-03)

## 증상
- CI에서 `ruff check . --line-length=100 --fix` 수행 시
  `collectors/youtube_collector.py` 구문 오류(Unexpected indentation 등) 다수 발생.

## 원인 정리
- 보고된 스택은 `_collect_channel()` 블록 중간에 잘못 끼어든 문자열 라인으로 인해
  `try/except` 블록 구조가 깨졌을 때 나타나는 전형적 패턴.
- 현재 브랜치 기준 파일은 해당 깨짐 라인이 제거된 정상 구조임.

## 현재 상태
- 동일 명령 재실행 결과 통과:
  - `ruff check . --line-length=100 --fix` ✅
- 즉, 현 시점 코드베이스에서는 재현되지 않음(과거 아티팩트/중간 커밋 로그로 판단).

## 재발 방지 운영 팁
1. 배포 직전 `ruff check . --line-length=100 --fix`를 필수 preflight로 실행.
2. 충돌 발생 시 `collectors/youtube_collector.py`의 `_collect_channel()` try/except 블록 우선 점검.
3. 실패 로그에 `line 19x~24x`가 나오면 문자열/괄호/들여쓰기 깨짐 여부를 먼저 확인.
