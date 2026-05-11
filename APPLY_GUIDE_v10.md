# v10 핫픽스 적용 가이드

## 적용 대상 파일 (4개만)
- publishers/weekly_news_x/collect.py
- publishers/weekly_news_x/notion_sync.py
- publishers/weekly_news_x/prompts/us_news_summary.md
- tests/test_weekly_news_x.py

## 변경 안 되는 파일 (건드리지 말 것)
- publishers/weekly_news_x/publish.py (이미 v1.3.1)
- publishers/weekly_news_x/notifier.py (이미 4함수)
- publishers/weekly_news_x/comic_voice.py
- publishers/weekly_news_x/image_gen.py
- .github/workflows/*.yml (모두 정상)
- 기타 기존 파일

## 핵심 변경 내용

### 1) collect.py v1.1.0 → v1.2.0
응답 형식 사후 검증 추가:
- 청크 수 < 5개 → 거부
- 메타 패턴 ("사용자님", "옵션 1", "어떻게 진행" 등) 감지 시 거부
- 거부 시 archive 저장 안 함 + Telegram "DRAFT FAILED" 알림

### 2) notion_sync.py v1.0.0 → v1.0.1
시크릿 미설정 시 exit 0 (정상 skip):
- NOTION_TOKEN/DB_ID 미설정 → return 0 (옵션 비활성)
- 시크릿 있는데 실패 → return 1 (실제 실패)
- Actions UI에서 빨간 X 표시 사라짐

### 3) us_news_summary.md v3 → v4
시스템 프롬프트 강화:
- 메타-질문 강력 금지 ("사용자님", "옵션 1/2", "어떻게 진행" 명시)
- 시간 범위 자동 확대 정책 (24h → 72h → 7일)
- "한국 투자자 관점" = "한국 관련 뉴스" 아님 명시
- "당신은 자동화 시스템 함수다" 강조

### 4) tests/test_weekly_news_x.py
신규 5건 + 기존 2건 수정:
- test_collect_main_rejects_meta_question_response (신규)
- test_collect_main_rejects_too_few_chunks (신규)
- test_collect_main_accepts_valid_8chunk_markdown (신규)
- test_collect_main_rejects_partial_meta_pattern (신규)
- test_notion_sync_main_skip_when_secrets_missing (신규)
- test_collect_main_weekday_in_output (수정 - 8청크 fixture)
- test_collect_main_success_writes_github_output (수정)
- test_notion_sync_main_no_archive (수정 - 시크릿 setenv 추가)
- test_notion_sync_main_success (수정)
- test_notion_sync_main_failure (수정)

## 사전 검증 결과 (적용 전)
- ruff: All checks passed
- Python 3.11 AST 컴파일: 7개 모듈 통과
- f-string 백슬래시: 0건
- pytest: 312/312 PASS
- Coverage: 88.61%

## Notion DB 추가 작업 (적용과 별개)
DB의 Status 속성에 다음 옵션 추가 필요:
- Draft
- Published

옵션 누락 시 Notion API 401: option does not exist 에러 발생
