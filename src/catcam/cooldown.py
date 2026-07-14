"""Delivery cooldown / rate limiting.

`CooldownManager` persists cooldown state (last successful delivery time,
current interval, notifications on/off) to a small local JSON file rather
than a database — this project has a single owner and a single device, so a
file is simpler and has no extra dependency. Writes are atomic (temp file +
`os.replace`) so a power loss on a Raspberry Pi with no UPS can't leave a
half-written, corrupt state file.

The cooldown timer only resets on a *confirmed* successful delivery
(`record_delivery_success()`), not on a send attempt — task 9's retry queue
is what decides when a delivery counts as successful.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from .config import CooldownConfig

logger = logging.getLogger("catcam.cooldown")


class CooldownConfigError(ValueError):
    """Raised when an interval value is outside the configured valid range."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CooldownManager:
    """Tracks delivery cooldown state, persisted across restarts."""

    def __init__(
        self,
        config: CooldownConfig,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self._config = config
        self._clock = clock or _utc_now
        self._state_path = Path(config.state_file)

        self._last_delivery_at: Optional[datetime] = None
        self._interval_minutes: int = config.default_minutes
        self._notifications_enabled: bool = True

        self._load()

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            with self._state_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error(
                "Could not read cooldown state file '%s': %s - using defaults",
                self._state_path,
                exc,
            )
            return

        last_delivery_raw = data.get("last_delivery_at")
        if last_delivery_raw:
            try:
                self._last_delivery_at = datetime.fromisoformat(last_delivery_raw)
            except ValueError:
                logger.error(
                    "Invalid last_delivery_at %r in '%s' - ignoring",
                    last_delivery_raw,
                    self._state_path,
                )
        self._interval_minutes = data.get("interval_minutes", self._interval_minutes)
        self._notifications_enabled = data.get(
            "notifications_enabled", self._notifications_enabled
        )

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "last_delivery_at": (
                    self._last_delivery_at.isoformat() if self._last_delivery_at else None
                ),
                "interval_minutes": self._interval_minutes,
                "notifications_enabled": self._notifications_enabled,
            }
            fd, tmp_path = tempfile.mkstemp(
                dir=self._state_path.parent, prefix=".cooldown-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                os.replace(tmp_path, self._state_path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.error("Could not persist cooldown state to '%s': %s", self._state_path, exc)

    def is_in_cooldown(self) -> bool:
        if self._last_delivery_at is None:
            return False
        elapsed = self._clock() - self._last_delivery_at
        return elapsed < timedelta(minutes=self._interval_minutes)

    def record_delivery_success(self) -> None:
        self._last_delivery_at = self._clock()
        self._save()
        logger.info("Recorded successful delivery at %s", self._last_delivery_at.isoformat())

    def get_interval_minutes(self) -> int:
        return self._interval_minutes

    def set_interval_minutes(self, value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool):
            raise CooldownConfigError(f"Cooldown interval must be an integer, got {value!r}")
        if not (self._config.min_minutes <= value <= self._config.max_minutes):
            raise CooldownConfigError(
                f"Cooldown interval must be between {self._config.min_minutes} and "
                f"{self._config.max_minutes} minutes, got {value}"
            )
        self._interval_minutes = value
        self._save()
        logger.info("Cooldown interval set to %d minutes", value)

    def notifications_enabled(self) -> bool:
        return self._notifications_enabled

    def enable_notifications(self) -> None:
        self._notifications_enabled = True
        self._save()
        logger.info("Notifications enabled")

    def disable_notifications(self) -> None:
        self._notifications_enabled = False
        self._save()
        logger.info("Notifications disabled")
