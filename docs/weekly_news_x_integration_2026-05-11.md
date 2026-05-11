# Pull Request: weekly_news_x 모듈 통합

## 변경 요약

기존 investment-alert 시스템에 **주말 미국 뉴스 X 스레드 자동화** 모듈을 격리 통합.

- 매주 **토요일·일요일 09:00 KST** 자동 수집 → 마스터 PR 검수 → Merge 시 X 스레드 발행
- 토/일 워크플로우 분리 (`weekly_news_draft.yml`, `weekly_news_draft_sunday.yml`) → 독립 활성/비활성 가능
- **재발행 방지**: 발행 성공 시 sidecar JSON 자동 생성 + commit, 동일 archive 재발행 시 자동 skip
- 강제 재발행: `FORCE_REPUBLISH=true` Variable 설정 시 강제 재발행 (기존 sidecar는 `previous` 필드로 보존)
- **Telegram 알림** (기존 `TelegramPublisher` 재활용): 발행 성공/실패 시 INTERNAL 운영자 채널 자동 알림
- **기존 alert.yml 파이프라인 영향 0** (별도 워크플로우 + concurrency group)
- 기존 코드 수정 최소화 (requirements.txt 1줄, pyproject.toml 주석 1줄, pytest.ini 1줄)

## 변경 매트릭스

### 신규 파일 (13개)

| # | 경로 | 역할 |
|---|---|---|
| 1 | `.github/workflows/weekly_news_draft.yml` | 토요일 cron → PR 생성 |
| 2 | `.github/workflows/weekly_news_draft_sunday.yml` | 일요일 cron → PR 생성 |
| 3 | `.github/workflows/weekly_news_publish.yml` | PR merge → X 발행 (토/일 공용) |
| 4 | `publishers/weekly_news_x/__init__.py` | 패키지 init |
| 5 | `publishers/weekly_news_x/collect.py` | Claude API + web_search 수집 |
| 6 | `publishers/weekly_news_x/publish.py` | tweepy 스레드 발행 + sidecar + notifier |
| 7 | `publishers/weekly_news_x/notifier.py` | Telegram INTERNAL 알림 (기존 TelegramPublisher 재활용) |
| 8 | `publishers/weekly_news_x/comic_voice.py` | (옵션) 코믹 캐릭터 한줄평 |
| 9 | `publishers/weekly_news_x/notion_sync.py` | (옵션) Notion DB 적재 |
| 10 | `publishers/weekly_news_x/image_gen.py` | (옵션) DALL-E 헤더 이미지 |
| 11 | `publishers/weekly_news_x/prompts/us_news_summary.md` | 메인 시스템 프롬프트 v3 |
| 12 | `publishers/weekly_news_x/prompts/comic_voice.md` | 코믹 캐릭터 프롬프트 |
| 13 | `tests/test_weekly_news_x.py` | 단위 테스트 70건 |

### 수정 파일 (3개, 총 +3 라인)

| 경로 | 변경 내용 |
|---|---|
| `requirements.txt` | `anthropic>=0.40.0` 추가 (1줄) |
| `pyproject.toml` | 주석 1줄 추가 (실질 변경 없음) |
| `pytest.ini` | `--cov=publishers.weekly_news_x` 추가 (1줄) |

### 무수정 보장 (diff 검증 완료)

- `run_alert.py`: 동일
- `.github/workflows/alert.yml`: 동일
- `publishers/x_publisher.py`: 동일

## 충돌 차단 검증

| 차원 | 기존 alert.yml | 신규 weekly_news_* | 격리 |
|---|---|---|---|
| concurrency group | `alert-pipeline` | `weekly-news-draft` / `weekly-news-draft-sunday` / `weekly-news-publish` | ✅ |
| cron | `*/45 * * * *` | `0 0 * * 6` (토) + `0 0 * * 0` (일) | ✅ |
| PR 브랜치 | - | 토 `weekly-news/{date}` / 일 `weekly-news-sun/{date}` | ✅ |
| X 시크릿 | `X_ACCESS_TOKEN_SECRET` | 동일 사용 (재등록 불필요) | ✅ |
| AI | `GEMINI_API_KEY` | `ANTHROPIC_API_KEY` (신규) | ✅ |
| entrypoint | `run_alert.py` | `python -m publishers.weekly_news_x.collect` | ✅ |
| X 발행 함수 | `XPublisher.publish()` (단일) | `post_thread()` (스레드 체이닝) | ✅ |
| 출력 | `logs/` | `logs/weekly_news/` | ✅ |

