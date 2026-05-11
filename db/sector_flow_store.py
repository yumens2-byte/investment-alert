"""
제목: ia_sector_flow_daily 테이블 데이터 접근 객체 (v1.1.0 — 재시도 추가)
내용: 섹터 ETF 일간 등락률을 적재(upsert)/조회한다.
      AlertStore와 동일한 lazy init + URL rstrip 패턴.

변경 사유 (v1.0.0 → v1.1.0):
  - Supabase 일시 장애 대비 — upsert/fetch에 1회 재시도 추가
  - upsert 실패 시 메모리 데이터 영구 소실 위험이 가장 큼 → 재시도 보호 우선순위 ↑
  - 재시도 간격은 3초 (timeout-minutes=10 내 안전)

주요 클래스:
  - SectorFlowStore: ia_sector_flow_daily Supabase 클라이언트

주요 함수:
  - SectorFlowStore.upsert_daily_rows(snapshot_date, market, ticker_chg_map): 6 row 일괄 upsert
  - SectorFlowStore.fetch_latest_n_days(n, market): 최근 N일치 row 조회
"""

from __future__ import annotations

import os
import time

from config.sector_groups import POLICY_VERSION_SECTOR, TICKER_TO_GROUP
from core.logger import get_logger

VERSION = "1.1.0"

logger = get_logger(__name__)

TABLE_SECTOR_FLOW = "ia_sector_flow_daily"

# v1.1.0: Supabase 재시도 설정
# 1회 재시도, 3초 대기 — timeout-minutes=10 내 안전 (최대 +3초 지연)
_RETRY_MAX = 1
_RETRY_WAIT_SEC = 3


