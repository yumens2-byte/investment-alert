"""
제목: Yahoo Finance API 기반 섹터 ETF 수집기
내용: requests를 사용해 US 섹터 ETF 6종(XLV/XLU/XLP/XLI/XLRE/XLB)의
      최근 5일 종가를 조회하여 일간 등락률(%) 계산.
      BaseCollector를 상속해 _retry_request로 지수 백오프 재시도를 활용.

주요 클래스:
  - SectorCollector: Yahoo Finance v8/chart API 호출 + 등락률 계산

주요 함수:
  - SectorCollector.collect_sector_changes(): 6 ticker × 최근 5일 등락률 dict 반환
  - SectorCollector._fetch_chart(ticker): 단일 ticker 5일 종가 조회 (retry 포함)
  - SectorCollector._parse_chart(data): 응답 JSON 파싱 + 등락률 산출
"""

from __future__ import annotations

from datetime import UTC, datetime

import requests

from collectors.base import BaseCollector, CollectorEvent
from config.sector_groups import ALL_SECTOR_TICKERS
from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)

# Yahoo Finance 공개 차트 엔드포인트 (안정성 검증된 v8)
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

# Yahoo가 default User-Agent를 차단할 가능성 대비
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


class SectorCollector(BaseCollector):
    """
    제목: Yahoo Finance API 기반 섹터 ETF 수집기
    내용: BaseCollector 상속. _retry_request로 지수 백오프 재시도.

    책임:
      - 6 ticker 각각의 최근 5일 종가 + 등락률 수집
      - 개별 ticker 실패 격리 (나머지는 진행)
      - 응답 None/누락 안전 처리
    """

    def __init__(self, timeout: int = 15, max_retries: int = 2) -> None:
        """
        제목: SectorCollector 초기화

        Args:
            timeout: HTTP 타임아웃 초
            max_retries: 재시도 횟수 (기본 2 = 총 3회 시도)
        """
        super().__init__(
            source_name="sector_etf_yahoo",
            timeout=timeout,
            max_retries=max_retries,
        )

    def collect(self) -> list[CollectorEvent]:
        """
        제목: BaseCollector 추상 메서드 구현 (이벤트 모델 미사용)
        내용: 본 수집기는 sector 데이터를 dict로 반환하므로 collect()는 빈 리스트.
              실제 수집은 collect_sector_changes() 사용.
        """
        return []

    def collect_sector_changes(self) -> dict[str, list[dict]]:
        """
        제목: 6 ticker × 최근 5일 등락률 수집
        내용: 각 ticker별로 _fetch_chart 호출, 실패한 ticker는 빈 리스트.

        Returns:
            {
              "XLV": [{"date": "2026-05-09", "chg_pct": -0.45}, ...],   # 최신순 최대 5건
              "XLU": [...],
              ...
            }
            데이터 없는 ticker는 빈 리스트.
        """
        logger.info(f"[SectorCollector] v{VERSION} 6 ticker 수집 시작")
        result: dict[str, list[dict]] = {}

        for ticker in ALL_SECTOR_TICKERS:
            try:
                rows = self._fetch_chart(ticker)
                result[ticker] = rows
                if rows:
                    logger.info(
                        f"[SectorCollector] {ticker}: {len(rows)}일 데이터 "
                        f"(최신: {rows[0].get('date')} {rows[0].get('chg_pct')}%)"
                    )
                else:
                    logger.warning(f"[SectorCollector] {ticker}: 데이터 0건")
            except Exception as e:
                logger.warning(
                    f"[SectorCollector] {ticker} 수집 실패 (격리): "
                    f"{type(e).__name__}: {e}"
                )
                result[ticker] = []

        success = sum(1 for v in result.values() if v)
        logger.info(
            f"[SectorCollector] 수집 완료: {success}/{len(ALL_SECTOR_TICKERS)} ticker"
        )
        return result

    def _fetch_chart(self, ticker: str) -> list[dict]:
        """
        제목: 단일 ticker 5일 등락률 조회
        내용: Yahoo v8/chart 엔드포인트 호출. BaseCollector._retry_request로 재시도.

        Args:
            ticker: 예 "XLV"

        Returns:
            list[dict]: [{"date": "YYYY-MM-DD", "chg_pct": float}, ...] 최신순 최대 5건
        """
        url = YAHOO_CHART_URL.format(ticker=ticker)
        params = {"interval": "1d", "range": "10d"}  # 영업일 5일 확보 + 여유

        def _do_fetch() -> dict:
            resp = requests.get(
                url,
                params=params,
                headers=DEFAULT_HEADERS,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()

        data = self._retry_request(_do_fetch)
        return self._parse_chart(data)

    @staticmethod
    def _parse_chart(data: dict) -> list[dict]:
        """
        제목: Yahoo chart 응답 파싱 + 등락률 계산
        내용: timestamps + closes 추출 → 인접 종가로 등락률 계산.

        응답 구조:
          chart.result[0].timestamp: [unix_ts, ...]
          chart.result[0].indicators.quote[0].close: [float|None, ...]

        Args:
            data: Yahoo API 응답 dict

        Returns:
            list[dict]: 최신순 최대 5건의 {date, chg_pct}.
                       파싱 실패/부족 시 빈 리스트.
        """
        try:
            result = data["chart"]["result"][0]
            timestamps = result.get("timestamp") or []
            closes_raw = result["indicators"]["quote"][0].get("close") or []
        except (KeyError, IndexError, TypeError) as e:
            logger.debug(f"[SectorCollector] chart 응답 파싱 실패: {e}")
            return []

        # None 제외 + ts/close 짝짓기
        pairs: list[tuple[int, float]] = []
        for ts, c in zip(timestamps, closes_raw, strict=False):
            if ts is None or c is None:
                continue
            try:
                pairs.append((int(ts), float(c)))
            except (TypeError, ValueError):
                continue

        if len(pairs) < 2:
            return []

        # 시간 오름차순 정렬 후 인접 종가로 등락률 계산
        pairs.sort(key=lambda p: p[0])

        rows: list[dict] = []
        for i in range(1, len(pairs)):
            prev_close = pairs[i - 1][1]
            curr_close = pairs[i][1]
            if prev_close == 0:
                continue
            chg = (curr_close - prev_close) / prev_close * 100
            date_str = datetime.fromtimestamp(pairs[i][0], tz=UTC).strftime("%Y-%m-%d")
            rows.append({"date": date_str, "chg_pct": round(chg, 4)})

        # 최신순 정렬 + 최대 5건
        rows.sort(key=lambda r: str(r["date"]), reverse=True)
        return rows[:5]
