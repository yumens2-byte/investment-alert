"""
제목: ia_sentiment_daily 테이블 데이터 접근 객체 (v1.0.0)
내용: EDT 심리지표 일간 산출 결과를 적재(upsert)/조회한다.
      SectorFlowStore v1.1.0과 동일한 lazy init + URL rstrip + 1회 재시도 패턴.

주요 클래스:
  - SentimentStore: ia_sentiment_daily Supabase 클라이언트

주요 함수:
  - SentimentStore.upsert_daily(row): score_date UNIQUE 멱등 upsert
  - SentimentStore.fetch_recent(n): 최근 N일 row (score_date 내림차순)
  - SentimentStore.fetch_e_score_days_ago(days): T축용 과거 E 점수 조회
  - SentimentStore.mark_published(score_date, channel): 발행 플래그 기록 (발행-기록 짝 규약)
"""

from __future__ import annotations

import os
import time

from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)

TABLE_SENTIMENT = "ia_sentiment_daily"

# SectorFlowStore v1.1.0 동일 — Supabase 일시 장애 대비 1회 재시도
_RETRY_MAX = 1
_RETRY_WAIT_SEC = 3


class SentimentStore:
    """
    제목: ia_sentiment_daily Supabase 데이터 접근 객체
    내용: SectorFlowStore와 동일한 lazy init 패턴.

    책임:
      - upsert_daily: score_date UNIQUE 멱등 upsert (재실행 안전)
      - fetch_recent / fetch_e_score_days_ago: T축 룩백 조회
      - mark_published: 발행 결과 기록 (발행-기록 짝 규약 준수)
    """

    def __init__(
        self,
        supabase_url: str | None = None,
        supabase_key: str | None = None,
    ) -> None:
        """
        제목: SentimentStore 초기화

        Args:
            supabase_url: None이면 환경변수 SUPABASE_URL 사용
            supabase_key: None이면 환경변수 SUPABASE_KEY 사용
        """
        raw_url = supabase_url or os.getenv("SUPABASE_URL", "")
        self._url = raw_url.rstrip("/") if raw_url else ""
        self._key = supabase_key or os.getenv("SUPABASE_KEY", "")
        self._client: object | None = None
        logger.info(f"[SentimentStore] v{VERSION} 초기화")

    def _get_client(self) -> object:
        """제목: Supabase 클라이언트 lazy init"""
        if self._client is not None:
            return self._client
        if not self._url or not self._key:
            raise RuntimeError("SUPABASE_URL/SUPABASE_KEY 환경변수 미설정")
        from supabase import create_client  # type: ignore[import]

        self._client = create_client(self._url, self._key)
        return self._client

    # ────────────────────────────────────────────────
    # 쓰기
    # ────────────────────────────────────────────────
    def upsert_daily(self, row: dict) -> bool:
        """
        제목: 일간 결과 upsert (멱등)
        내용: score_date UNIQUE 제약으로 재실행 시 갱신. 1회 재시도.

        Args:
            row: ia_sentiment_daily 컬럼 dict (score_date 필수)

        Returns:
            bool: 성공 여부
        """
        if not row.get("score_date"):
            logger.warning("[SentimentStore] upsert 스킵 — score_date 없음")
            return False
        for attempt in range(_RETRY_MAX + 1):
            try:
                client = self._get_client()
                client.table(TABLE_SENTIMENT).upsert(
                    row, on_conflict="score_date"
                ).execute()
                logger.info(f"[SentimentStore] upsert 완료: {row['score_date']}")
                return True
            except Exception as e:
                logger.warning(
                    f"[SentimentStore] upsert 실패 (시도 {attempt + 1}): "
                    f"{type(e).__name__}: {e}"
                )
                if attempt < _RETRY_MAX:
                    time.sleep(_RETRY_WAIT_SEC)
        return False

    def mark_published(self, score_date: str, channel: str) -> bool:
        """
        제목: 발행 플래그 기록 (발행-기록 짝 규약)
        내용: channel은 "x" 또는 "tg_internal" — published_{channel}=True 갱신.

        Args:
            score_date: 대상 일자 (YYYY-MM-DD)
            channel: "x" | "tg_internal"

        Returns:
            bool: 성공 여부
        """
        column = f"published_{channel}"
        for attempt in range(_RETRY_MAX + 1):
            try:
                client = self._get_client()
                client.table(TABLE_SENTIMENT).update({column: True}).eq(
                    "score_date", score_date
                ).execute()
                logger.info(f"[SentimentStore] {column}=True 기록: {score_date}")
                return True
            except Exception as e:
                logger.warning(
                    f"[SentimentStore] mark_published 실패 (시도 {attempt + 1}): "
                    f"{type(e).__name__}: {e}"
                )
                if attempt < _RETRY_MAX:
                    time.sleep(_RETRY_WAIT_SEC)
        return False

    # ────────────────────────────────────────────────
    # 읽기
    # ────────────────────────────────────────────────
    def fetch_recent(self, n: int = 10) -> list[dict]:
        """
        제목: 최근 N일 row 조회 (score_date 내림차순)
        내용: 1회 재시도. 실패 시 빈 리스트 (파이프라인 중단 없음 — T축만 결측 처리).
        """
        for attempt in range(_RETRY_MAX + 1):
            try:
                client = self._get_client()
                result = (
                    client.table(TABLE_SENTIMENT)
                    .select("*")
                    .order("score_date", desc=True)
                    .limit(n)
                    .execute()
                )
                return list(result.data or [])
            except Exception as e:
                logger.warning(
                    f"[SentimentStore] fetch_recent 실패 (시도 {attempt + 1}): "
                    f"{type(e).__name__}: {e}"
                )
                if attempt < _RETRY_MAX:
                    time.sleep(_RETRY_WAIT_SEC)
        return []

    def fetch_e_score_days_ago(self, days: int, today: str) -> float | None:
        """
        제목: T축 룩백용 과거 E 점수 조회
        내용: today보다 과거인 row 중 최신순으로 days번째 row의 e_score.
              영업일 기준 근사 — DB에는 영업일만 적재되므로 row 순번 = 영업일 수.
              콜드스타트(이력 부족) 시 None.

        Args:
            days: 룩백 영업일 수 (예: 5)
            today: 기준일 YYYY-MM-DD (자기 자신 제외용)

        Returns:
            float | None
        """
        for attempt in range(_RETRY_MAX + 1):
            try:
                client = self._get_client()
                result = (
                    client.table(TABLE_SENTIMENT)
                    .select("score_date,e_score")
                    .lt("score_date", today)
                    .order("score_date", desc=True)
                    .limit(days)
                    .execute()
                )
                rows = list(result.data or [])
                if len(rows) < days:
                    logger.info(
                        f"[SentimentStore] T축 콜드스타트 — 이력 {len(rows)}/{days}건"
                    )
                    return None
                value = rows[days - 1].get("e_score")
                return float(value) if value is not None else None
            except Exception as e:
                logger.warning(
                    f"[SentimentStore] fetch_e_score_days_ago 실패 (시도 {attempt + 1}): "
                    f"{type(e).__name__}: {e}"
                )
                if attempt < _RETRY_MAX:
                    time.sleep(_RETRY_WAIT_SEC)
        return None
