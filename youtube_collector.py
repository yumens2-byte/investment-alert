# investment-alert

미국 증시 긴급 Alert 감지 시스템 — 뉴스 및 YouTube 기반 실시간 시장 변동 감지.

## 개요

Investment OS 생태계의 Alert 전담 서브시스템. 뉴스 및 유튜버 콘텐츠를 병렬 수집·분석하여 L1(CRITICAL)/L2(HIGH)/L3(MEDIUM) 레벨의 Alert를 자동 판정합니다.

## 아키텍처

```
┌──────────────────────────────────────────────────────┐
│           MacroNewsLayer (통합 감지 레이어)             │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────────────┐     ┌──────────────────┐      │
│  │  NewsCollector   │     │ YouTubeCollector │      │
│  │                  │     │                  │      │
│  │  ▸ Tier S/A/B    │     │  ▸ 4 채널        │      │
│  │  ▸ RSS 기반       │     │  ▸ 48시간 윈도우 │      │
│  │  ▸ 키워드 필터     │     │  ▸ 한글 키워드    │      │
│  └────────┬─────────┘     └────────┬─────────┘      │
│           │                        │                 │
│           └──────────┬─────────────┘                 │
│                      ▼                               │
│           ┌──────────────────────┐                   │
│           │   NewsValidator      │                   │
│           │   (추측성/재탕 제외)   │                   │
│           └──────────┬───────────┘                   │
│                      ▼                               │
│           ┌──────────────────────┐                   │
│           │   Score 산출          │                   │
│           │   Tier × Source      │                   │
│           │   × YouTube Bonus    │                   │
│           └──────────┬───────────┘                   │
│                      ▼                               │
│           ┌──────────────────────┐                   │
│           │   L1/L2/L3 판정      │                   │
│           └──────────────────────┘                   │
└──────────────────────────────────────────────────────┘
```

## 프로젝트 구조

```
investment-alert/
├── collectors/            # 데이터 수집
│   ├── base.py            #   BaseCollector + CollectorEvent
│   ├── news_collector.py  #   Day 2
│   └── youtube_collector.py #   Day 3
├── validators/            # 이벤트 검증
│   └── news_validator.py  #   Day 2
├── detection/             # 감지 레이어
│   └── macro_news_layer.py #   Day 4
├── config/
│   └── settings.py        # 환경변수 및 상수
├── core/
│   ├── exceptions.py      # 커스텀 예외
│   └── logger.py          # 로거
├── tests/                 # pytest 테스트
├── pyproject.toml         # ruff 설정
├── pytest.ini             # pytest 설정 (80% threshold)
├── requirements.txt       # 의존성
└── .github/workflows/
    └── test.yml           # CI (ruff + pytest)
```

## 빠른 시작

```bash
# 1. 의존성 설치
python -m venv venv
. venv/bin/activate
pip install -r requirements.txt

# 2. 환경변수 설정
cp .env.example .env
# .env 파일 편집

# 3. 테스트 (GTT팀 공통지침 11 준수)
ruff check . --line-length=100
pytest tests/ -v
```

## 개발 원칙

1. **반말 금지 (대화)** · **정확성 > 속도**
2. **VERSION 상수 필수** (모든 모듈) + 실행 시작 로그에 버전 출력 (GTT팀 지침 5)
3. **ruff + pytest 세트 통과** (GTT팀 지침 11)
4. **coverage ≥ 80%** (pytest.ini fail_under 강제)
5. **주석 표준**: 파일 헤더 / 클래스 / 함수 / 인라인 모두 `제목 + 내용` 포함

## Alert 레벨 판정 규칙

| 레벨 | 조건 |
|------|------|
| L1 (CRITICAL) | Tier S 이벤트 OR (score ≥ 7.0 AND source_count ≥ 2 AND health ≥ 0.90) |
| L2 (HIGH) | 5.0 ≤ score < 7.0 OR 유튜브 단독 긴급 (ai_score ≥ 6.0) |
| L3 (MEDIUM) | 3.0 ≤ score < 5.0 |
| NONE | score < 3.0 |

## 상위 허브

- 자동화 허브(Notion): Investment OS — 자동화 허브
- 세분화 허브(Notion): investment Alert - 세분화

## 라이선스

Private — EDT Investment Team
