"""
제목: Alert 엔진 — MacroNewsResult를 AlertSignal로 변환
내용: MacroNewsLayer의 감지 결과를 받아 AlertSignal을 생성하고,
      쿨다운 상태를 확인하여 발행 가능 여부를 결정합니다.
      감사 추적 가능한 UUID를 모든 Alert에 부여합니다.

주요 클래스:
  - AlertSignal: Alert 발행 단위 데이터 클래스
  - AlertEngine: MacroNewsResult → AlertSignal 변환 엔진

주요 함수:
  - AlertEngine.process(result): MacroNewsResult 처리, AlertSignal 반환
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.logger import get_logger
from detection.macro_news_layer import AlertLevel, MacroNewsResult

VERSION = "1.0.0"

logger = get_logger(__name__)

# 발행 정책 확정 (마스터 결정 2026-04-25)
# L1: X + TG Free + TG Paid
# L2: TG Free + TG Paid
# L3: 로그만 (발행 없음)
PUBLISH_POLICY: dict[str, dict[str, bool]] = {
    "L1": {"x": True,  "tg_free": True,  "tg_paid": True},
    "L2": {"x": False, "tg_free": True,  "tg_paid": True},
    "L3": {"x": False, "tg_free": False, "tg_paid": False},
    "NONE": {"x": False, "tg_free": False, "tg_paid": False},
}


@dataclass
class AlertSignal:
    """
    제목: Alert 발행 단위 데이터 클래스
    내용: AlertEngine.process()가 반환하는 발행 준비 완료 Alert.
          감사 추적을 위한 UUID, 발행 대상 채널 플래그, 쿨다운 상태를 포함합니다.

    책임:
      - alert_id (UUID) 기반 감사 추적
      - 레벨별 발행 채널 플래그 (publish_x, publish_tg_free, publish_tg_paid)
      - 쿨다운 활성 여부 (is_cooldown_active=True이면 발행 스킵)
    """

    alert_id: str
    level: AlertLevel
    score: float
    reasoning: str
    health_score: float
    created_at: datetime

    top_news_titles: list[str] = field(default_factory=list)
    top_youtube_titles: list[str] = field(default_factory=list)

    # 발행 채널 플래그
    publish_x: bool = False
    publish_tg_free: bool = False
    publish_tg_paid: bool = False

    # 쿨다운 활성이면 발행 스킵
    is_cooldown_active: bool = False

    @property
    def should_publish(self) -> bool:
        """
        제목: 발행 필요 여부
        내용: 쿨다운 미활성이고 최소 1개 채널 발행 대상이면 True.

        Returns:
            bool: 발행 필요하면 True
        """
        if self.is_cooldown_active:
            return False
        return self.publish_x or self.publish_tg_free or self.publish_tg_paid


class AlertEngine:
    """
    제목: MacroNewsResult → AlertSignal 변환 엔진
    내용: MacroNewsLayer의 감지 결과를 받아 발행 가능한 AlertSignal로 변환합니다.
          쿨다운 체크 및 발행 채널 플래그 결정을 담당합니다.

    책임:
      - UUID 기반 alert_id 생성
      - 레벨별 발행 정책(PUBLISH_POLICY) 적용
      - AlertStore를 통한 쿨다운 조회
      - AlertStore를 통한 감사로그 저장
    """

    def __init__(self, alert_store: object | None = None) -> None:
        """
        제목: AlertEngine 초기화

        Args:
            alert_store: AlertStore 인스턴스 (None이면 실제 Supabase 미사용)
        """
        self.alert_store = alert_store
        logger.info(f"[AlertEngine] v{VERSION} 초기화 (store={'있음' if alert_store else '없음'})")

    def process(self, result: MacroNewsResult) -> AlertSignal:
        """
        제목: MacroNewsResult 처리 → AlertSignal 생성
        내용: 감지 결과에서 AlertSignal을 생성하고, 쿨다운/발행정책을 적용합니다.
              NONE 레벨이면 발행 채널 모두 False 반환.

        처리 플로우:
          1. UUID alert_id 생성
          2. 레벨별 발행 정책 적용
          3. 쿨다운 활성 여부 확인 (AlertStore)
          4. top_news/youtube 제목 추출
          5. AlertSignal 조립
          6. Supabase 감사로그 저장 (AlertStore)

        Args:
            result: MacroNewsLayer.detect() 반환값

        Returns:
            AlertSignal: 발행 준비 완료 Signal
        """
        alert_id = str(uuid.uuid4())
        level = result.level
        policy = PUBLISH_POLICY.get(level, PUBLISH_POLICY["NONE"])

        # 제목: 쿨다운 체크
        is_cooldown = False
        if level != "NONE" and self.alert_store:
            try:
                is_cooldown = self.alert_store.is_cooldown_active(level)  # type: ignore[union-attr]
            except Exception as e:
                logger.warning(f"[AlertEngine] 쿨다운 조회 실패 (발행 허용): {e}")

        # 제목: top_news/youtube 제목 추출
        top_news_titles = [e.title for e in result.top_news]
        top_youtube_titles = [e.title for e in result.top_youtube]

        signal = AlertSignal(
            alert_id=alert_id,
            level=level,
            score=result.score,
            reasoning=result.reasoning,
            health_score=result.health_score,
            created_at=datetime.now(UTC),
            top_news_titles=top_news_titles,
            top_youtube_titles=top_youtube_titles,
            publish_x=policy["x"] and not is_cooldown,
            publish_tg_free=policy["tg_free"] and not is_cooldown,
            publish_tg_paid=policy["tg_paid"] and not is_cooldown,
            is_cooldown_active=is_cooldown,
        )

        # 제목: Supabase 감사로그 저장 (결정 2: B)
        if self.alert_store and level != "NONE":
            try:
                top_news_data = [
                    {"title": e.title, "source": e.source_name}
                    for e in result.top_news
                ]
                top_yt_data = [
                    {"title": e.title, "channel": e.source_name}
                    for e in result.top_youtube
                ]
                self.alert_store.save_alert(  # type: ignore[union-attr]
                    alert_id=alert_id,
                    level=level,
                    score=result.score,
                    health_score=result.health_score,
                    reasoning=result.reasoning,
                    top_news=top_news_data,
                    top_youtube=top_yt_data,
                )
            except Exception as e:
                logger.warning(f"[AlertEngine] 감사로그 저장 실패 (발행은 계속): {e}")

        logger.info(
            f"[AlertEngine] AlertSignal 생성: id={alert_id[:8]}, level={level}, "
            f"cooldown={is_cooldown}, publish=(x={signal.publish_x}, "
            f"tg_free={signal.publish_tg_free}, tg_paid={signal.publish_tg_paid})"
        )

        return signal
