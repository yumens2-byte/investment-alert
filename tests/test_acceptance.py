from datetime import datetime, timedelta, timezone

from investment_alert.audit import AuditRecord, InMemoryAuditStore
from investment_alert.models import AlertEvent, AlertLevel, LayerScores
from investment_alert.policy import ReviewStatus, SourcePolicy, SourcePolicyGate
from investment_alert.quota import QuotaManager, QuotaMode
from investment_alert.routing import ChannelRouter


def test_ac01_l1_routes_to_three_channels() -> None:
    router = ChannelRouter()
    assert router.channels_for(AlertLevel.L1) == (
        "telegram_free",
        "telegram_paid",
        "x",
    )


def test_ac02_duplicate_cooldown() -> None:
    router = ChannelRouter(cooldown_seconds=300)
    now = datetime.now(timezone.utc)
    assert router.can_send("btc-l1", now=now) is True
    assert router.can_send("btc-l1", now=now + timedelta(seconds=10)) is False
    assert router.can_send("btc-l1", now=now + timedelta(seconds=301)) is True


def test_ac03_audit_record_lookup() -> None:
    store = InMemoryAuditStore()
    record = AuditRecord(
        alert_id="A-100",
        score_total=88.2,
        evidence_sources=("fred", "polygon"),
        delivery_results={"telegram_free": "ok", "x": "ok"},
    )
    store.save(record)
    assert store.get("A-100") == record


def test_ac04_unreviewed_source_blocked() -> None:
    gate = SourcePolicyGate()
    ok, blocked = gate.validate_for_deploy(
        [
            SourcePolicy("fred", ReviewStatus.APPROVED, True),
            SourcePolicy("unknown_news", ReviewStatus.NOT_REVIEWED, True),
        ]
    )
    assert ok is False
    assert blocked == ["unknown_news: not reviewed"]


def test_ac05_quota_auto_downgrade() -> None:
    manager = QuotaManager()
    assert manager.mode_for_usage(82) == QuotaMode.STOP_EXPERIMENTAL
    assert manager.mode_for_usage(91) == QuotaMode.LOW_FREQ_BACKUP
    assert manager.mode_for_usage(95) == QuotaMode.CORE_ONLY


def test_quality_health_can_downgrade_level() -> None:
    event = AlertEvent(
        alert_id="A-200",
        symbol="BTC",
        scores=LayerScores(price=40, derivatives=30, macro_news=15),
        quality_ok=False,
    )
    assert event.level() == AlertLevel.L2
