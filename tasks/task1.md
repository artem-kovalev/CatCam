# Task 1 — Repository scaffolding & configuration foundation

## Status: Done

## Goal

Establish the repository skeleton and the configuration layer (`.env` + YAML) that every later task depends on: directory structure, packaging files, base documentation stubs, and `src/catcam/config.py` with validation.

## Depends on

None (first task).

## Spec references

- "Non-Functional Requirements" (Python 3, `.env`/YAML config, `.env.example`, no real secrets, type hints/error handling).
- "Recommended Repository Structure" (full tree).
- "Private Telegram Bot" → token/secret storage rules (`.env` only, never committed, never logged).

## Assumptions

- Python 3.11+ target (current Raspberry Pi OS Bookworm ships Python 3.11).
- Packaging via `pyproject.toml` (PEP 621) with `requirements.txt` kept in sync for environments without build-tool support (e.g. plain `pip install -r requirements.txt` on-device).
- Config precedence: `config/config.yaml` (or `config.example.yaml` if none present) provides structural/default values; `.env` provides secrets and environment-specific overrides; environment variables override both. This will be documented in `docs/configuration.md`.
- License: MIT, unless the user specifies otherwise later (private/personal project, permissive default; can be changed with no code impact).

## Steps

1. Create directories: `src/catcam/`, `config/`, `scripts/`, `deploy/systemd/`, `deploy/mediamtx/`, `tests/`, `docs/`.
2. Add `.gitignore` covering: `.env`, `__pycache__/`, `*.pyc`, `.venv/`, `config/config.yaml` (real config, as opposed to the example), recordings/temp media output dirs, `*.log`.
3. Add `.env.example` listing every secret/env var referenced anywhere in the spec: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`, `TELEGRAM_CHAT_ID`, `CATCAM_CONFIG_PATH` (optional override), with comments — no real values.
4. Add `config/config.example.yaml` with structural (non-secret) defaults: camera settings (type: `csi`|`usb`, device path/index), motion detection (sensitivity, ROI, min duration), recording (clip duration, pre-roll, max size, storage dir, disk quota), video note (crop/scale/codec params), cooldown (default 60, min/max), streaming (MediaMTX bind address, ports), logging (level, rotation size/count).
5. Add `pyproject.toml` (project metadata, dependencies: `python-telegram-bot` or `pyTelegramBotAPI`, `opencv-python-headless`, `pyyaml`, `python-dotenv`, `psutil` for disk stats) and mirrored `requirements.txt`.
6. Add `LICENSE` (MIT) and a `README.md` skeleton (project description, links into `docs/`, quick-start placeholder to be filled by task 10/11).
7. Implement `src/catcam/__init__.py` and `src/catcam/config.py`:
   - Typed dataclasses (or `pydantic` models) for `CameraConfig`, `MotionConfig`, `RecordingConfig`, `VideoNoteConfig`, `CooldownConfig`, `StreamingConfig`, `TelegramConfig`, `LoggingConfig`, and a top-level `AppConfig`.
   - `load_config(env_path: str | None, yaml_path: str | None) -> AppConfig` that reads `.env` via `python-dotenv`, reads YAML, merges with env-var overrides, and validates (e.g. cooldown in 1–1440, sensitivity in valid range, required Telegram fields present and correctly typed as ints).
   - Raise a clear, specific exception (e.g. `ConfigError`) on invalid/missing required values — no silent defaults for secrets.
8. Start `src/catcam/logging_config.py` only to the extent needed so `config.py` can log non-secret startup info without leaking tokens (full implementation is task 9); at minimum define a helper that redacts token-like values before logging.
9. Add `docs/configuration.md` documenting every config key, its source (`.env` vs YAML), default, valid range, and precedence rules.
10. Write `tests/test_config.py`: valid config loads correctly; missing required secret raises `ConfigError`; out-of-range cooldown/sensitivity raises `ConfigError`; env-var override takes precedence over YAML.

## Acceptance criteria

- [x] Repository tree matches (or documents deviation from) the structure in `AGENT_PROMPT_EN.md`.
- [x] `.env.example` exists, contains no real secrets, and covers every secret used later in the project.
- [x] `config/config.example.yaml` exists and covers every non-secret setting referenced by later tasks.
- [x] `config.py` loads and validates configuration with type hints and explicit error handling; invalid config fails fast with a clear message.
- [x] `tests/test_config.py` passes.
- [x] `docs/configuration.md` documents all keys with no unexplained variables.
- [x] `.gitignore` prevents `.env` and any real secret file from being committed.

## Result

Implemented. Directory skeleton, packaging, config layer, docs, and tests are
all in place and verified locally (no Raspberry Pi hardware involved in this
task).

- Created files:
  - `.gitignore`
  - `.env.example`
  - `config/config.example.yaml`
  - `pyproject.toml`
  - `requirements.txt`
  - `LICENSE`
  - `README.md`
  - `src/catcam/__init__.py`
  - `src/catcam/config.py`
  - `src/catcam/logging_config.py` (redaction helper only; full setup is task 9)
  - `docs/configuration.md`
  - `tests/test_config.py`
  - Empty directories created for later tasks: `config/`, `scripts/`, `deploy/systemd/`, `deploy/mediamtx/`, `tests/`, `docs/`
- Modified files: none (first implementation task; nothing pre-existed besides `AGENT_PROMPT_EN.md` and `tasks/`).
- Commands executed:
  - `python3 -m venv .venv && pip install pyyaml python-dotenv pytest` (local dev dependencies for verification; `python-telegram-bot`, `opencv-python-headless`, `psutil` are declared in `requirements.txt`/`pyproject.toml` for later tasks but weren't needed to test the config loader itself)
  - `python -m pytest tests/test_config.py -v` → 5 passed
  - Manual smoke test: `cp .env.example .env` + `cp config/config.example.yaml config/config.yaml` with dummy Telegram values, `python -c "from catcam.config import load_config; print(load_config())"` → loaded successfully with all sections populated; both files then deleted afterward (not committed, per `.gitignore`)
- Test results: `tests/test_config.py` — 5/5 passed (valid load; missing required secret → `ConfigError`; out-of-range `cooldown.default_minutes` → `ConfigError`; out-of-range `motion.sensitivity` → `ConfigError`; `CATCAM_COOLDOWN_DEFAULT_MINUTES` env override beats YAML value).
- Unresolved questions:
  - Local Python is 3.14 (repo targets 3.11+ per Raspberry Pi OS Bookworm); `python-telegram-bot`/`opencv-python-headless`/`psutil` weren't installed/verified in this task since task 1 only needed `pyyaml`/`python-dotenv`/`pytest` — their compatibility with the actual target Python (3.11 on-device) should be (re)verified in task 2 onward when camera/bot code is written.
  - `motion.sensitivity` valid range (0–100) and its exact relationship to OpenCV's `createBackgroundSubtractorMOG2` parameters is an assumption to be refined in task 4 when the motion detector is implemented; the range itself is not expected to change.
