"""
제목: EDT 심리지표 수집기 (v1.2.0)
내용: 5개 심리지표의 원천 데이터를 4개 소스에서 수집한다.
      sector_collector v1.1.0 패턴 차용 — requests 1순위 + yfinance 2순위 fallback,
      개별 소스 실패 격리 (예외 raise 없이 None 반환, 파이프라인 중단 없음).

변경 이력:
  v1.0.0 초기 설계
  v1.1.0 recency 가드 신설 (2026-08-27 dry_run stale 결함 수정)
  v1.2.0 PCR 소스 CBOE CSV → Alpha Vantage SPY PCR API 교체
         (CBOE equitypc.csv/archive 모두 2019년 이전에서 끊김 — 구조적 수집 불가 확인)
         CBOE 관련 헬퍼 제거, ALPHAVANTAGE_API_KEY 사용

수집 항목:
  1. VIX / VIX3M 종가          — Yahoo Finance v8 chart
  2. RSP/SPY 20영업일 상대수익률 — Yahoo Finance v8 chart (종가 시계열)
  3. SPY 풀체인 PCR             — Alpha Vantage REALTIME/HISTORICAL_PUT_CALL_RATIO
  4. HY OAS (bp)                — FRED API (BAMLH0A0HYM2, %→bp 환산)
  5. Crypto Fear & Greed        — alternative.me

주요 클래스:
  - SentimentCollector: collect_all() 오케스트레이션

반환 규약:
  - 각 지표는 {"value": float, "date": "YYYY-MM-DD"} 또는 None(수집 실패)
  - collect_all()은 지표 키 전체를 항상 포함 (실패 키는 None)
"""

from __future__ import annotations

import logging
import os
import signal
from datetime import UTC, datetime

import requests

from config.sentiment_settings import (
    ALPHA_VANTAGE_PCR_SYMBOL,
    ALTERNATIVE_FNG_URL,
    BREADTH_CHART_RANGE,
    BREADTH_LOOKBACK_DAYS,
    FRED_HY_OAS_SERIES,
    FRED_OBS_URL,
    MAX_AGE_DAYS,
    YAHOO_CHART_URL,
)
from core.logger import get_logger

VERSION = "1.2.0"

logger = get_logger(__name__)

# sector_collector와 동일 — yfinance 라이브러리 로그 noise 차단
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)

# sector_collector v1.1.0과 동일한 User-Agent (Windows Chrome 120)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_REQUESTS_TIMEOUT_SEC = 10
_YFINANCE_TIMEOUT_SEC = 15


class _TimeoutError(Exception):  # noqa: N818 — sector_collector 동일 패턴
    """제목: yfinance 호출 timeout 전용 예외"""


def _timeout_handler(signum, frame) -> None:  # noqa: ARG001
    """제목: SIGALRM 핸들러"""
    raise _TimeoutError("yfinance timeout")


def _run_with_timeout(fn, timeout_sec: int):
    """
    제목: signal.alarm 기반 timeout 실행 (sector_collector 동일 패턴)
    내용: yfinance 무한 대기 차단. 실패/timeout 시 예외 전파.
    """
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout_sec)
    try:
        return fn()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


