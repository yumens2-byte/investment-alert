"""
제목: EDT 심리지표 설정 상수
내용: EDT Sentiment Scoreboard 파이프라인의 임계값/가중치/소스 URL 정의.
      모든 수치는 상수화하여 하드코딩 없이 운영 중 조정 가능하게 한다.

구성:
  - 지표별 밴드 브레이크포인트 (선형보간용 (원값, 점수) 쌍)
  - 지표 가중치 (합계 1.0)
  - EDT D축 (Fast/Slow Fear) 내부 가중치
  - EDT T축 임계값 및 룩백 일수
  - 종합 레벨 경계
  - 데이터 소스 URL

점수 방향: 0 = 극단공포, 100 = 극단탐욕.
"""

from __future__ import annotations

VERSION = "1.1.0"

# ────────────────────────────────────────────────────────
# 지표별 밴드 브레이크포인트
# (원값, 점수) 쌍 리스트 — 원값 오름차순, 구간 내 선형보간, 범위 밖 clamp.
# ────────────────────────────────────────────────────────

# VIX/VIX3M 비율 — 값이 클수록 공포 (≥1.0 백워데이션 = 패닉)
BAND_VIX_RATIO: list[tuple[float, float]] = [
    (0.75, 100.0),
    (0.82, 75.0),
    (0.88, 55.0),
    (0.93, 45.0),
    (1.00, 25.0),
    (1.10, 0.0),
]

# CBOE Equity Put/Call Ratio — 값이 클수록 공포 (풋 수요 증가)
BAND_PCR: list[tuple[float, float]] = [
    (0.40, 100.0),
    (0.50, 75.0),
    (0.62, 55.0),
    (0.78, 45.0),
    (0.95, 25.0),
    (1.20, 0.0),
]

# ICE BofA US High Yield OAS (bp) — 값이 클수록 공포 (신용 스트레스)
BAND_HY_OAS: list[tuple[float, float]] = [
    (250.0, 100.0),
    (280.0, 75.0),
    (330.0, 55.0),
    (400.0, 45.0),
    (500.0, 25.0),
    (700.0, 0.0),
]

# Breadth: RSP-SPY 20영업일 상대수익률 (%p) — 값이 클수록 탐욕 (시장폭 건강)
BAND_BREADTH: list[tuple[float, float]] = [
    (-6.0, 0.0),
    (-3.0, 25.0),
    (-1.0, 45.0),
    (1.0, 55.0),
    (3.0, 75.0),
    (6.0, 100.0),
]

# ────────────────────────────────────────────────────────
# 지표 가중치 (합계 1.0)
# ────────────────────────────────────────────────────────
WEIGHTS: dict[str, float] = {
    "vix_ratio": 0.30,
    "pcr": 0.20,
    "hy_oas": 0.20,
    "breadth": 0.20,
    "crypto_fg": 0.10,
}

# 결측 허용 한계 — 이 수 이상 결측 시 SYSTEM_DEGRADED (발행 스킵)
MAX_MISSING_INDICATORS = 2

# ────────────────────────────────────────────────────────
# EDT 3축 파라미터
# ────────────────────────────────────────────────────────
# D축: Fast Fear(파생시장) / Slow Fear(신용·실체) 내부 가중치
D_FAST_WEIGHTS: dict[str, float] = {"vix_ratio": 0.6, "pcr": 0.4}
D_SLOW_WEIGHTS: dict[str, float] = {"hy_oas": 0.7, "breadth": 0.3}

# D축 괴리 판정 임계 (점수 차이)
D_DIVERGENCE_THRESHOLD = 20.0

# T축: E 점수 룩백 영업일 수 및 방향 판정 임계 (점수 차이)
T_LOOKBACK_DAYS = 5
T_TREND_THRESHOLD = 8.0

# ────────────────────────────────────────────────────────
# 종합 레벨 경계 (E 점수 기준, 하한 포함)
# ────────────────────────────────────────────────────────
LEVEL_BOUNDS: list[tuple[float, str]] = [
    (75.0, "EXTREME_GREED"),
    (55.0, "GREED"),
    (45.0, "NEUTRAL"),
    (25.0, "FEAR"),
    (0.0, "EXTREME_FEAR"),
]

LEVEL_LABEL_KR: dict[str, str] = {
    "EXTREME_GREED": "극단탐욕",
    "GREED": "탐욕",
    "NEUTRAL": "중립",
    "FEAR": "공포",
    "EXTREME_FEAR": "극단공포",
}

LEVEL_EMOJI: dict[str, str] = {
    "EXTREME_GREED": "🔥",
    "GREED": "🟢",
    "NEUTRAL": "⚪",
    "FEAR": "🟠",
    "EXTREME_FEAR": "🔴",
}

# ────────────────────────────────────────────────────────
# 데이터 소스
# ────────────────────────────────────────────────────────
# Yahoo Finance v8 chart (sector_collector와 동일 엔드포인트)
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

# CBOE Equity PCR — v1.1.0: 실측 검증된 current 파일(equitypc.csv)을 1순위로 교체.
# 사유(2026-08-27 dry_run 결함): archive 파일은 2012-06-07에서 끝나는 과거 아카이브로
# 확인됨 — "마지막 유효 행" 파서가 14년 전 값을 최신값으로 오인 수집.
# 실측 포맷(2026-08 검증): 헤더 "DATE,CALL,PUT,TOTAL,P/C Ratio",
# 데이터 "MM/DD/YYYY,call,put,total,ratio" (일부 구간 콤마 뒤 공백 존재 — strip 필수)
CBOE_PCR_URLS: list[str] = [
    "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypc.csv",
    "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypcarchive.csv",
]

# ────────────────────────────────────────────────────────
# Recency 가드 (v1.1.0 신설 — 2026-08-27 dry_run 결함 재발 방지)
# 지표 기준일이 오늘 대비 아래 캘린더일을 초과 과거이거나 기준일이 없으면
# 결측(None) 처리 — stale 데이터의 점수 반영 차단 (default-deny)
# 여유일수: 주말(2일) + 연휴/T+1 지연 고려
# ────────────────────────────────────────────────────────
MAX_AGE_DAYS: dict[str, int] = {
    "vix_ratio": 5,
    "pcr": 7,
    "hy_oas": 7,
    "breadth": 5,
    "crypto_fg": 3,
}

# FRED — ICE BofA US High Yield Index Option-Adjusted Spread (단위: %, bp 환산 필요)
FRED_HY_OAS_SERIES = "BAMLH0A0HYM2"
FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"

# alternative.me Crypto Fear & Greed
ALTERNATIVE_FNG_URL = "https://api.alternative.me/fng/"

# Breadth 계산 파라미터
BREADTH_LOOKBACK_DAYS = 20  # 영업일 기준 상대수익률 룩백
BREADTH_CHART_RANGE = "3mo"  # 20영업일 + 여유 확보용 차트 범위
