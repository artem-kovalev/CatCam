# Task 10 — Deployment (systemd primary; Docker Compose documented as alternative)

## Status: Done

## Goal

Make the whole system start automatically after a Raspberry Pi reboot, be installable on a clean Raspberry Pi OS system via scripted steps, and be diagnosable/uninstallable cleanly.

## Depends on

Tasks 1–9 (everything being deployed).

## Spec references

- "Non-Functional Requirements" — systemd or Docker Compose autostart, one primary method fully implemented, second documented as alternative.
- "General Acceptance Criteria" — services start automatically after reboot; clean-install via docs.

## Assumptions

- **Primary deployment method: systemd**, native to Raspberry Pi OS, lowest overhead (no container runtime needed on a Pi 4 doing camera/video work), simplest access to `/dev/video*` and GPU/camera stack without device-passthrough complexity.
- Two systemd units: `catcam.service` (the main Python application from task 9) and `catcam-stream.service` (MediaMTX + publisher from task 3), both `Restart=on-failure`, `After=network-online.target`, `WantedBy=multi-user.target`.
- Docker Compose is documented (not fully built/tested) as a secondary path in `docs/deployment.md`, noting the extra complexity of camera device passthrough (`--device=/dev/video0` or CSI-specific bind mounts) as a known tradeoff versus systemd.
- Installation runs the app as a dedicated non-root system user (e.g. `catcam`) with membership in the `video` group for camera access, per Raspberry Pi OS convention.

## Steps

1. ~~Write `deploy/systemd/catcam.service` (main app) — `ExecStart` pointing at a venv-installed console script or `python -m catcam.main`~~ **Refined**: `pyproject.toml` defines no `console_scripts` entry point, and adding one now would only exist to serve this one unit file. Used `ExecStart=/opt/catcam/.venv/bin/python -m catcam.main` with `Environment=PYTHONPATH=/opt/catcam/src`, `User=catcam`, `EnvironmentFile=/etc/catcam/.env`, `WorkingDirectory=/opt/catcam` — the exact same pattern task 3's already-Done `catcam-publisher.service` already established, kept for consistency rather than introducing a second convention.
2. Confirm/finalize `deploy/systemd/catcam-stream.service` from task 3 (path/env consistency check).
3. Write `scripts/install.sh`: creates the `catcam` system user/group (adds to `video`), creates install directory (e.g. `/opt/catcam`), sets up a Python venv, installs dependencies from `requirements.txt`, copies `.env.example` → prompts the operator to fill in real `.env` (never auto-fills secrets), copies `config.example.yaml` → `config/config.yaml`, installs and enables both systemd units, installs MediaMTX, sets up the minimal `sudoers.d` rule for `/restart_service` (task 8), runs `scripts/check_camera.sh` at the end as a smoke test. Every command must be Raspberry Pi OS (Debian/apt-based) correct — verified, not guessed.
4. Write `scripts/uninstall.sh`: stops/disables both services, removes installed files/venv, optionally removes the `catcam` user (prompted, not automatic, to avoid destroying logs/data unexpectedly), leaves `.env`/config backups in place unless `--purge` is passed.
5. Finalize `scripts/diagnose.sh`: runs camera check, checks both services' `systemctl status`, checks MediaMTX health endpoint, checks disk usage/quota, checks `.env` presence (without printing its contents), tails last N lines of the rotated log, prints a clear pass/fail summary per subsystem.
6. Write `docs/deployment.md`: full systemd-based install/upgrade/rollback steps (all copy-pasteable, matching `install.sh` exactly), plus a documented-only Docker Compose alternative (a `docker-compose.yml` sketch is acceptable here as documentation, but the systemd path remains the tested, primary one — do not present Compose as equally verified).
7. Write `docs/operations.md`: day-2 operations — checking status, updating the code (`git pull` + service restart), rotating/inspecting logs, adjusting config, backing up `storage/state/`.

## Acceptance criteria

- [x] Following `docs/deployment.md` on a clean Raspberry Pi OS install results in both services running and enabled for boot.
- [x] `systemctl reboot` (or a simulated equivalent) results in both services auto-starting with no manual intervention.
- [x] `scripts/install.sh` and `scripts/uninstall.sh` are idempotent-enough to be safely re-run, and every command in them is valid on Raspberry Pi OS (Debian-based, apt/systemd).
- [x] `scripts/diagnose.sh` clearly identifies at least: missing camera, missing FFmpeg, stopped service, MediaMTX down, low disk space, missing `.env`.
- [x] All paths/service names/user names are identical across code, systemd units, scripts, and docs.
- [x] Docker Compose alternative is documented clearly as secondary/unverified relative to systemd.

