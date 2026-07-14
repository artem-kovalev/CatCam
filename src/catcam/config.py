"""Configuration loading and validation for CatCam.

Precedence (low to high): config/config.yaml (or config.example.yaml if the
real file doesn't exist yet) provides structural defaults -> .env supplies
secrets and environment-specific overrides -> real process environment
variables win over both. See docs/configuration.md for the full key list.
"""

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when configuration is missing, malformed, or out of range."""


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_YAML_PATH = _REPO_ROOT / "config" / "config.yaml"
_EXAMPLE_YAML_PATH = _REPO_ROOT / "config" / "config.example.yaml"

_SCALAR_TYPES = (int, float, bool, str)


@dataclass
class CameraConfig:
    type: str = "csi"
    device: str = "/dev/video0"
    resolution: List[int] = field(default_factory=lambda: [1280, 720])
    framerate: int = 15


@dataclass
class MotionConfig:
    sensitivity: int = 25
    min_duration_seconds: float = 1.5
    roi: Optional[List[int]] = None


@dataclass
class RecordingConfig:
    clip_duration_seconds: int = 10
    pre_roll_seconds: int = 2
    max_clip_size_mb: int = 50
    storage_dir: str = "storage/recordings"
    pending_dir: str = "storage/pending"
    disk_quota_mb: int = 2048


@dataclass
class VideoNoteConfig:
    size_px: int = 384
    max_duration_seconds: int = 60
    max_size_mb: int = 8
    codec: str = "libx264"
    crf: int = 28


@dataclass
class CooldownConfig:
    default_minutes: int = 60
    min_minutes: int = 1
    max_minutes: int = 1440
    state_file: str = "storage/state/cooldown.json"


@dataclass
class StreamingConfig:
    # Informational only (used to build connect-URLs for docs/health output).
    # MediaMTX's actual listener bind addresses are set in
    # deploy/mediamtx/mediamtx.yml, not here — see docs/streaming.md for why
    # each protocol is bound wide and gated by MediaMTX's own auth instead.
    bind_address: str = "0.0.0.0"
    webrtc_port: int = 8889
    hls_port: int = 8888
    rtsp_port: int = 8554
    # MediaMTX path name serving the camera feed (rtsp://<host>:<rtsp_port>/<path>).
    path: str = "cam"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "storage/logs/catcam.log"
    max_bytes: int = 10_485_760
    backup_count: int = 5


@dataclass
class TelegramConfig:
    bot_token: str
    user_id: int
    chat_id: int


@dataclass
class AppConfig:
    camera: CameraConfig
    motion: MotionConfig
    recording: RecordingConfig
    video_note: VideoNoteConfig
    cooldown: CooldownConfig
    streaming: StreamingConfig
    logging: LoggingConfig
    telegram: TelegramConfig


def _coerce(raw: str, target_type: type) -> Any:
    if target_type is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if target_type is int:
        return int(raw)
    if target_type is float:
        return float(raw)
    return raw


def _build_section(section_name: str, dc_type: type, raw: Dict[str, Any]) -> Any:
    values = dict(raw or {})
    fields_by_name = {f.name: f for f in dataclasses.fields(dc_type)}

    unknown = set(values) - set(fields_by_name)
    if unknown:
        raise ConfigError(f"Unknown key(s) in '{section_name}' config: {sorted(unknown)}")

    for name, f in fields_by_name.items():
        env_name = f"CATCAM_{section_name.upper()}_{name.upper()}"
        raw_env = os.environ.get(env_name)
        if raw_env is None:
            continue
        if f.type not in _SCALAR_TYPES:
            raise ConfigError(
                f"Environment override {env_name} is not supported for non-scalar field '{name}'"
            )
        try:
            values[name] = _coerce(raw_env, f.type)
        except ValueError as exc:
            raise ConfigError(f"Invalid value for {env_name}: {raw_env!r}") from exc

    try:
        return dc_type(**values)
    except TypeError as exc:
        raise ConfigError(f"Invalid '{section_name}' config: {exc}") from exc


def _resolve_yaml_path(yaml_path: Optional[str]) -> Path:
    if yaml_path:
        return Path(yaml_path)
    env_override = os.environ.get("CATCAM_CONFIG_PATH")
    if env_override:
        return Path(env_override)
    if _DEFAULT_YAML_PATH.exists():
        return _DEFAULT_YAML_PATH
    return _EXAMPLE_YAML_PATH


def _load_yaml(yaml_path: Optional[str]) -> Dict[str, Any]:
    path = _resolve_yaml_path(yaml_path)
    if not path.exists():
        raise ConfigError(f"Config YAML not found at {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config YAML at {path} must be a mapping at the top level")
    return data


def _build_telegram_config() -> TelegramConfig:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    user_id_raw = os.environ.get("TELEGRAM_USER_ID", "").strip()
    chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_USER_ID", user_id_raw),
            ("TELEGRAM_CHAT_ID", chat_id_raw),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            f"Missing required environment variable(s): {', '.join(missing)}"
        )

    try:
        user_id = int(user_id_raw)
    except ValueError as exc:
        raise ConfigError("TELEGRAM_USER_ID must be an integer") from exc
    try:
        chat_id = int(chat_id_raw)
    except ValueError as exc:
        raise ConfigError("TELEGRAM_CHAT_ID must be an integer") from exc

    return TelegramConfig(bot_token=token, user_id=user_id, chat_id=chat_id)


def _validate(config: AppConfig) -> None:
    if not (0 <= config.motion.sensitivity <= 100):
        raise ConfigError("motion.sensitivity must be between 0 and 100")
    if config.motion.min_duration_seconds <= 0:
        raise ConfigError("motion.min_duration_seconds must be positive")

    cd = config.cooldown
    if not (1 <= cd.min_minutes <= 1440):
        raise ConfigError("cooldown.min_minutes must be between 1 and 1440")
    if not (1 <= cd.max_minutes <= 1440):
        raise ConfigError("cooldown.max_minutes must be between 1 and 1440")
    if cd.min_minutes > cd.max_minutes:
        raise ConfigError("cooldown.min_minutes must not exceed cooldown.max_minutes")
    if not (cd.min_minutes <= cd.default_minutes <= cd.max_minutes):
        raise ConfigError(
            f"cooldown.default_minutes ({cd.default_minutes}) must be within "
            f"[{cd.min_minutes}, {cd.max_minutes}]"
        )

    if config.recording.clip_duration_seconds <= 0:
        raise ConfigError("recording.clip_duration_seconds must be positive")
    if config.recording.disk_quota_mb <= 0:
        raise ConfigError("recording.disk_quota_mb must be positive")

    if config.camera.type not in ("csi", "usb"):
        raise ConfigError("camera.type must be 'csi' or 'usb'")

    if config.video_note.size_px <= 0:
        raise ConfigError("video_note.size_px must be positive")


def load_config(
    env_path: Optional[str] = None, yaml_path: Optional[str] = None
) -> AppConfig:
    """Load and validate the full application configuration.

    Raises ConfigError on any missing required secret or out-of-range value —
    there are no silent defaults for secrets.
    """
    load_dotenv(dotenv_path=env_path, override=False)
    yaml_data = _load_yaml(yaml_path)

    config = AppConfig(
        camera=_build_section("camera", CameraConfig, yaml_data.get("camera", {})),
        motion=_build_section("motion", MotionConfig, yaml_data.get("motion", {})),
        recording=_build_section(
            "recording", RecordingConfig, yaml_data.get("recording", {})
        ),
        video_note=_build_section(
            "video_note", VideoNoteConfig, yaml_data.get("video_note", {})
        ),
        cooldown=_build_section(
            "cooldown", CooldownConfig, yaml_data.get("cooldown", {})
        ),
        streaming=_build_section(
            "streaming", StreamingConfig, yaml_data.get("streaming", {})
        ),
        logging=_build_section("logging", LoggingConfig, yaml_data.get("logging", {})),
        telegram=_build_telegram_config(),
    )
    _validate(config)
    return config
