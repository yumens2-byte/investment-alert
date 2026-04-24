from __future__ import annotations

from investment_alert.models import AlertEvent, LayerScores
from investment_alert.policy import ReviewStatus, SourcePolicy
from investment_alert.runner import AlertRunner


def main() -> None:
    runner = AlertRunner(state_path=".state/pilot_twice.json")
    event = AlertEvent(
        alert_id="pilot-twice-btc",
        symbol="BTC",
        scores=LayerScores(price=30, derivatives=30, macro_news=25),
        quality_ok=True,
    )
    policies = [SourcePolicy("fred", ReviewStatus.APPROVED, True)]

    for idx in (1, 2):
        result = runner.dispatch_once(
            run_id=f"pilot-{idx}",
            event=event,
            policies=policies,
            quota_usage_percent=70,
        )
        print(f"pilot_run={idx} sent={result.sent} reason={result.reason}")


if __name__ == "__main__":
    main()
