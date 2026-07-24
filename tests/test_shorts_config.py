from __future__ import annotations

import pytest

from shorts.config import ShortsConfig


def test_safe_defaults_cannot_publish() -> None:
    config = ShortsConfig()
    config.validate()
    assert config.can_publish is False
    assert config.slot_times == ("08:00", "22:00")
    assert config.daily_limit == 2


def test_all_kill_switches_required_to_publish() -> None:
    assert ShortsConfig(enabled=True, upload_enabled=True, public_enabled=True).can_publish is True
    assert ShortsConfig(enabled=True, upload_enabled=False, public_enabled=True).can_publish is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("daily_limit", 3),
        ("slot_times", ("09:00", "22:00")),
        ("timezone", "UTC"),
        ("bgm_max_cost_usd", 0.01),
    ],
)
def test_locked_product_decisions_reject_drift(field: str, value: object) -> None:
    values = {field: value}
    with pytest.raises(ValueError):
        ShortsConfig(**values).validate()


def test_from_env_reads_safe_operating_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHORTS_ENABLED", "true")
    monkeypatch.setenv("SHORTS_UPLOAD_ENABLED", "true")
    monkeypatch.setenv("SHORTS_PUBLIC_ENABLED", "false")
    config = ShortsConfig.from_env()
    assert config.enabled is True
    assert config.can_publish is False
