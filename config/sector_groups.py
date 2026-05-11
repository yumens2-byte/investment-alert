"""
제목: 섹터 그룹 정의
내용: US 섹터 ETF의 defensive/cyclical 그룹 매핑.
      향후 KR/JP 시장 추가 시 같은 패턴으로 확장 가능.

주요 상수:
  - SECTOR_GROUPS_US: defensive/cyclical 그룹 정의
  - POLICY_VERSION_SECTOR: 그룹 정의 정책 버전 (ia_sector_flow_daily.policy_version 적재용)
  - TICKER_TO_GROUP: ticker → group 역방향 매핑
  - ALL_SECTOR_TICKERS: 전체 ticker 정렬 리스트
"""

from __future__ import annotations

VERSION = "1.0.0"

# 정책 버전 — 그룹 정의 변경 시 semver 증가, ia_sector_flow_daily.policy_version에 기록
POLICY_VERSION_SECTOR = "sector-v1.0.0"

# 그룹 정의 (Phase 1)
# investment-os engines/macro_engine.py:_score_sector_rotation()와 동일한 분류
SECTOR_GROUPS_US: dict[str, list[str]] = {
    "defensive": ["XLV", "XLU", "XLP"],   # 헬스케어/유틸리티/필수소비재
    "cyclical":  ["XLI", "XLRE", "XLB"],  # 산업재/리츠/소재
}

# ticker → group 역방향 매핑 (적재 시 자주 사용)
TICKER_TO_GROUP: dict[str, str] = {
    ticker: group
    for group, tickers in SECTOR_GROUPS_US.items()
    for ticker in tickers
}

# 전체 ticker 정렬 리스트 (Collector 순회용)
ALL_SECTOR_TICKERS: list[str] = sorted(TICKER_TO_GROUP.keys())
