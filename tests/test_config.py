import textwrap

import pytest

from catcam.config import ConfigError, load_config

REQUIRED_ENV = {
    "TELEGRAM_BOT_TOKEN": "123456789:AAFabcDEFghijklmnopqrstuvwxyz012345",
    "TELEGRAM_USER_ID": "111111",
    "TELEGRAM_CHAT_ID": "222222",
}


@pytest.fixture
def missing_env_file(tmp_path):
    # A dotenv path that doesn't exist, so load_dotenv() is a no-op and only
    # the environment variables set explicitly in each test apply.
    return str(tmp_path / "does-not-exist.env")


def _write_yaml(tmp_path, content: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(content))
    return str(path)


def _set_required_env(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


def test_valid_config_loads(tmp_path, monkeypatch, missing_env_file):
    _set_required_env(monkeypatch)
    yaml_path = _write_yaml(
        tmp_path,
        """
        cooldown:
          default_minutes: 45
        motion:
          sensitivity: 40
        """,
    )

    config = load_config(env_path=missing_env_file, yaml_path=yaml_path)

    assert config.telegram.bot_token == REQUIRED_ENV["TELEGRAM_BOT_TOKEN"]
    assert config.telegram.user_id == 111111
    assert config.telegram.chat_id == 222222
    assert config.cooldown.default_minutes == 45
    assert config.motion.sensitivity == 40
    # Sections untouched by the test YAML still fall back to dataclass defaults.
    assert config.camera.type == "csi"
    assert config.streaming.bind_address == "0.0.0.0"
    assert config.streaming.path == "cam"


def test_missing_required_secret_raises(tmp_path, monkeypatch, missing_env_file):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_USER_ID", "111111")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "222222")
    yaml_path = _write_yaml(tmp_path, "{}")

    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config(env_path=missing_env_file, yaml_path=yaml_path)


def test_out_of_range_cooldown_raises(tmp_path, monkeypatch, missing_env_file):
    _set_required_env(monkeypatch)
    yaml_path = _write_yaml(
        tmp_path,
        """
        cooldown:
          default_minutes: 5000
        """,
    )

    with pytest.raises(ConfigError, match="cooldown.default_minutes"):
        load_config(env_path=missing_env_file, yaml_path=yaml_path)


def test_out_of_range_sensitivity_raises(tmp_path, monkeypatch, missing_env_file):
    _set_required_env(monkeypatch)
    yaml_path = _write_yaml(
        tmp_path,
        """
        motion:
          sensitivity: 150
        """,
    )

    with pytest.raises(ConfigError, match="motion.sensitivity"):
        load_config(env_path=missing_env_file, yaml_path=yaml_path)


def test_env_override_beats_yaml(tmp_path, monkeypatch, missing_env_file):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("CATCAM_COOLDOWN_DEFAULT_MINUTES", "30")
    yaml_path = _write_yaml(
        tmp_path,
        """
        cooldown:
          default_minutes: 90
        """,
    )

    config = load_config(env_path=missing_env_file, yaml_path=yaml_path)

    assert config.cooldown.default_minutes == 30