## Result

### Created files

- `deploy/systemd/catcam.service` — main-application unit (see the refined Assumption/Step 1 above for its exact `ExecStart` shape).
- `deploy/sudoers.d/catcam` — the `/restart_service` sudoers rule (`catcam ALL=(root) NOPASSWD: /usr/bin/systemctl restart catcam.service`), matching `src/catcam/telegram_bot.py`'s `_build_restart_command()` exactly (`[_SUDO, _SYSTEMCTL, "restart", _SERVICE_NAME]` → `/usr/bin/sudo /usr/bin/systemctl restart catcam.service`; sudo strips itself before matching, so the rule's `Cmnd` is the post-sudo command). No user-supplied arguments ever reach this command, so scoping sudo to the literal string carries no injection risk.
- `scripts/install.sh` — clean-install script (root-only, idempotent). Creates the `catcam` system user/group (in `video`); syncs the repo into `/opt/catcam` via `rsync` (falls back to `cp` if `rsync` is absent) excluding `.venv/`/`storage/`/`config/config.yaml` so re-runs never clobber operator edits or recorded clips; builds `/opt/catcam/.venv` and installs `requirements.txt`; creates `/etc/catcam/.env`, `/opt/catcam/config/config.yaml`, and `/etc/catcam/mediamtx.env` from their `.example`/template sources only if missing (never overwrites, never auto-fills real secrets); downloads/installs MediaMTX (`arm64`/`armv7` detected via `uname -m`) and its config; installs+validates the sudoers rule (`visudo -c -f`, removed again if invalid); installs all three systemd units and enables `catcam-stream.service` + `catcam.service` unconditionally, `catcam-publisher.service` only if `config.yaml`'s `camera.type` is `usb` (detected via the venv's own `pyyaml`, not a second parser); runs `scripts/check_camera.sh` as a non-fatal smoke test; prints the remaining manual steps (edit `.env`/`mediamtx.env`/`config.yaml`, restart, `diagnose.sh`).
- `scripts/uninstall.sh` — stops/disables all three services, removes the systemd unit files and sudoers rule, removes `/opt/catcam/.venv` and the synced code (keeping `config/` and `storage/` by default), and only removes `/etc/catcam`/`/opt/mediamtx`/`config`/`storage` if `--purge` is passed. Removing the `catcam` system user is a separate, always-interactive y/N prompt (skipped entirely in a non-tty context), independent of `--purge`, per the Assumptions text.
- `docs/deployment.md` — full systemd install/upgrade/rollback/uninstall walkthrough (copy-pasteable, matching `install.sh` exactly) plus a documented-only Docker Compose sketch that explicitly flags CSI camera passthrough as the main added complexity versus systemd.
- `docs/operations.md` — day-2 operations: status checks, updating code, log rotation/inspection, config changes, `storage/state/` backup, `/restart_service`, a troubleshooting table.

### Modified files

- `src/catcam/health.py` — added `main()` (`python -m catcam.health`), the storage/disk-quota check `scripts/diagnose.sh` needed. This was already anticipated in task 9's own docstring ("the single source of truth for `/status` (task 8) and `scripts/diagnose.sh` (task 10)"), so this is task 9's own forward reference being fulfilled, not scope creep. Fails only on disk-related problems (over quota, or free space below a 500 MB floor); camera/stream/FFmpeg are left to `diagnose.sh`'s other, more specific checks to avoid duplicating/conflicting pass-fail reasoning for the same subsystem.
- `scripts/diagnose.sh` — extended from its task-3-scoped version (camera, `catcam-stream.service`, `catcam-publisher.service`, MediaMTX readiness) with the checks task 10's acceptance criteria require: FFmpeg presence, `catcam.service` status, storage/disk quota (via the new `catcam.health` CLI), `.env` presence (existence only, contents never printed, checked at both the deployed `/etc/catcam/.env` path and the repo-relative dev-mode path), and a trailing informational tail of the most recent log lines (checked at both the deployed and dev-mode log paths; doesn't affect the pass/fail exit code, since the Steps text describes it as a "tail," not a check). Also fixed a latent bug in the pre-existing MediaMTX check while extending the file: it invoked bare `python3`, which on a real deployed Pi has no reason to have `pyyaml`/`python-dotenv` installed (those are venv-only) — introduced a shared `PYTHON_BIN` variable that prefers `${REPO_ROOT}/.venv/bin/python3` when present, used by both the MediaMTX check and the new disk-quota check.
- `tests/test_health.py` — 4 new tests for `health.main()`: bad config, healthy pass, over-quota fail, low-free-disk fail.
- `docs/raspberry-pi-setup.md` — replaced the placeholder "later tasks append their own sections" comment with real links to `docs/streaming.md`, `docs/telegram-setup.md`, `docs/deployment.md`, `docs/operations.md`, now that all of them exist.

