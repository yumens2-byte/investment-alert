from __future__ import annotations

from investment_alert.models import AlertEvent, LayerScores
from investment_alert.policy import ReviewStatus, SourcePolicy
from investment_alert.runner import AlertRunner



def main() -> None:
    # TODO: replace with real collector/detection integration.
    runner = AlertRunner()
    event = AlertEvent(
        alert_id="hourly-heartbeat-btc",
        symbol="BTC",
        scores=LayerScores(price=30, derivatives=30, macro_news=25),
        quality_ok=True,
    )
    policies = [SourcePolicy("fred", ReviewStatus.APPROVED, True)]
    result = runner.dispatch_once(
        run_id="hourly-job",
        event=event,
        policies=policies,
        quota_usage_percent=70,
    )
    print(f"dispatch_result alert_id={result.alert_id} sent={result.sent} reason={result.reason}")


if __name__ == "__main__":
    main()
