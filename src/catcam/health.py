"""Aggregated health status: the single source of truth for `/status`
(task 8) and `scripts/diagnose.sh` (task 10).

`get_status()` pulls a live snapshot from camera, streaming, cooldown, and
storage state; `format_status()` renders it as the human-readable text
`/status` sends. Kept as two functions (data, then formatting) so callers
that want the raw numbers (e.g. a future `scripts/diagnose.sh` JSON mode)
don't have to parse text back out.
"""

import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .camera import create_camera
from .config import AppConfig, ConfigError, load_config
from .cooldown import CooldownManager
from .storage import StorageManager
from .stream_health import DEFAULT_API_URL, check_mediamtx_path

# Below this much free disk space, `scripts/diagnose.sh` flags a failure even
# if CatCam's own quota isn't exceeded yet - the OS itself (logs, apt, etc.)
# also needs headroom on a Pi's SD card/SSD.
_LOW_DISK_FREE_MB_THRESHOLD = 500


@dataclass
class HealthStatus:
    camera_type: str
    camera_available: bool
    stream_ready: bool
    stream_error: Optional[str]
    last_motion_at: Optional[float]
    cooldown_minutes: int
    in_cooldown: bool
    notifications_enabled: bool
    storage_used_mb: float
    disk_quota_mb: int
    disk_free_mb: float


def _existing_ancestor(path: Path) -> Path:
    """Walk up from `path` to the nearest ancestor that actually exists.

    `shutil.disk_usage` requires an existing path; storage directories may
    not have been created yet at health-check time (e.g. before the first
    recording).
    """
    path = path.resolve()
    while not path.exists():
        if path.parent == path:
            return Path(".").resolve()
        path = path.parent
    return path


def get_status(
    config: AppConfig,
    cooldown_manager: CooldownManager,
    storage_manager: StorageManager,
    last_motion_at: Optional[float] = None,
) -> HealthStatus:
    """Pull a live snapshot of camera/stream/cooldown/disk state.

    Non-blocking and safe to call frequently: `camera.is_available()` never
    opens/locks the device, and `check_mediamtx_path` is a single
    short-timeout HTTP call to MediaMTX's loopback-only control API.
    """
    camera_available = create_camera(config.camera).is_available()
    stream = check_mediamtx_path(DEFAULT_API_URL, config.streaming.path)
    disk_usage = shutil.disk_usage(_existing_ancestor(Path(config.recording.storage_dir)))

    return HealthStatus(
        camera_type=config.camera.type,
        camera_available=camera_available,
        stream_ready=stream.path_ready,
        stream_error=stream.error,
        last_motion_at=last_motion_at,
        cooldown_minutes=cooldown_manager.get_interval_minutes(),
        in_cooldown=cooldown_manager.is_in_cooldown(),
        notifications_enabled=cooldown_manager.notifications_enabled(),
        storage_used_mb=storage_manager.disk_usage_mb(),
        disk_quota_mb=config.recording.disk_quota_mb,
        disk_free_mb=disk_usage.free / (1024 * 1024),
    )


def format_status(status: HealthStatus) -> str:
    """Render `HealthStatus` as the text `/status` replies with."""
    if status.last_motion_at is None:
        motion_line = "no motion detected yet this run"
    else:
        elapsed = max(0.0, time.time() - status.last_motion_at)
        motion_line = f"last motion {elapsed:.0f}s ago"

    stream_line = "ready" if status.stream_ready else "not ready"
    if status.stream_error:
        stream_line += f" ({status.stream_error})"

    lines = [
        f"Camera ({status.camera_type}): "
        f"{'available' if status.camera_available else 'NOT DETECTED'}",
        f"Stream: {stream_line}",
        f"Motion detector: {motion_line}",
        (
            f"Cooldown: {status.cooldown_minutes} min "
            f"({'in cooldown' if status.in_cooldown else 'ready'})"
        ),
        f"Notifications: {'enabled' if status.notifications_enabled else 'disabled'}",
        (
            f"Storage used: {status.storage_used_mb:.1f} / {status.disk_quota_mb} MB "
            f"(disk free: {status.disk_free_mb:.0f} MB)"
        ),
    ]
    return "\n".join(lines)


def main() -> int:
    """`python -m catcam.health` - the storage/disk-quota check used by
    `scripts/diagnose.sh` (task 10). Prints the full status for context, but
    only fails the process on disk-related problems; camera/stream/FFmpeg are
    already covered by diagnose.sh's other, more specific checks.
    """
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 1

    cooldown_manager = CooldownManager(config.cooldown)
    storage_manager = StorageManager(config.recording)
    status = get_status(config, cooldown_manager, storage_manager)
    print(format_status(status))

    if status.storage_used_mb > status.disk_quota_mb:
        print(
            f"FAIL: storage usage ({status.storage_used_mb:.1f} MB) exceeds "
            f"quota ({status.disk_quota_mb} MB)",
            file=sys.stderr,
        )
        return 1
    if status.disk_free_mb < _LOW_DISK_FREE_MB_THRESHOLD:
        print(
            f"FAIL: only {status.disk_free_mb:.0f} MB free on disk "
            f"(threshold: {_LOW_DISK_FREE_MB_THRESHOLD} MB)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
