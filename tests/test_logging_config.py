import json
import logging
import logging.handlers

from catcam.config import LoggingConfig
from catcam.logging_config import redact, setup_logging

_FAKE_TOKEN = "123456789:AAtestFAKEtokenNOTrealNOTrealNOTreal12"


def _config(tmp_path) -> LoggingConfig:
    return LoggingConfig(
        level="INFO",
        file=str(tmp_path / "catcam.log"),
        max_bytes=1_000_000,
        backup_count=3,
    )


def test_redact_scrubs_bare_token():
    text = f"using token {_FAKE_TOKEN} to connect"
    assert _FAKE_TOKEN not in redact(text)
    assert "<redacted-token>" in redact(text)


def test_redact_scrubs_token_embedded_in_url():
    url = f"https://api.telegram.org/bot{_FAKE_TOKEN}/sendMessage"
    assert _FAKE_TOKEN not in redact(url)


def test_redact_leaves_normal_text_untouched():
    assert redact("motion event at bbox=(1,2,3,4)") == "motion event at bbox=(1,2,3,4)"


def test_setup_logging_writes_json_lines(tmp_path):
    config = _config(tmp_path)
    setup_logging(config)

    logger = logging.getLogger("catcam.some_module")
    logger.info("hello %s", "world")

    log_file = tmp_path / "catcam.log"
    lines = [line for line in log_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["message"] == "hello world"
    assert record["level"] == "INFO"
    assert record["logger"] == "catcam.some_module"
    assert "timestamp" in record


def test_setup_logging_never_writes_the_bot_token(tmp_path):
    config = _config(tmp_path)
    setup_logging(config)

    logger = logging.getLogger("catcam.some_module")
    logger.error("failed request with token %s", _FAKE_TOKEN)

    log_file = tmp_path / "catcam.log"
    contents = log_file.read_text()
    assert _FAKE_TOKEN not in contents
    assert "<redacted-token>" in contents


def test_setup_logging_configures_rotation_from_config(tmp_path):
    config = _config(tmp_path)
    setup_logging(config)

    root = logging.getLogger("catcam")
    file_handlers = [
        h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    handler = file_handlers[0]
    assert handler.maxBytes == config.max_bytes
    assert handler.backupCount == config.backup_count


def test_setup_logging_respects_configured_level(tmp_path):
    config = _config(tmp_path)
    config.level = "WARNING"
    setup_logging(config)

    logger = logging.getLogger("catcam.some_module")
    logger.info("this should not be written")

    log_file = tmp_path / "catcam.log"
    assert log_file.read_text().strip() == ""
