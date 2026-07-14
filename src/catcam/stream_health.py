"""Streaming health check against MediaMTX's control API.

Queries `GET /v3/paths/get/{path}` on MediaMTX's (loopback-only) control API.
The "ready" field in that response is true only when the underlying source —
either MediaMTX's native `rpiCamera` source (CSI) or `stream_publisher.py`'s
ffmpeg process (USB) — is actually delivering frames, so a single HTTP call
covers both camera types and both "MediaMTX down" and "source not producing
frames" failure modes without a separate process-liveness check.

CLI entry point: `python -m catcam.stream_health` (PASS/FAIL, exit 0/1),
called by `scripts/diagnose.sh` and importable as a library function by
task 9's `health.py` aggregator.
"""

import json
import logging
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .config import ConfigError, load_config

logger = logging.getLogger("catcam.stream_health")

DEFAULT_API_URL = "http://127.0.0.1:9997"


@dataclass
class StreamHealth:
    api_reachable: bool
    path_ready: bool
    error: Optional[str] = None


def check_mediamtx_path(
    api_url: str, path: str, timeout: float = 3.0
) -> StreamHealth:
    """Check whether MediaMTX's `path` is up and receiving frames."""
    url = f"{api_url.rstrip('/')}/v3/paths/get/{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return StreamHealth(api_reachable=False, path_ready=False, error=str(exc))
    except ValueError as exc:
        return StreamHealth(
            api_reachable=True,
            path_ready=False,
            error=f"Could not parse MediaMTX API response: {exc}",
        )

    ready = bool(data.get("ready", False))
    return StreamHealth(api_reachable=True, path_ready=ready)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        config = load_config()
        path = config.streaming.path
    except ConfigError as exc:
        logger.error("Invalid configuration: %s", exc)
        return 1

    health = check_mediamtx_path(DEFAULT_API_URL, path)

    if not health.api_reachable:
        print(f"FAIL: MediaMTX control API unreachable at {DEFAULT_API_URL} ({health.error})")
        return 1
    if not health.path_ready:
        print(f"FAIL: MediaMTX path '{path}' is not ready (no frames flowing)")
        return 1

    print(f"PASS: MediaMTX path '{path}' is ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
