"""
제목: Supabase Alert 데이터 적재 모듈
내용: ia_alert_history(감사로그)와 ia_cooldown_state(쿨다운)를
      Supabase PostgreSQL에 읽기/쓰기합니다.

주요 클래스:
  - AlertStore: Supabase 연동 데이터 접근 객체

주요 함수:
  - AlertStore.save_alert(signal): alert_history 적재
  - AlertStore.update_publish_result(...): 발행 결과 업데이트
  - AlertStore.is_cooldown_active(level): 쿨다운 활성 여부 확인
  - AlertStore.set_cooldown(level, minutes): 쿨다운 설정
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)

# 제목: Supabase 테이블명
TABLE_ALERT_HISTORY = "ia_alert_history"
TABLE_COOLDOWN_STATE = "ia_cooldown_state"

# 제목: 레벨별 쿨다운 분 (결정 1: B — Supabase 기반)
COOLDOWN_MINUTES: dict[str, int] = {
    "L1": 60,
    "L2": 90,
    "L3": 120,
    "SYSTEM_DEGRADED": 30,  # 운영자 과다 알림 방지 (Phase 1 신규)
}

# 제목: 주제 해시 쿨다운 (FR-04)
# 내용: 동일 topic_hash 90분 내 재발행 금지, 2회 이상 감지 시 요약 발행
TOPIC_COOLDOWN_MINUTES: int = 90
TOPIC_SUMMARY_THRESHOLD: int = 2  # 이 횟수 이상 감지 시 요약 업데이트 발행
TABLE_TOPIC_COOLDOWN = "ia_topic_cooldown"


class AlertStore:
    """
    제목: Supabase Alert 데이터 접근 객체
    내용: ia_alert_history, ia_cooldown_state 테이블에 읽기/쓰기합니다.
          supabase-py 클라이언트를 lazy init으로 생성합니다.

    책임:
      - alert_history UPSERT (alert_id 기준 멱등성)
      - 발행 결과 업데이트 (x_published, tg_free_published, tg_paid_published)
      - 쿨다운 조회/설정 (레벨별)
    """

    def __init__(
        self,
        supabase_url: str | None = None,
        supabase_key: str | None = None,
    ) -> None:
        """
        제목: AlertStore 초기화

        Args:
            supabase_url: Supabase 프로젝트 URL (None이면 환경변수 사용)
            supabase_key: Supabase anon/service key (None이면 환경변수 사용)
        """
        # 제목: trailing slash 방어 처리
        # 내용: URL 끝 슬래시 제거 — //rest/v1/... 이중 슬래시로 인한 PGRST125 방지
        raw_url = supabase_url or os.getenv("SUPABASE_URL", "")
        self._url = raw_url.rstrip("/") if raw_url else ""
        self._key = supabase_key or os.getenv("SUPABASE_KEY", "")
        self._client: object | None = None
        logger.info(f"[AlertStore] v{VERSION} 초기화")

    def _get_client(self) -> object:
        """
        제목: Supabase 클라이언트 lazy init
        내용: 첫 호출 시 클라이언트를 생성하고 재사용합니다.

        Returns:
            supabase.Client: 초기화된 클라이언트

        Raises:
            RuntimeError: URL/KEY 미설정 시
        """
        if self._client is not None:
            return self._client

        if not self._url or not self._key:
            raise RuntimeError("SUPABASE_URL/SUPABASE_KEY 환경변수 미설정")

        from supabase import create_client  # type: ignore[import]
        self._client = create_client(self._url, self._key)
        return self._client

    def save_alert(
        self,
        alert_id: str,
        level: str,
        score: float,
        health_score: float,
        reasoning: str,
        top_news: list[dict],
        top_youtube: list[dict],
        reasoning_json: dict | None = None,
        policy_version: str | None = None,
        published_channels: list[str] | None = None,
    ) -> bool:
        """
        제목: Alert 감사로그 저장
        내용: ia_alert_history에 UPSERT합니다. alert_id가 같으면 무시합니다.

        Args:
            alert_id: UUID4 감사 추적 키
            level: 'L1'|'L2'|'L3'|'NONE'|'SYSTEM_DEGRADED'
            score: Macro-News Score
            health_score: 데이터 건강도
            reasoning: 판정 근거 (text)
            top_news: [{"title": str, "source": str}, ...]
            top_youtube: [{"title": str, "channel": str}, ...]
            reasoning_json: FR-05 표준화 JSON (None이면 빈 dict로 저장)
            policy_version: 적용된 정책 버전 (semver)
            published_channels: 발행 성공 채널 리스트 (e.g. ["x","tg_free"])

        Returns:
            bool: 성공 시 True, 실패 시 False
        """
        try:
            client = self._get_client()
            data: dict[str, object] = {
                "alert_id": alert_id,
                "level": level,
                "score": score,
                "health_score": health_score,
                "reasoning": reasoning,
                "top_news": top_news,
                "top_youtube": top_youtube,
            }
            # FR-05 신규 컬럼은 마이그레이션 적용 후에만 의미 있음
            # 하지만 None이 아닐 때만 전송하여 마이그레이션 미적용 환경에서도 동작
            if reasoning_json is not None:
                data["reasoning_json"] = reasoning_json
            if policy_version is not None:
                data["policy_version"] = policy_version
            if published_channels is not None:
                data["published_channels"] = published_channels

            client.table(TABLE_ALERT_HISTORY).upsert(  # type: ignore[union-attr]
                data, on_conflict="alert_id"
            ).execute()
            logger.info(f"[AlertStore] alert_history 저장 완료: alert_id={alert_id[:8]}")
            return True
        except Exception as e:
            logger.error(f"[AlertStore] alert_history 저장 실패: {type(e).__name__}: {e}")
            return False

    def update_publish_result(
        self,
        alert_id: str,
        x_published: bool = False,
        tg_free_published: bool = False,
        tg_paid_published: bool = False,
        x_error: str | None = None,
        tg_free_error: str | None = None,
        tg_paid_error: str | None = None,
        tg_internal_published: bool = False,
        tg_internal_error: str | None = None,
    ) -> bool:
        """
        제목: 발행 결과 업데이트
        내용: 발행 성공/실패 여부를 ia_alert_history에 반영합니다.
              FR-04에 따라 tg_internal 채널 결과도 기록한다.

        Args:
            alert_id: 대상 Alert ID
            x_published: X 발행 성공 여부
            tg_free_published: TG Free 발행 성공 여부
            tg_paid_published: TG Paid 발행 성공 여부
            x_error: X 발행 오류 메시지 (실패 시)
            tg_free_error: TG Free 오류 메시지
            tg_paid_error: TG Paid 오류 메시지
            tg_internal_published: TG Internal 발행 성공 여부 (FR-04)
            tg_internal_error: TG Internal 오류 메시지

        Returns:
            bool: 성공 시 True
        """
        try:
            client = self._get_client()
            updates: dict[str, object] = {
                "x_published": x_published,
                "tg_free_published": tg_free_published,
                "tg_paid_published": tg_paid_published,
            }
            # tg_internal 컬럼은 마이그레이션 적용 후에만 의미 있음
            # 기본값 False는 항상 전송, error는 None이 아닐 때만 전송
            updates["tg_internal_published"] = tg_internal_published
            if x_error:
                updates["x_error"] = x_error[:500]
            if tg_free_error:
                updates["tg_free_error"] = tg_free_error[:500]
            if tg_paid_error:
                updates["tg_paid_error"] = tg_paid_error[:500]
            if tg_internal_error:
                updates["tg_internal_error"] = tg_internal_error[:500]

            client.table(TABLE_ALERT_HISTORY).update(updates).eq(  # type: ignore[union-attr]
                "alert_id", alert_id
            ).execute()
            logger.info(f"[AlertStore] 발행 결과 업데이트: alert_id={alert_id[:8]}")
            return True
        except Exception as e:
            logger.error(f"[AlertStore] 발행 결과 업데이트 실패: {e}")
            return False

    def is_cooldown_active(self, level: str) -> bool:
        """
        제목: 쿨다운 활성 여부 확인
        내용: ia_cooldown_state에서 해당 레벨의 cooldown_until을 조회하여
              현재 시각보다 미래이면 쿨다운 중으로 판정합니다.

        Args:
            level: 'L1'|'L2'|'L3'

        Returns:
            bool: 쿨다운 중이면 True
        """
        try:
            client = self._get_client()
            result = (
                client.table(TABLE_COOLDOWN_STATE)  # type: ignore[union-attr]
                .select("cooldown_until")
                .eq("level", level)
                .execute()
            )
            rows = result.data
            if not rows:
                return False

            cooldown_until_str = rows[0]["cooldown_until"]
            # 제목: ISO 문자열을 UTC datetime으로 변환
            from dateutil.parser import parse as dateutil_parse
            cooldown_until = dateutil_parse(cooldown_until_str)
            if cooldown_until.tzinfo is None:
                cooldown_until = cooldown_until.replace(tzinfo=UTC)

            now = datetime.now(UTC)
            is_active = cooldown_until > now

            if is_active:
                remaining = (cooldown_until - now).seconds // 60
                logger.info(f"[AlertStore] {level} 쿨다운 활성 (잔여 {remaining}분)")

            return is_active

        except Exception as e:
            logger.warning(f"[AlertStore] 쿨다운 조회 실패 (False 반환): {e}")
            return False

    def set_cooldown(self, level: str, alert_id: str) -> bool:
        """
        제목: 쿨다운 설정
        내용: 발행 후 해당 레벨의 cooldown_until을 현재 시각 + COOLDOWN_MINUTES로 설정합니다.

        Args:
            level: 'L1'|'L2'|'L3'
            alert_id: 발행된 Alert ID

        Returns:
            bool: 성공 시 True
        """
        try:
            client = self._get_client()
            minutes = COOLDOWN_MINUTES.get(level, 90)
            now = datetime.now(UTC)
            cooldown_until = now + timedelta(minutes=minutes)

            client.table(TABLE_COOLDOWN_STATE).upsert(  # type: ignore[union-attr]
                {
                    "level": level,
                    "last_alert_id": alert_id,
                    "last_published_at": now.isoformat(),
                    "cooldown_until": cooldown_until.isoformat(),
                    "updated_at": now.isoformat(),
                },
                on_conflict="level",
            ).execute()

            logger.info(
                f"[AlertStore] {level} 쿨다운 설정: "
                f"{minutes}분 ({cooldown_until.strftime('%H:%M')} UTC까지)"
            )
            return True
        except Exception as e:
            logger.error(f"[AlertStore] 쿨다운 설정 실패: {e}")
            return False

    # ────────────────────────────────────────────────────
    # FR-04: topic_hash 기반 중복 억제
    # ────────────────────────────────────────────────────
    def is_topic_cooldown_active(self, topic_hash: str) -> bool:
        """
        제목: 주제 해시 쿨다운 활성 여부
        내용: 동일 topic_hash가 90분 내 이미 발행됐으면 True 반환.

        Args:
            topic_hash: 주제 해시 키

        Returns:
            bool: 쿨다운 중이면 True
        """
        try:
            client = self._get_client()
            result = (
                client.table(TABLE_TOPIC_COOLDOWN)
                .select("cooldown_until, seen_count")
                .eq("topic_hash", topic_hash)
                .execute()
            )
            rows = result.data
            if not rows:
                return False

            from dateutil.parser import parse as dateutil_parse
            cooldown_until = dateutil_parse(rows[0]["cooldown_until"])
            if cooldown_until.tzinfo is None:
                cooldown_until = cooldown_until.replace(tzinfo=UTC)

            return cooldown_until > datetime.now(UTC)
        except Exception as e:
            logger.warning(f"[AlertStore] topic 쿨다운 조회 실패 (False 반환): {e}")
            return False

    def upsert_topic_cooldown(
        self,
        topic_hash: str,
        level: str,
        keywords: str = "",
    ) -> dict:
        """
        제목: 주제 해시 쿨다운 등록/업데이트
        내용: 신규이면 INSERT, 기존이면 seen_count 증가 + cooldown_until 갱신.
              seen_count >= TOPIC_SUMMARY_THRESHOLD이면 요약 발행 필요 신호 반환.

        Args:
            topic_hash: 주제 해시 키
            level: 감지 레벨
            keywords: 매칭된 키워드 문자열

        Returns:
            dict: {'is_new': bool, 'seen_count': int, 'needs_summary': bool}
        """
        try:
            client = self._get_client()
            now = datetime.now(UTC)
            cooldown_until = now + timedelta(minutes=TOPIC_COOLDOWN_MINUTES)

            # 기존 레코드 조회
            result = (
                client.table(TABLE_TOPIC_COOLDOWN)
                .select("id, seen_count, summary_sent")
                .eq("topic_hash", topic_hash)
                .execute()
            )
            rows = result.data

            if not rows:
                # 신규 등록
                client.table(TABLE_TOPIC_COOLDOWN).insert({
                    "topic_hash": topic_hash,
                    "level": level,
                    "first_seen_at": now.isoformat(),
                    "last_seen_at": now.isoformat(),
                    "seen_count": 1,
                    "cooldown_until": cooldown_until.isoformat(),
                    "keywords": keywords,
                    "summary_sent": False,
                }).execute()
                logger.info(f"[AlertStore] topic 쿨다운 신규 등록: {topic_hash[:8]}")
                return {"is_new": True, "seen_count": 1, "needs_summary": False}
            else:
                # 기존 업데이트
                old_count = rows[0].get("seen_count", 1)
                new_count = old_count + 1
                summary_sent = rows[0].get("summary_sent", False)
                needs_summary = (new_count >= TOPIC_SUMMARY_THRESHOLD) and not summary_sent

                client.table(TABLE_TOPIC_COOLDOWN).update({
                    "last_seen_at": now.isoformat(),
                    "seen_count": new_count,
                    "cooldown_until": cooldown_until.isoformat(),
                    "updated_at": now.isoformat(),
                }).eq("topic_hash", topic_hash).execute()

                logger.info(
                    f"[AlertStore] topic 쿨다운 업데이트: {topic_hash[:8]} "
                    f"(seen={new_count}, needs_summary={needs_summary})"
                )
                return {"is_new": False, "seen_count": new_count, "needs_summary": needs_summary}
        except Exception as e:
            logger.error(f"[AlertStore] topic 쿨다운 업데이트 실패: {e}")
            return {"is_new": True, "seen_count": 1, "needs_summary": False}

    def mark_topic_summary_sent(self, topic_hash: str) -> bool:
        """
        제목: 주제 요약 발행 완료 마킹

        Args:
            topic_hash: 주제 해시 키

        Returns:
            bool: 성공 시 True
        """
        try:
            client = self._get_client()
            client.table(TABLE_TOPIC_COOLDOWN).update(
                {"summary_sent": True, "updated_at": datetime.now(UTC).isoformat()}
            ).eq("topic_hash", topic_hash).execute()
            return True
        except Exception as e:
            logger.error(f"[AlertStore] topic summary 마킹 실패: {e}")
            return False