class SentimentCollector:
    """
    제목: EDT 심리지표 수집기
    내용: 4개 외부 소스에서 5개 지표 원값을 수집한다.

    책임:
      - 소스별 독립 실패 격리 (한 소스 실패가 다른 소스 수집을 막지 않음)
      - 지표별 기준일(date) 분리 반환 (FRED T+1 지연 대응 — 추측값 금지)
    """

    def __init__(
        self,
        fred_api_key: str | None = None,
        alphavantage_api_key: str | None = None,
    ) -> None:
        """
        제목: SentimentCollector 초기화

        Args:
            fred_api_key: None이면 환경변수 FRED_API_KEY 사용
            alphavantage_api_key: None이면 환경변수 ALPHAVANTAGE_API_KEY 사용
        """
        self._fred_api_key = fred_api_key or os.getenv("FRED_API_KEY", "")
        self._alphavantage_api_key = alphavantage_api_key or os.getenv(
            "ALPHAVANTAGE_API_KEY", ""
        )
        logger.info(f"[SentimentCollector] v{VERSION} 초기화")

    # ────────────────────────────────────────────────
    # 공개 API
    # ────────────────────────────────────────────────
    def collect_all(self, today: str | None = None) -> dict[str, dict | None]:
        """
        제목: 5개 지표 전체 수집 (v1.1.0 — recency 가드 포함)
        내용: 소스별 실패 격리. 실패 지표는 None.
              수집 후 지표별 기준일 검사 — stale/기준일 미존재 시 결측 전환.

        Args:
            today: 기준일 YYYY-MM-DD (None이면 UTC 오늘 — 테스트 주입용)

        Returns:
            dict: {
              "vix_ratio": {"value": float, "date": str, "vix": float, "vix3m": float} | None,
              "pcr":       {"value": float, "date": str} | None,
              "hy_oas":    {"value": float(bp), "date": str} | None,
              "breadth":   {"value": float(%p), "date": str} | None,
              "crypto_fg": {"value": float, "date": str} | None,
            }
        """
        logger.info(f"[SentimentCollector] v{VERSION} 전체 수집 시작")
        today_date = self._resolve_today(today)
        result: dict[str, dict | None] = {
            "vix_ratio": self._safe(self.collect_vix_ratio, "vix_ratio"),
            "pcr": self._safe(self.collect_pcr, "pcr"),
            "hy_oas": self._safe(self.collect_hy_oas, "hy_oas"),
            "breadth": self._safe(self.collect_breadth, "breadth"),
            "crypto_fg": self._safe(self.collect_crypto_fg, "crypto_fg"),
        }
        # v1.1.0: recency 가드 — stale 데이터 결측 전환 (default-deny)
        for name in list(result.keys()):
            result[name] = self._apply_recency_guard(name, result[name], today_date)
        ok = sum(1 for v in result.values() if v is not None)
        logger.info(f"[SentimentCollector] 수집 완료: {ok}/5 지표 (recency 가드 적용)")
        return result

    # ────────────────────────────────────────────────
    # 지표별 수집
    # ────────────────────────────────────────────────
    def collect_vix_ratio(self) -> dict | None:
        """
        제목: VIX/VIX3M 기간구조 비율 수집
        내용: 두 심볼의 최신 종가를 수집해 비율 계산. 하나라도 실패 시 None.
        """
        vix = self._fetch_latest_close("^VIX")
        vix3m = self._fetch_latest_close("^VIX3M")
        if not vix or not vix3m or vix3m["close"] <= 0:
            logger.warning("[SentimentCollector] vix_ratio: VIX/VIX3M 수집 실패")
            return None
        ratio = round(vix["close"] / vix3m["close"], 4)
        return {
            "value": ratio,
            "date": vix["date"],
            "vix": vix["close"],
            "vix3m": vix3m["close"],
        }

    def collect_breadth(self) -> dict | None:
        """
        제목: 시장폭 프록시 수집 — RSP-SPY 20영업일 상대수익률(%p)
        내용: 동일가중(RSP) vs 시총가중(SPY) 상대강도. 양수 = 시장폭 건강.
        """
        rsp = self._fetch_close_series("RSP", BREADTH_CHART_RANGE)
        spy = self._fetch_close_series("SPY", BREADTH_CHART_RANGE)
        need = BREADTH_LOOKBACK_DAYS + 1
        if len(rsp) < need or len(spy) < need:
            logger.warning(
                f"[SentimentCollector] breadth: 시계열 부족 "
                f"(RSP={len(rsp)}, SPY={len(spy)}, 필요={need})"
            )
            return None
        rsp_ret = (rsp[-1]["close"] / rsp[-need]["close"] - 1.0) * 100.0
        spy_ret = (spy[-1]["close"] / spy[-need]["close"] - 1.0) * 100.0
        return {
            "value": round(rsp_ret - spy_ret, 4),
            "date": rsp[-1]["date"],
        }

    def collect_pcr(self) -> dict | None:
        """
        제목: SPY 풀체인 Put/Call Ratio 수집 (v1.2.0 — Alpha Vantage 교체)
        내용: REALTIME_PUT_CALL_RATIO(SPY)로 당일 값 수집.
              실패 시 HISTORICAL_PUT_CALL_RATIO(SPY, date=오늘)로 fallback.
              반환 값: put_call_ratio_full_chain (전체 옵션체인 비율).
              기준일: 실시간 조회는 UTC 오늘, historical은 API 응답 date 필드 사용.
        """
        if not self._alphavantage_api_key:
            logger.warning("[SentimentCollector] pcr: ALPHAVANTAGE_API_KEY 미설정")
            return None
        # 1순위: realtime
        result = self._fetch_av_pcr_realtime()
        if result is not None:
            return result
        # 2순위: historical fallback
        logger.info("[SentimentCollector] pcr: realtime 실패 → historical fallback")
        return self._fetch_av_pcr_historical()

    def collect_hy_oas(self) -> dict | None:
        """
        제목: HY OAS 수집 (FRED BAMLH0A0HYM2)
        내용: FRED 응답은 % 단위 → bp 환산(×100). 최신 유효 관측치 1건.
              FRED는 T+1 지연 — date 필드로 기준일 분리 기록 (추측값 금지).
        """
        if not self._fred_api_key:
            logger.warning("[SentimentCollector] hy_oas: FRED_API_KEY 미설정")
            return None
        try:
            resp = requests.get(
                FRED_OBS_URL,
                params={
                    "series_id": FRED_HY_OAS_SERIES,
                    "api_key": self._fred_api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": "10",
                },
                timeout=_REQUESTS_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            observations = resp.json().get("observations", [])
            for obs in observations:
                raw = str(obs.get("value", "")).strip()
                if raw in ("", "."):
                    continue  # FRED 결측 표기
                pct = float(raw)
                return {"value": round(pct * 100.0, 2), "date": str(obs.get("date", ""))}
            logger.warning("[SentimentCollector] hy_oas: 유효 관측치 없음")
            return None
        except Exception as e:
            logger.warning(f"[SentimentCollector] hy_oas 실패: {type(e).__name__}: {e}")
            return None

    def collect_crypto_fg(self) -> dict | None:
        """
        제목: Crypto Fear & Greed 수집 (alternative.me)
        내용: 최신 1건. value는 0~100 정수 문자열.
        """
        try:
            resp = requests.get(
                ALTERNATIVE_FNG_URL,
                params={"limit": "1"},
                headers=DEFAULT_HEADERS,
                timeout=_REQUESTS_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            rows = resp.json().get("data", [])
            if not rows:
                return None
            value = float(rows[0]["value"])
            ts = int(rows[0].get("timestamp", 0))
            date = datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d") if ts else ""
            return {"value": value, "date": date}
        except Exception as e:
            logger.warning(f"[SentimentCollector] crypto_fg 실패: {type(e).__name__}: {e}")
            return None

    # ────────────────────────────────────────────────
    # 내부 헬퍼 — Yahoo
    # ────────────────────────────────────────────────
    def _fetch_latest_close(self, ticker: str) -> dict | None:
        """제목: 단일 심볼 최신 종가 (requests 1순위 → yfinance 2순위)"""
        series = self._fetch_close_series(ticker, "10d")
        return series[-1] if series else None

    def _fetch_close_series(self, ticker: str, chart_range: str) -> list[dict]:
        """
        제목: 종가 시계열 수집 (오름차순)
        내용: requests 1순위 → 실패 시 yfinance fallback (timeout 적용).

        Returns:
            list[dict]: [{"date": "YYYY-MM-DD", "close": float}, ...] 오름차순.
                        실패 시 빈 리스트.
        """
        rows = self._fetch_series_via_requests(ticker, chart_range)
        if rows:
            return rows
        logger.info(f"[SentimentCollector] yfinance fallback 시도: {ticker}")
        return self._fetch_series_via_yfinance(ticker, chart_range)

    def _fetch_series_via_requests(self, ticker: str, chart_range: str) -> list[dict]:
        """제목: Yahoo v8 chart requests 직접 호출 (1순위) — 실패 시 빈 리스트"""
        try:
            resp = requests.get(
                YAHOO_CHART_URL.format(ticker=ticker),
                headers=DEFAULT_HEADERS,
                params={"interval": "1d", "range": chart_range},
                timeout=_REQUESTS_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            chart = resp.json().get("chart", {}).get("result", [])
            if not chart:
                return []
            node = chart[0]
            timestamps = node.get("timestamp", []) or []
            closes = (
                node.get("indicators", {}).get("quote", [{}])[0].get("close", []) or []
            )
            rows: list[dict] = []
            for ts, close in zip(timestamps, closes, strict=False):
                if close is None:
                    continue
                rows.append(
                    {
                        "date": datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d"),
                        "close": float(close),
                    }
                )
            return rows
        except Exception as e:
            logger.warning(
                f"[SentimentCollector] {ticker} requests 실패: {type(e).__name__}: {e}"
            )
            return []

    def _fetch_series_via_yfinance(self, ticker: str, chart_range: str) -> list[dict]:
        """제목: yfinance fallback (2순위, signal timeout) — 실패 시 빈 리스트"""
        try:
            import yfinance as yf  # 지연 import — fallback 경로에서만 로드

            def _call() -> list[dict]:
                hist = yf.Ticker(ticker).history(period=chart_range, interval="1d")
                rows: list[dict] = []
                for idx, row in hist.iterrows():
                    close = row.get("Close")
                    if close is None:
                        continue
                    rows.append(
                        {"date": idx.strftime("%Y-%m-%d"), "close": float(close)}
                    )
                return rows

            return _run_with_timeout(_call, _YFINANCE_TIMEOUT_SEC)
        except Exception as e:
            logger.warning(
                f"[SentimentCollector] {ticker} yfinance 실패: {type(e).__name__}: {e}"
            )
            return []

    # ────────────────────────────────────────────────
    # 내부 헬퍼 — Alpha Vantage PCR (v1.2.0)
    # ────────────────────────────────────────────────
    def _fetch_av_pcr_realtime(self) -> dict | None:
        """
        제목: Alpha Vantage REALTIME_PUT_CALL_RATIO 호출
        내용: put_call_ratio_full_chain 필드 사용. 기준일 = UTC 오늘.
        """
        try:
            resp = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "REALTIME_PUT_CALL_RATIO",
                    "symbol": ALPHA_VANTAGE_PCR_SYMBOL,
                    "apikey": self._alphavantage_api_key,
                },
                headers=DEFAULT_HEADERS,
                timeout=_REQUESTS_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            data = resp.json()
            ratio_str = str(data.get("put_call_ratio_full_chain", "")).strip()
            if not ratio_str:
                logger.warning("[SentimentCollector] pcr realtime: put_call_ratio_full_chain 없음")
                return None
            today_str = datetime.now(UTC).strftime("%Y-%m-%d")
            return {"value": round(float(ratio_str), 4), "date": today_str}
        except Exception as e:
            logger.warning(
                f"[SentimentCollector] pcr realtime 실패: {type(e).__name__}: {e}"
            )
            return None

    def _fetch_av_pcr_historical(self) -> dict | None:
        """
        제목: Alpha Vantage HISTORICAL_PUT_CALL_RATIO fallback
        내용: date=오늘 기준 조회. API 응답의 date 필드를 기준일로 사용.
        """
        try:
            today_str = datetime.now(UTC).strftime("%Y-%m-%d")
            resp = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "HISTORICAL_PUT_CALL_RATIO",
                    "symbol": ALPHA_VANTAGE_PCR_SYMBOL,
                    "date": today_str,
                    "apikey": self._alphavantage_api_key,
                },
                headers=DEFAULT_HEADERS,
                timeout=_REQUESTS_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            data = resp.json()
            ratio_str = str(data.get("put_call_ratio_full_chain", "")).strip()
            date_str = str(data.get("date", today_str)).strip() or today_str
            if not ratio_str:
                logger.warning("[SentimentCollector] pcr historical: put_call_ratio_full_chain 없음")
                return None
            return {"value": round(float(ratio_str), 4), "date": date_str}
        except Exception as e:
            logger.warning(
                f"[SentimentCollector] pcr historical 실패: {type(e).__name__}: {e}"
            )
            return None

    # ────────────────────────────────────────────────
    # 내부 헬퍼 — recency 가드 (v1.1.0)
    # ────────────────────────────────────────────────
    @staticmethod
    def _resolve_today(today: str | None):
        """제목: 기준일 결정 (None이면 UTC 오늘) — date 객체 반환"""
        if today:
            try:
                return datetime.strptime(today, "%Y-%m-%d").date()
            except ValueError:
                logger.warning(
                    f"[SentimentCollector] today 파싱 실패({today}) → UTC 오늘 사용"
                )
        return datetime.now(UTC).date()

    @staticmethod
    def _apply_recency_guard(name: str, item: dict | None, today_date) -> dict | None:
        """
        제목: 지표 기준일 신선도 검사 (default-deny)
        내용: 아래 조건이면 결측(None) 전환 + WARNING:
              - 기준일(date) 미존재/파싱 불가 (검증 불가 데이터는 채택하지 않음)
              - 기준일이 today 대비 MAX_AGE_DAYS[name] 초과 과거
              - 기준일이 today보다 미래 (데이터 이상)
        """
        if item is None:
            return None
        max_age = MAX_AGE_DAYS.get(name)
        if max_age is None:  # 방어 — 미정의 지표는 가드 없이 통과
            return item
        raw_date = str(item.get("date") or "").strip()
        if not raw_date:
            logger.warning(
                f"[SentimentCollector] recency 가드: {name} 기준일 없음 → 결측 처리"
            )
            return None
        try:
            item_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(
                f"[SentimentCollector] recency 가드: {name} 기준일 파싱 불가"
                f"({raw_date}) → 결측 처리"
            )
            return None
        age_days = (today_date - item_date).days
        if age_days < 0:
            logger.warning(
                f"[SentimentCollector] recency 가드: {name} 기준일 미래"
                f"({raw_date}, today={today_date}) → 결측 처리"
            )
            return None
        if age_days > max_age:
            logger.warning(
                f"[SentimentCollector] recency 가드: {name} stale "
                f"({raw_date}, {age_days}일 경과 > 허용 {max_age}일) → 결측 처리"
            )
            return None
        return item

    # ────────────────────────────────────────────────
    # 내부 헬퍼 — 공통
    # ────────────────────────────────────────────────
    @staticmethod
    def _safe(fn, name: str) -> dict | None:
        """제목: 지표 수집 예외 격리 래퍼"""
        try:
            return fn()
        except Exception as e:
            logger.warning(f"[SentimentCollector] {name} 예외 격리: {type(e).__name__}: {e}")
            return None