class SectorFlowStore:
    """
    제목: ia_sector_flow_daily Supabase 데이터 접근 객체
    내용: AlertStore와 동일한 lazy init 패턴(_url rstrip, _get_client).
          v1.1.0: 일시 장애 대비 1회 재시도 추가.

    책임:
      - upsert_daily_rows: 6 row 일괄 upsert (UNIQUE 멱등성 + 재시도)
      - fetch_latest_n_days: 최근 N일 row 조회 (snapshot_date 내림차순 + 재시도)
    """

    def __init__(
        self,
        supabase_url: str | None = None,
        supabase_key: str | None = None,
    ) -> None:
        """
        제목: SectorFlowStore 초기화

        Args:
            supabase_url: None이면 환경변수 사용
            supabase_key: None이면 환경변수 사용
        """
        raw_url = supabase_url or os.getenv("SUPABASE_URL", "")
        self._url = raw_url.rstrip("/") if raw_url else ""
        self._key = supabase_key or os.getenv("SUPABASE_KEY", "")
        self._client: object | None = None
        logger.info(f"[SectorFlowStore] v{VERSION} 초기화")

    def _get_client(self) -> object:
        """제목: Supabase 클라이언트 lazy init"""
        if self._client is not None:
            return self._client
        if not self._url or not self._key:
            raise RuntimeError("SUPABASE_URL/SUPABASE_KEY 환경변수 미설정")
        from supabase import create_client  # type: ignore[import]
        self._client = create_client(self._url, self._key)
        return self._client

    def upsert_daily_rows(
        self,
        snapshot_date: str,
        market: str,
        ticker_chg_map: dict[str, float | None],
    ) -> bool:
        """
        제목: 6 ticker row 일괄 upsert (v1.1.0 — 1회 재시도 적용)
        내용: snapshot_date, market, ticker 단위 멱등성(UNIQUE 제약 사용).
              supabase-py는 단일 RPC 호출로 트랜잭션 보장.
              일시 장애 대비 1회 재시도 (3초 대기).

        Args:
            snapshot_date: ISO date 'YYYY-MM-DD' (US ET 기준)
            market: 'US' (현재는 단일값)
            ticker_chg_map: {"XLV": -0.45, "XLU": None, ...}

        Returns:
            bool: 성공 시 True, 실패 시 False (raise 안 함)
        """
        rows: list[dict[str, object]] = []
        for ticker, chg in ticker_chg_map.items():
            group = TICKER_TO_GROUP.get(ticker)
            if not group:
                logger.warning(
                    f"[SectorFlowStore] ticker {ticker} 그룹 미정의 — 스킵"
                )
                continue
            rows.append({
                "snapshot_date": snapshot_date,
                "market": market,
                "ticker": ticker,
                "sector_group": group,
                "chg_pct": chg,
                "policy_version": POLICY_VERSION_SECTOR,
            })

        if not rows:
            logger.warning("[SectorFlowStore] upsert 대상 row 0건")
            return False

        # 재시도 루프 (1회 추가 시도)
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX + 1):
            try:
                client = self._get_client()
                client.table(TABLE_SECTOR_FLOW).upsert(  # type: ignore[union-attr]
                    rows,
                    on_conflict="snapshot_date,market,ticker",
                ).execute()

                if attempt > 0:
                    logger.info(
                        f"[SectorFlowStore] upsert 재시도 성공 "
                        f"(attempt={attempt + 1}/{_RETRY_MAX + 1})"
                    )

                logger.info(
                    f"[SectorFlowStore] upsert 완료: {len(rows)} rows "
                    f"(date={snapshot_date}, market={market})"
                )
                return True

            except Exception as e:
                last_err = e
                if attempt < _RETRY_MAX:
                    logger.warning(
                        f"[SectorFlowStore] upsert 실패 "
                        f"(attempt={attempt + 1}/{_RETRY_MAX + 1}): "
                        f"{type(e).__name__}: {e} — {_RETRY_WAIT_SEC}초 후 재시도"
                    )
                    time.sleep(_RETRY_WAIT_SEC)
                    continue

        logger.error(
            f"[SectorFlowStore] upsert 최종 실패 (모든 재시도 소진): "
            f"{type(last_err).__name__}: {last_err}"
        )
        return False

    def fetch_latest_n_days(
        self,
        n: int = 5,
        market: str = "US",
    ) -> list[dict]:
        """
        제목: 최근 N일치 row 조회 (v1.1.0 — 1회 재시도 적용)
        내용: snapshot_date 내림차순. 최대 N × 6 ticker row 반환.
              일시 장애 대비 1회 재시도 (3초 대기).

        Args:
            n: 최근 N일 (기본 5)
            market: 시장 필터 (기본 'US')

        Returns:
            list[dict]: [{snapshot_date, market, ticker, sector_group, chg_pct}, ...]
                       모든 재시도 실패 시 빈 리스트 (raise 안 함).
        """
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX + 1):
            try:
                client = self._get_client()
                result = (
                    client.table(TABLE_SECTOR_FLOW)  # type: ignore[union-attr]
                    .select("snapshot_date, market, ticker, sector_group, chg_pct")
                    .eq("market", market)
                    .order("snapshot_date", desc=True)
                    .limit(n * 6)
                    .execute()
                )
                data = result.data or []

                if attempt > 0:
                    logger.info(
                        f"[SectorFlowStore] 조회 재시도 성공 "
                        f"(attempt={attempt + 1}/{_RETRY_MAX + 1})"
                    )

                logger.info(
                    f"[SectorFlowStore] 조회 완료: {len(data)} rows "
                    f"(market={market}, n={n})"
                )
                return data

            except Exception as e:
                last_err = e
                if attempt < _RETRY_MAX:
                    logger.warning(
                        f"[SectorFlowStore] 조회 실패 "
                        f"(attempt={attempt + 1}/{_RETRY_MAX + 1}): "
                        f"{type(e).__name__}: {e} — {_RETRY_WAIT_SEC}초 후 재시도"
                    )
                    time.sleep(_RETRY_WAIT_SEC)
                    continue

        logger.warning(
            f"[SectorFlowStore] 조회 최종 실패 (빈 리스트 반환): "
            f"{type(last_err).__name__}: {last_err}"
        )
        return []