## 신규 시크릿/변수 등록 필요

### 필수 (2개)

- `ANTHROPIC_API_KEY` (Secret)
- `X_SCREEN_NAME` (Secret)

### 옵션

- `APPEND_COMIC_VOICE=true` (Variable) — 코믹 캐릭터 한줄평
- `NOTION_TOKEN`, `NOTION_DB_ID` (Secret) — Notion 적재
- `OPENAI_API_KEY` (Secret) + `ATTACH_IMAGE=true` (Variable) — 이미지 첨부

## 테스트 결과

```
$ python -m pytest tests/
======================== 299 passed in 20.80s ========================
Required test coverage of 80% reached. Total coverage: 88.23%
```

- 기존 193건 + 신규 106건 = 299건 PASS
- Coverage 88.23% (기존 임계 80% 유지)
- 회귀 0건

### weekly_news_x 패키지 모듈별 cov

| 모듈 | LOC | cov |
|---|---|---|
| `__init__.py` | 2 | 100% |
| `collect.py` | 70 | 99% |
| `comic_voice.py` | 60 | 97% |
| `image_gen.py` | 29 | 100% |
| `notifier.py` | 35 | **100%** |
| `notion_sync.py` | 47 | 98% |
| `publish.py` | 196 | 92% |
| **패키지 평균** | - | **약 97%** |

## ruff 검증

```
$ ruff check publishers/weekly_news_x/ tests/test_weekly_news_x.py --line-length=100
All checks passed!
```

## 운영 플로우

```
[토요일 09:00 KST] weekly_news_draft.yml cron
    ↓
collect.py → logs/weekly_news/YYYY/MM/...md
    ↓
[옵션] comic_voice.py → 한줄평 추가
[옵션] notion_sync.py → Notion 적재 (Status: Draft)
    ↓
peter-evans/create-pull-request (label: weekly-news-draft)
    ↓
[마스터 PR 검수 → Merge]
    ↓
weekly_news_publish.yml 자동 트리거
    ↓
publish.py: 재발행 방지 검사 (sidecar 존재 시 skip)
    ↓
tweepy 스레드 발행 (체이닝)
[옵션] image_gen.py → DALL-E 헤더 첨부
    ↓
sidecar .meta.json 생성 + git auto-commit
[옵션] notion_sync.py → Status: Published 적재
    ↓
Step Summary: Thread URL + sidecar 경로 출력
```

## 재발행 방지 시나리오

| 케이스 | sidecar | FORCE_REPUBLISH | 동작 |
|---|---|---|---|
| 첫 발행 | 없음 | - | 발행 진행, sidecar 작성 |
| 동일 archive 재트리거 | 있음 | (default) | skip (exit 0) |
| 의도적 재발행 (마스터 수동) | 있음 | `true` | 발행 진행, 이전 sidecar는 `previous` 필드 보존 |
| archive 수정 후 새 발행 | 있음 (구버전) | `true` | 발행 진행 |

> sha256 검사는 의도적으로 미포함 (단순성 우선). archive 내용 수정 후 재발행은 `FORCE_REPUBLISH` 수동 설정 필요.

## 마이그레이션 절차 (마스터 수행)

1. 이 PR을 main에 merge
2. Settings → Secrets에 `ANTHROPIC_API_KEY`, `X_SCREEN_NAME` 등록
3. Settings → Actions → Workflow permissions → Read/Write + PR 생성 권한 허용
4. Actions → "Weekly News Draft" → Run workflow (수동 검증)
5. 자동 생성된 PR 검수 → Merge → Thread URL 확인

## 비용 영향

- Claude API (Sonnet 4.5 + web_search): 월 ~$1
- 기존 X API Basic: $200 (그대로, 추가 비용 0)
- 기존 GitHub Actions Public: $0 (그대로)
