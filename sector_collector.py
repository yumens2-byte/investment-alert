"""
제목: Yahoo Finance 기반 섹터 ETF 수집기 (v1.1.0 — 429 대응)
내용: investment-os/engines/yahoo_finance.py v1.9.0 패턴 차용.
      GitHub Actions IP에서 검증된 "requests 1순위 + yfinance 2순위 fallback" 구조.

변경 사유 (v1.0.0 → v1.1.0):
  - v1.0.0은 requests만 + _retry_request로 동일 URL 3회 재시도 → 429 차단 100% 재현
  - investment-os v1.9.0은 requests 1회 실패 시 yfinance fallback → GitHub Actions에서 검증됨
  - User-Agent를 Mac → Windows Chrome 120 (investment-os와 동일)
  - signal.alarm 기반 yfinance timeout (무한 대기 차단)
  - yfinance 라이브러리 로그 noise 차단

주요 클래스:
  - SectorCollector: Yahoo Finance 호출 + 등락률 5일 시계열 추출

주요 함수:
  - SectorCollector.collect_sector_changes(): 6 ticker × 5일 등락률 dict 반환
  - SectorCollector._fetch_chart(ticker): requests 1순위 → yfinance 2순위
  - SectorCollector._fetch_via_requests(ticker): requests 직접 호출
  - SectorCollector._fetch_via_yfinance(ticker): yfinance fallback (timeout 적용)
"""

from __future__ import annotations

import logging
import signal
from datetime import UTC, datetime

import requests

from collectors.base import BaseCollector, CollectorEvent
from config.sector_groups import ALL_SECTOR_TICKERS
from core.logger import get_logger

VERSION = "1.1.0"

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────
# investment-os v1.7.0 패턴: yfinance 라이브러리 noise 차단
# GitHub Actions에서 ticker당 2줄 ERROR 로그 도배 방지
# ─────────────────────────────────────────────────────────────────
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)

# Yahoo Finance v8 chart endpoint (investment-os와 동일)
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

# investment-os v1.9.0와 동일한 User-Agent (Windows Chrome 120)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Timeout — investment-os 패턴 그대로
_REQUESTS_TIMEOUT_SEC = 10  # requests 1순위
_YFINANCE_TIMEOUT_SEC = 15  # yfinance fallback (signal.alarm)


class _TimeoutError(Exception):  # noqa: N818
    """yfinance signal.alarm 타임아웃 전용 예외 (investment-os 패턴)"""


def _timeout_handler(signum, frame) -> None:  # noqa: ARG001
    """signal.alarm 핸들러"""
    raise _TimeoutError("yfinance fetch timeout")


def _run_with_timeout(fn, timeout_sec: int):
    """
    제목: signal.alarm 기반 yfinance 타임아웃 (investment-os v1.5.2 패턴)
    내용: yfinance가 내부적으로 무한 대기에 빠지는 케이스 차단.
          Windows 등 SIGALRM 미지원 환경에서는 타임아웃 없이 실행.

    Args:
        fn: 실행할 함수 (인자 없음)
        timeout_sec: 최대 대기 초

    Returns:
        fn 실행 결과, timeout 시 None
    """
    try:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_sec)
        try:
            return fn()
        finally:
            signal.alarm(0)
    except AttributeError:
        # Windows 등 SIGALRM 미지원
        return fn()
    except _TimeoutError:
        logger.warning(f"[SectorCollector] {timeout_sec}초 yfinance timeout")
        return None


