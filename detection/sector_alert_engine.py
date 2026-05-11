"""
제목: SectorRotationResult → SectorSignal 변환 엔진
내용: 기존 AlertEngine과 분리된 sector 전용 엔진.
      SHADOW_MODE 분기 + 쿨다운 prefix 'sector:' 처리 + audit_fallback 분기.

주요 클래스:
  - SectorSignal: Alert 발행 단위 dataclass
  - SectorAlertEngine: SectorRotationResult → SectorSignal 변환

주요 함수:
  - SectorAlertEngine.process(result): 변환 + 쿨다운 체크 + 감사로그 저장
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from config.settings import get_env_bool
from core.logger import get_logger
from db.alert_store import AlertStore
from detection.sector_flow_layer import SectorRotationResult

VERSION = "1.0.0"

logger = get_logger(__name__)

# Sector 전용 발행 정책 (macro_news PUBLISH_POLICY와 분리)
SECTOR_PUBLISH_POLICY: dict[str, dict[str, bool]] = {
    "L1":   {"x": False, "tg_free": False, "tg_paid": False, "tg_internal": True},
    "L2":   {"x": False, "tg_free": True,  "tg_paid": True,  "tg_internal": True},
    "NONE": {"x": False, "tg_free": False, "tg_paid": False, "tg_internal": False},
}

# 쿨다운 level prefix (macro_news와 격리 — AlertStore 수정 불필요, 호출부 prefix만)
COOLDOWN_LEVEL_PREFIX = "sector:"


@dataclass
class SectorSignal:
    """
    제목: Sector Alert 발행 단위 dataclass
    내용: SectorRotationResult를 발행 준비 완료 상태로 변환한 결과.

    책임:
      - alert_id (UUID4) 기반 감사 추적
      - 레벨별 채널 플래그 (shadow_mode 반영)
      - 쿨다운 활성 여부
      - audit_persisted (B5 패치 패턴)
    """
    alert_id: str
    level: str
    rotation_type: str
    spread_1d: float | None
    spread_5d: float | None
    def_avg_1d: float | None
    cyc_avg_1d: float | None
    def_avg_5d: float | None
    cyc_avg_5d: float | None
    reasoning: str
    reasoning_json: dict = field(default_factory=dict)
    policy_version: str = "sector-v1.0.0"
    health_score: float = 1.0
    rows_used: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # 발행 채널 플래그
    publish_x: bool = False
    publish_tg_free: bool = False
    publish_tg_paid: bool = False
    publish_tg_internal: bool = False

    # 상태
    is_cooldown_active: bool = False
    audit_persisted: bool = True
    shadow_mode: bool = False

    @property
    def should_publish(self) -> bool:
        """제목: 발행 필요 여부"""
        if self.is_cooldown_active:
            return False
        return (
            self.publish_x
            or self.publish_tg_free
            or self.publish_tg_paid
            or self.publish_tg_internal
        )


class SectorAlertEngine:
    """
    제목: SectorRotationResult → SectorSignal 변환 엔진
    내용: SHADOW_MODE 분기 + 쿨다운 'sector:' prefix 처리.
          AlertStore는 손대지 않고 호출부에서만 prefix 부여.

    책임:
      - UUID 기반 alert_id 생성
      - SECTOR_PUBLISH_POLICY 적용 (SHADOW_MODE 강제 적용)
      - 쿨다운 조회 ('sector:L2' 형태로 AlertStore 호출)
      - ia_alert_history 적재 (top_news/youtube 빈 리스트)
      - B5 audit_fallback 분기 (save_alert 실패 시)
    """

    def __init__(self, alert_store: AlertStore) -> None:
        """
        제목: SectorAlertEngine 초기화

        Args:
            alert_store: AlertStore 인스턴스 (재사용)
        """
        self.alert_store = alert_store
        logger.info(f"[SectorAlertEngine] v{VERSION} 초기화")

    def process(self, result: SectorRotationResult) -> SectorSignal:
        """
        제목: SectorRotationResult → SectorSignal
        내용:
          1. UUID alert_id 생성
          2. SHADOW_MODE 확인
          3. SECTOR_PUBLISH_POLICY 적용 (SHADOW_MODE 시 tg_free/tg_paid 강제 False)
          4. 쿨다운 체크 ('sector:' prefix)
          5. SectorSignal 조립
          6. 감사로그 저장 + B5 fallback

        Args:
            result: SectorFlowLayer.detect() 반환값

        Returns:
            SectorSignal
        """
        alert_id = str(uuid.uuid4())
        level = result.level
        shadow_mode = get_env_bool("SECTOR_SHADOW_MODE", default=True)

        # 발행 정책 적용
        base_policy = SECTOR_PUBLISH_POLICY.get(level, SECTOR_PUBLISH_POLICY["NONE"])

        if shadow_mode:
            # Shadow: tg_free/tg_paid 강제 차단. tg_internal은 정책 그대로.
            policy = {
                "x": False,
                "tg_free": False,
                "tg_paid": False,
                "tg_internal": base_policy.get("tg_internal", False),
            }
            logger.info(
                "[SectorAlertEngine] SHADOW_MODE=true — tg_free/tg_paid 강제 차단"
            )
        else:
            policy = dict(base_policy)

        # 쿨다운 체크 (sector: prefix 적용)
        is_cooldown = False
        if level != "NONE":
            prefixed_level = f"{COOLDOWN_LEVEL_PREFIX}{level}"
            try:
                is_cooldown = self.alert_store.is_cooldown_active(prefixed_level)
            except Exception as e:
                logger.warning(
                    f"[SectorAlertEngine] 쿨다운 조회 실패 (발행 허용): {e}"
                )

        signal = SectorSignal(
            alert_id=alert_id,
            level=level,
            rotation_type=result.rotation_type,
            spread_1d=result.spread_1d,
            spread_5d=result.spread_5d,
            def_avg_1d=result.def_avg_1d,
            cyc_avg_1d=result.cyc_avg_1d,
            def_avg_5d=result.def_avg_5d,
            cyc_avg_5d=result.cyc_avg_5d,
            reasoning=result.reasoning,
            reasoning_json=result.reasoning_json,
            policy_version=result.policy_version,
            health_score=result.health_score,
            rows_used=result.rows_used,
            publish_x=policy["x"] and not is_cooldown,
            publish_tg_free=policy["tg_free"] and not is_cooldown,
            publish_tg_paid=policy["tg_paid"] and not is_cooldown,
            publish_tg_internal=policy["tg_internal"] and not is_cooldown,
            is_cooldown_active=is_cooldown,
            shadow_mode=shadow_mode,
        )

        # 감사로그 저장 (NONE 제외)
        if level != "NONE":
            try:
                ok = self.alert_store.save_alert(
                    alert_id=alert_id,
                    level=level,
                    score=0.0,  # sector는 score 개념 없음 — placeholder
                    health_score=result.health_score,
                    reasoning=result.reasoning,
                    top_news=[],  # sector는 텍스트 이벤트 없음
                    top_youtube=[],
                    reasoning_json=result.reasoning_json,
                    policy_version=result.policy_version,
                )
                signal.audit_persisted = bool(ok)
            except Exception as e:
                logger.warning(
                    f"[SectorAlertEngine] 감사로그 저장 예외 (발행 계속): {e}"
                )
                signal.audit_persisted = False

            # B5: save_alert 실패 → audit fallback (run_alert.py 패턴 동일)
            if not signal.audit_persisted:
                from core.audit_fallback import append_audit_fallback
                append_audit_fallback({
                    "stage": "save_alert",
                    "alert_id": alert_id,
                    "level": level,
                    "rotation_type": result.rotation_type,
                    "spread_5d": result.spread_5d,
                    "reasoning": result.reasoning,
                    "policy_version": result.policy_version,
                    "reason": "sector_save_alert_returned_false_or_raised",
                })

        logger.info(
            f"[SectorAlertEngine] SectorSignal: id={alert_id[:8]}, "
            f"level={level}, rotation={result.rotation_type}, "
            f"shadow={shadow_mode}, cooldown={is_cooldown}, "
            f"publish=(tg_free={signal.publish_tg_free}, "
            f"tg_paid={signal.publish_tg_paid}, "
            f"tg_internal={signal.publish_tg_internal})"
        )

        return signal
