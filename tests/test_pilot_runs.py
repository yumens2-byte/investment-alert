from investment_alert.models import AlertEvent, LayerScores
from investment_alert.pilot import PilotExecutor
from investment_alert.policy import ReviewStatus, SourcePolicy
from investment_alert.quota import QuotaMode


def _approved_policy() -> list[SourcePolicy]:
    return [SourcePolicy("fred", ReviewStatus.APPROVED, True)]


def test_pilot_run_1_l1_success() -> None:
    executor = PilotExecutor()
    outcome = executor.run(
        run_id="pilot-1",
        event=AlertEvent(
            alert_id="P-1",
            symbol="BTC",
            scores=LayerScores(price=30, derivatives=30, macro_news=25),
            quality_ok=True,
        ),
        policies=_approved_policy(),
        quota_usage_percent=70,
    )
    assert outcome.sent is True
    assert outcome.channels == ("telegram_free", "telegram_paid", "x")
    assert outcome.quota_mode == QuotaMode.NORMAL


def test_pilot_run_2_policy_blocked() -> None:
    executor = PilotExecutor()
    outcome = executor.run(
        run_id="pilot-2",
        event=AlertEvent(
            alert_id="P-2",
            symbol="ETH",
            scores=LayerScores(price=20, derivatives=20, macro_news=20),
            quality_ok=True,
        ),
        policies=[SourcePolicy("unknown_news", ReviewStatus.NOT_REVIEWED, True)],
        quota_usage_percent=70,
    )
    assert outcome.sent is False
    assert outcome.blocked_reasons == ("unknown_news: not reviewed",)


def test_pilot_run_3_core_only_blocks_non_l1() -> None:
    executor = PilotExecutor()
    outcome = executor.run(
        run_id="pilot-3",
        event=AlertEvent(
            alert_id="P-3",
            symbol="SOL",
            scores=LayerScores(price=20, derivatives=20, macro_news=10),
            quality_ok=True,
        ),
        policies=_approved_policy(),
        quota_usage_percent=96,
    )
    assert outcome.quota_mode == QuotaMode.CORE_ONLY
    assert outcome.sent is False