class SectorCollector(BaseCollector):
    """
    제목: Yahoo Finance 기반 섹터 ETF 수집기 (429 대응 v1.1.0)
    내용: investment-os v1.9.0 패턴 차용 — requests 1순위 + yfinance 2순위.
          BaseCollector를 상속하지만 _retry_request는 사용하지 않음
          (재시도 대신 다른 source로 fallback이 더 효과적).

    책임:
      - 6 ticker 각각의 최근 5일 등락률 시계열 추출
      - requests 1차 시도 → 실패 시 yfinance fallback
      - 개별 ticker 실패 격리 (예외 raise 없이 빈 리스트 반환)
    """

    def __init__(self) -> None:
        """제목: SectorCollector 초기화"""
        super().__init__(
            source_name="sector_etf",
            timeout=_REQUESTS_TIMEOUT_SEC,
            max_retries=0,  # investment-os 패턴: retry 대신 source fallback
        )

    def collect(self) -> list[CollectorEvent]:
        """제목: BaseCollector 추상 메서드 구현 (이벤트 모델 미사용)"""
        return []

    def collect_sector_changes(self) -> dict[str, list[dict]]:
        """
        제목: 6 ticker × 최근 5일 등락률 수집
        내용: requests 1순위 → yfinance 2순위 fallback. 개별 ticker 실패 격리.

        Returns:
            dict: {ticker: [{"date": "YYYY-MM-DD", "chg_pct": float}, ...], ...}
                 데이터 없는 ticker는 빈 리스트.
        """
        logger.info(
            f"[SectorCollector] v{VERSION} 6 ticker 수집 시작 "
            f"(requests 1순위 + yfinance 2순위)"
        )
        result: dict[str, list[dict]] = {}

        for ticker in ALL_SECTOR_TICKERS:
            try:
                rows = self._fetch_chart(ticker)
                result[ticker] = rows
                if rows:
                    logger.info(
                        f"[SectorCollector] {ticker}: {len(rows)}일 데이터 "
                        f"(최신 {rows[0].get('date')} {rows[0].get('chg_pct')}%)"
                    )
                else:
                    logger.warning(
                        f"[SectorCollector] {ticker}: 0건 — requests+yfinance 모두 실패"
                    )
            except Exception as e:
                logger.warning(
                    f"[SectorCollector] {ticker} 예외 격리: {type(e).__name__}: {e}"
                )
                result[ticker] = []

        success = sum(1 for v in result.values() if v)
        logger.info(
            f"[SectorCollector] 수집 완료: {success}/{len(ALL_SECTOR_TICKERS)} ticker"
        )
        return result

    def _fetch_chart(self, ticker: str) -> list[dict]:
        """
        제목: requests 1순위 → yfinance 2순위 (investment-os v1.9.0 패턴)
        내용: requests가 빈 리스트 반환 시 yfinance fallback.

        Args:
            ticker: 예 "XLV"

        Returns:
            list[dict]: 5일 등락률 시계열 (최신순), 둘 다 실패 시 빈 리스트
        """
        # 1순위: requests (GitHub Actions에서 검증된 안정적 경로)
        rows = self._fetch_via_requests(ticker)
        if rows:
            return rows

        # 2순위: yfinance fallback
        logger.info(f"[SectorCollector] yfinance fallback 시도: {ticker}")
        return self._fetch_via_yfinance(ticker)

    def _fetch_via_requests(self, ticker: str) -> list[dict]:
        """
        제목: requests 직접 호출 (1순위)
        내용: investment-os _fetch_with_requests 패턴 — Windows UA + timeout.
              실패 시 raise 없이 빈 리스트.
        """
        try:
            url = YAHOO_CHART_URL.format(ticker=ticker)
            params = {"interval": "1d", "range": "10d"}  # 영업일 5일 + 여유
            resp = requests.get(
                url,
                headers=DEFAULT_HEADERS,
                params=params,
                timeout=_REQUESTS_TIMEOUT_SEC,
            )
            if resp.status_code != 200:
                logger.warning(
                    f"[SectorCollector] requests HTTP {resp.status_code}: {ticker}"
                )
                return []
            return self._parse_chart(resp.json())
        except Exception as e:
            logger.warning(
                f"[SectorCollector] requests 실패 {ticker}: {type(e).__name__}: {e}"
            )
            return []

    def _fetch_via_yfinance(self, ticker: str) -> list[dict]:
        """
        제목: yfinance fallback (2순위) — signal.alarm timeout 적용
        내용: yfinance Ticker.history()로 시계열 추출. internal API endpoint를
              사용해 v8/chart 차단된 IP에서도 동작할 가능성 있음.
        """
        def _do() -> list[dict]:
            try:
                import yfinance as yf  # type: ignore[import]
                t = yf.Ticker(ticker)
                hist = t.history(period="10d")
                if hist is None or hist.empty or len(hist) < 2:
                    return []

                closes = hist["Close"].tolist()
                # hist.index는 pandas Timestamp — strftime 가능
                dates = [d.strftime("%Y-%m-%d") for d in hist.index]

                rows: list[dict] = []
                for i in range(1, len(closes)):
                    prev_c = closes[i - 1]
                    curr_c = closes[i]
                    if prev_c is None or curr_c is None or prev_c == 0:
                        continue
                    chg = (curr_c - prev_c) / prev_c * 100
                    rows.append({"date": dates[i], "chg_pct": round(chg, 4)})

                # 최신순 정렬 + 최대 5건
                rows.sort(key=lambda r: str(r["date"]), reverse=True)
                return rows[:5]
            except Exception as e:
                logger.warning(
                    f"[SectorCollector] yfinance 실패 {ticker}: "
                    f"{type(e).__name__}: {e}"
                )
                return []

        result = _run_with_timeout(_do, _YFINANCE_TIMEOUT_SEC)
        return result if result is not None else []

    @staticmethod
    def _parse_chart(data: dict) -> list[dict]:
        """
        제목: Yahoo v8/chart 응답 파싱 + 등락률 계산
        내용: timestamps + closes 추출 → 인접 종가로 등락률 계산.

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

        # 시간 오름차순 정렬 후 인접 종가로 등락률
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
