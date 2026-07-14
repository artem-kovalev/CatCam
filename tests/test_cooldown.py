from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from catcam.config import CooldownConfig
from catcam.cooldown import CooldownConfigError, CooldownManager


def _config(tmp_path: Path, **overrides) -> CooldownConfig:
    defaults = dict(
        default_minutes=60,
        min_minutes=1,
        max_minutes=1440,
        state_file=str(tmp_path / "cooldown.json"),
    )
    defaults.update(overrides)
    return CooldownConfig(**defaults)


class FakeClock:
    def __init__(self, start: datetime):
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, **timedelta_kwargs) -> None:
        self._now += timedelta(**timedelta_kwargs)


def test_fresh_state_is_not_in_cooldown(tmp_path):
    manager = CooldownManager(_config(tmp_path))
    assert manager.is_in_cooldown() is False


def test_default_interval_is_60_when_no_prior_state(tmp_path):
    manager = CooldownManager(_config(tmp_path))
    assert manager.get_interval_minutes() == 60


def test_notifications_enabled_by_default(tmp_path):
    manager = CooldownManager(_config(tmp_path))
    assert manager.notifications_enabled() is True


def test_in_cooldown_until_interval_elapses(tmp_path):
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    manager = CooldownManager(_config(tmp_path, default_minutes=30), clock=clock)

    manager.record_delivery_success()
    assert manager.is_in_cooldown() is True

    clock.advance(minutes=29)
    assert manager.is_in_cooldown() is True

    clock.advance(minutes=2)  # total 31 minutes elapsed
    assert manager.is_in_cooldown() is False


def test_set_interval_minutes_valid(tmp_path):
    manager = CooldownManager(_config(tmp_path))
    manager.set_interval_minutes(120)
    assert manager.get_interval_minutes() == 120


@pytest.mark.parametrize("bad_value", [0, 1441, -5, 30.5, "30", None])
def test_set_interval_minutes_rejects_invalid_values(tmp_path, bad_value):
    manager = CooldownManager(_config(tmp_path))
    with pytest.raises((ValueError, CooldownConfigError)):
        manager.set_interval_minutes(bad_value)
    # Rejected value must not have changed stored state.
    assert manager.get_interval_minutes() == 60


def test_state_persists_across_reinstantiation(tmp_path):
    config = _config(tmp_path)
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))

    first = CooldownManager(config, clock=clock)
    first.set_interval_minutes(45)
    first.record_delivery_success()
    first.disable_notifications()

    second = CooldownManager(config, clock=clock)
    assert second.get_interval_minutes() == 45
    assert second.notifications_enabled() is False
    assert second.is_in_cooldown() is True


def test_disabling_notifications_is_independent_of_cooldown_state(tmp_path):
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    manager = CooldownManager(_config(tmp_path), clock=clock)

    # Not in cooldown (no delivery yet), but notifications disabled.
    manager.disable_notifications()
    assert manager.is_in_cooldown() is False
    assert manager.notifications_enabled() is False

    # In cooldown, but notifications re-enabled - the two are independent
    # gates; task 9's caller is responsible for ORing them together.
    manager.record_delivery_success()
    manager.enable_notifications()
    assert manager.is_in_cooldown() is True
    assert manager.notifications_enabled() is True


def test_invalid_interval_does_not_corrupt_persisted_state(tmp_path):
    config = _config(tmp_path)
    first = CooldownManager(config)
    first.set_interval_minutes(90)

    with pytest.raises(CooldownConfigError):
        first.set_interval_minutes(9999)

    second = CooldownManager(config)
    assert second.get_interval_minutes() == 90


def test_atomic_write_leaves_no_leftover_tmp_files(tmp_path):
    config = _config(tmp_path)
    manager = CooldownManager(config)
    manager.set_interval_minutes(30)

    leftovers = list(tmp_path.glob(".cooldown-*"))
    assert leftovers == []
