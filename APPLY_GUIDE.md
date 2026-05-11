# v10 핫픽스 적용 가이드

## 적용 대상 파일 4개
- publishers/weekly_news_x/collect.py (v1.1.0 → v1.2.0)
- publishers/weekly_news_x/notion_sync.py (v1.0.0 → v1.0.1)
- publishers/weekly_news_x/prompts/us_news_summary.md (v3 → v4)
- tests/test_weekly_news_x.py (신규 검증 테스트 5건 추가)

## 적용 절차
1. 본 zip 압축 해제
2. 4개 파일을 마스터 저장소에 그대로 덮어쓰기
3. git add + commit + push

```bash
unzip weekly-news-x-v10-hotfix.zip
cp -r final_patch_v10/publishers/* /path/to/investment-alert/publishers/
cp final_patch_v10/tests/test_weekly_news_x.py /path/to/investment-alert/tests/

cd /path/to/investment-alert
git add publishers/weekly_news_x/collect.py
git add publishers/weekly_news_x/notion_sync.py
git add publishers/weekly_news_x/prompts/us_news_summary.md
git add tests/test_weekly_news_x.py
git commit -m "fix(weekly-news): v10 핫픽스 — 메타-질문 차단 + notion_sync exit 0"
git push
```

## 변경 내용 요약

### collect.py v1.1.0 → v1.2.0
- 응답 형식 사후 검증 추가 (청크 < 5 또는 메타 패턴 감지 시 차단)
- 신규 stage: `invalid_format`
- archive 저장 차단 + notify_draft_failure 알림

### notion_sync.py v1.0.0 → v1.0.1
- 시크릿 미설정 시 exit 0 반환 (의도된 옵션 비활성 = 정상 skip)
- Actions UI 빨간 X 표시 사라짐

### us_news_summary.md v3 → v4
- 메타 질문·옵션 제안 절대 금지 명시
- 시간 범위 자동 확대 정책 (24h → 72h → 7일)
- "한국 투자자 관점" 의미 정의 명확화
- "당신은 자동화 시스템 함수다" 강조

### test_weekly_news_x.py
- 신규 5건: 메타 패턴 감지, 청크 부족 거부, 정상 마크다운 수락, notion_sync skip exit 0
- 기존 2건 수정: 8청크 마크다운 fixture 사용

## 검증 결과 (적용 전)
- ruff: All checks passed
- Python 3.11 AST 컴파일: 7개 모듈 통과
- f-string 백슬래시: 0건
- pytest: 312/312 PASS
- Coverage: 88.61%