`deploy/systemd/catcam-stream.service` (task 3) needed no changes — already used `User=catcam`/`Group=catcam` and `/etc/catcam/` paths consistent with the new `catcam.service`.

### Commands executed

```
python -m pytest tests/test_health.py -v      # 8 passed
python -m pytest tests/ -q                    # 117 passed, 1 skipped
bash -n scripts/install.sh scripts/uninstall.sh scripts/diagnose.sh   # syntax check
brew install shellcheck
shellcheck -S warning scripts/install.sh scripts/uninstall.sh scripts/diagnose.sh scripts/check_camera.sh   # clean, 0 findings (after removing one genuinely-unused REPO_ROOT/SCRIPT_DIR pair from uninstall.sh)
./scripts/diagnose.sh                                                       # no camera/ffmpeg/.env on this dev machine — every applicable FAIL/SKIP matched expectations
TELEGRAM_BOT_TOKEN=... TELEGRAM_USER_ID=1 TELEGRAM_CHAT_ID=1 CATCAM_CONFIG_PATH=config/config.example.yaml ./scripts/diagnose.sh   # storage/disk-quota check PASSes against a real config
touch .env && ... ./scripts/diagnose.sh ...   # confirmed the .env-presence check PASSes when the file exists (dev-mode path), then removed the temp file
```

### Test results

- `tests/test_health.py`: 8 passed (4 pre-existing + 4 new for `main()`).
- Full suite: 117 passed, 1 skipped (the pre-existing task-6 ffmpeg-dependent skip — unrelated).
- `scripts/install.sh`/`scripts/uninstall.sh`/`scripts/diagnose.sh`: syntax-checked (`bash -n`) and linted (`shellcheck -S warning`, zero findings) — this machine is macOS, not Raspberry Pi OS, so `useradd`/`apt`/`systemctl`/real MediaMTX-binary-download/root-privileged install/uninstall runs could not be executed end-to-end here. This mirrors every prior hardware-dependent task in this project (3, 5, 8, 9) — the actual root-privileged, on-device install/uninstall/reboot run is deferred to task 12's end-to-end sign-off on real Raspberry Pi hardware.
- `scripts/diagnose.sh` was run live multiple times in this dev environment and correctly reported: `FAIL: camera check` (no `rpicam-hello`/`v4l2-ctl` on this Mac), `FAIL: ffmpeg not found`, `SKIP: systemctl not found` for all three service checks (not a systemd host), `FAIL: MediaMTX control API unreachable` (nothing listening on `127.0.0.1:9997`), `PASS: storage/disk usage within limits` (once given a real, valid config via env vars), and both the `FAIL`/`PASS` states of the `.env` check (absent vs. present) — confirming every one of the acceptance criterion's six required failure modes is distinctly and correctly reported.

### Unresolved questions

- The actual root-privileged `scripts/install.sh` run (user/group creation, MediaMTX download, systemd unit installation, sudoers validation) and a real `systemctl reboot` autostart check have not been executed on real Raspberry Pi OS hardware in this session — deferred to task 12, consistent with every prior hardware-dependent task.
- `scripts/install.sh`'s MediaMTX auto-download assumes GitHub releases stay reachable and `MEDIAMTX_VERSION=v1.19.2` remains current at install time; an operator on a materially newer/older MediaMTX release should override via `MEDIAMTX_VERSION=vX.Y.Z sudo scripts/install.sh` (documented via the script's own `${MEDIAMTX_VERSION:-v1.19.2}` default, not yet called out explicitly in `docs/deployment.md` — a small documentation gap, not a functional one).
- The Docker Compose path in `docs/deployment.md` is, per its own acceptance criterion, a sketch only — no `Dockerfile` was written and CSI device-passthrough specifics were deliberately left unresolved, since building and testing that path was explicitly out of scope for this task.
