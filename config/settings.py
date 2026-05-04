"""
제목: 프로젝트 전역 설정
내용: 환경변수 로드 및 전역 상수를 정의합니다.
      .env 파일이 존재하면 자동 로드하고, 누락된 필수 설정은 경고를 남깁니다.

주요 함수:
  - get_env(key, default, required): 환경변수 안전 조회
  - get_env_float(key, default): float 변환 포함 조회
  - get_env_bool(key, default): bool 변환 포함 조회

주요 상수:
  - DRY_RUN: 모의 실행 여부
  - YOUTUBE_CHANNELS_RAW: 유튜브 채널 원본 문자열
  - CHANNEL_WEIGHTS: 유튜브 채널별 신뢰도 가중치
  - NEWS_SOURCE_REGISTRY: 뉴스 Tier별 소스 정의
  - TIER_WEIGHTS: 뉴스 Tier별 점수 가중치
  - LEVEL_THRESHOLDS: L1/L2/L3 판정 임계값
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────
# .env 파일 로드
# ────────────────────────────────────────────────────────
load_dotenv()


# ────────────────────────────────────────────────────────
# 환경변수 헬퍼
# ────────────────────────────────────────────────────────
def get_env(key: str, default: str | None = None, required: bool = False) -> str | None:
    """
    제목: 환경변수 안전 조회
    내용: os.getenv 래퍼. required=True이면서 값이 없으면 경고 로그 출력.

    Args:
        key: 환경변수 키
        default: 기본값
        required: 필수 여부 (누락 시 경고)

    Returns:
        환경변수 값 또는 default
    """
    value = os.getenv(key, default)
    if required and not value:
        logger.warning(f"[settings] 필수 환경변수 누락: {key}")
    return value


def get_env_float(key: str, default: float) -> float:
    """
    제목: float 환경변수 조회
    내용: 환경변수를 float으로 변환. 실패 시 default 반환.

    Args:
        key: 환경변수 키
        default: 변환 실패 시 기본값

    Returns:
        float 값
    """
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"[settings] {key} float 변환 실패 (값: {raw}) → 기본값 {default} 사용")
        return default


def get_env_bool(key: str, default: bool = False) -> bool:
    """
    제목: bool 환경변수 조회
    내용: "true"/"1"/"yes"(대소문자 무시)를 True로 변환.

    Args:
        key: 환경변수 키
        default: 기본값

    Returns:
        bool 값
    """
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


# ────────────────────────────────────────────────────────
# 운영 제어
# ────────────────────────────────────────────────────────
DRY_RUN: bool = get_env_bool("DRY_RUN", default=True)
FORCE_RUN: bool = get_env_bool("FORCE_RUN", default=False)
LOG_LEVEL: str = get_env("LOG_LEVEL", default="INFO") or "INFO"


# ────────────────────────────────────────────────────────
# YouTube 채널 설정
# ────────────────────────────────────────────────────────
# 원본 문자열 (Collector에서 파싱)
YOUTUBE_CHANNELS_RAW: str = get_env("YOUTUBE_CHANNELS", default="") or ""

# 제목: 채널별 신뢰도 가중치
# 내용: 기존 4채널 + 신규 4채널 (2026-04-25 추가: 김단테, 윤석종, 노매드크리틱, 헤딩)
CHANNEL_WEIGHTS: dict[str, float] = {
    # ── 기존 채널 ──────────────────────────────────────
    "오선의 미국 증시 라이브": 1.2,  # 실시간성 강점
    "전인구경제연구소": 1.3,         # 매크로 신뢰도
    "소수몽키": 1.0,                 # 균형
    "미주은": 0.9,                   # 종목 중심, Alert 기여 낮음
    # ── 신규 채널 (2026-04-25) ─────────────────────────
    "김단테": 1.2,        # 매크로·투자 인사이트
    "윤석종": 1.1,        # 배당 투자 전문
    "노매드크리틱": 1.0,  # 여행·투자 복합
    "헤딩": 1.1,          # 신규 추가 (채널 ID 마스터 확인 후 반영)
    "판교불패": 0.3,
    "김피비": 0.5,
    "머니코믹스": 1
}


# ────────────────────────────────────────────────────────
# 뉴스 Tier별 소스 정의
# ────────────────────────────────────────────────────────
# 제목: Tier별 뉴스 RSS 소스 레지스트리
# 내용: 3인 전문가 협의 설계서 Round 1 기반
#       - Tier S: 자동 L1 후보 (Fed, 백악관)
#       - Tier A: 키워드 + AI 분석 필수
#       - Tier B: 보조 소스
NEWS_SOURCE_REGISTRY: dict[str, dict[str, dict]] = {
    "S": {
        "fed_rss": {
            "url": "https://www.federalreserve.gov/feeds/press_monetary.xml",  # 통화정책 전용 (A안 적용 2026-04-25)
            "auto_l1": True,
        },
    },
    "A": {
          "reuters_markets": {
              "url": "https://www.cnbc.com/id/10001147/device/rss/rss.html",
              "auto_l1": False,
          },
          "investing_com": {          # reuters_breaking 교체 — Reuters RSS 폐쇄 (2026-05-03)
              "url": "https://www.investing.com/rss/news.rss",
              "auto_l1": False,
          },
      },
    "B": {
        "yahoo_finance": {
            "url": "https://finance.yahoo.com/news/rssindex",
            "auto_l1": False,
        },
    },
}


# ────────────────────────────────────────────────────────
# 점수 가중치
# ────────────────────────────────────────────────────────
# 제목: Tier별 뉴스 점수 가중치
# 내용: 3인 전문가 협의 설계서 Round 3 확정값
TIER_WEIGHTS: dict[str, float] = {
    "S": 1.5,
    "A": 1.2,
    "B": 1.0,
}


# ────────────────────────────────────────────────────────
# 레벨 판정 임계값
# ────────────────────────────────────────────────────────
# 제목: Macro-News Score 기반 L1/L2/L3 판정 임계값
# 내용: 환경변수로 오버라이드 가능 (튜닝 용이)
LEVEL_THRESHOLDS: dict[str, float] = {
    "l1_score": get_env_float("THRESHOLD_L1_SCORE", 7.0),
    "l2_score": get_env_float("THRESHOLD_L2_SCORE", 5.0),
    "l3_score": get_env_float("THRESHOLD_L3_SCORE", 3.0),
    "health_l1": get_env_float("THRESHOLD_HEALTH_L1", 0.90),
    "health_l2": get_env_float("THRESHOLD_HEALTH_L2", 0.80),
    "health_l3": get_env_float("THRESHOLD_HEALTH_L3", 0.70),
}


# ────────────────────────────────────────────────────────
# Collector 기본 파라미터
# ────────────────────────────────────────────────────────
# 제목: 수집기 재시도/타임아웃 기본값
# 내용: BaseCollector 생성자 기본값으로 사용
DEFAULT_TIMEOUT_SEC: int = 15
DEFAULT_MAX_RETRIES: int = 3
DEFAULT_RETRY_DELAY_SEC: float = 1.0


# ────────────────────────────────────────────────────────
# 수집 윈도우
# ────────────────────────────────────────────────────────
# 제목: 이벤트 수집 시간 범위
# 내용: 뉴스는 24시간, 유튜브는 48시간 (Day 3 보고서 기준)
NEWS_WINDOW_HOURS: int = 24
# 제목: YouTube 수집 기준
# 내용: 당일(UTC 00:00) 이후 영상만 수집 — YOUTUBE_WINDOW_HOURS는 레거시로 유지
YOUTUBE_WINDOW_HOURS: int = 48  # 레거시 (youtube_collector에서 당일 기준으로 오버라이드)
YOUTUBE_TODAY_ONLY: bool = True  # True: UTC 당일 0시 이후만 수집
