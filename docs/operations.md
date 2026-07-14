# Operations (day 2)

Assumes a systemd install per `docs/deployment.md`, with the app at
`/opt/catcam`, secrets at `/etc/catcam/`, and MediaMTX at `/opt/mediamtx`.

## Checking status

```bash
scripts/diagnose.sh
```

Runs every check in one pass: camera detected, FFmpeg present,
`catcam-stream.service`/`catcam-publisher.service`/`catcam.service` active,
MediaMTX stream `ready`, storage within its disk quota, `.env` present, and a
tail of the most recent log lines. Exits non-zero if anything failed.

For a single subsystem:

```bash
systemctl status catcam.service
systemctl status catcam-stream.service
journalctl -u catcam.service -f              # follow live
```

From Telegram, `/status` reports the same camera/stream/cooldown/storage
picture from inside a running process (see `docs/telegram-setup.md`).

## Updating the code

```bash
cd /path/to/your/catcam-src && git pull
sudo scripts/install.sh
sudo systemctl restart catcam.service catcam-stream.service
scripts/diagnose.sh
```

`install.sh` is idempotent and never touches `/etc/catcam/.env`,
`config.yaml`, `mediamtx.yml`/`mediamtx.env`, or `storage/` — only code and
the venv are refreshed.

## Rotating and inspecting logs

Logs are JSON-lines at `storage/logs/catcam.log` (see `config/config.yaml`'s
`logging` section for path/size/backup-count), auto-rotated by
`logging.handlers.RotatingFileHandler` — no external logrotate config is
needed. Every record is redacted of anything token-shaped before being
written (see `docs/architecture.md`'s "Logging" section).

```bash
tail -n 50 /opt/catcam/storage/logs/catcam.log
journalctl -u catcam.service --since "1 hour ago"
```

To change rotation size/count or verbosity, edit `config/config.yaml`'s
`logging` block (see `docs/configuration.md`'s "Tuning guidance") and
restart `catcam.service`.

## Adjusting configuration

- **Structural config** (`camera`, `motion`, `recording`, `video_note`,
  `cooldown`, `streaming`, `logging`): edit
  `/opt/catcam/config/config.yaml`, then `sudo systemctl restart
  catcam.service` (config is only read at startup).
- **Secrets** (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_USER_ID`/`TELEGRAM_CHAT_ID`,
  `CATCAM_STREAM_VIEW_PASSWORD`): edit `/etc/catcam/.env`, then restart.
- **Cooldown interval, on/off**: change live via Telegram (`/cooldown
  <minutes>`, `/notifications_on`/`off`) — no restart needed, persisted to
  `storage/state/cooldown.json` immediately.
- **MediaMTX passwords**: edit `/etc/catcam/mediamtx.env`, then `sudo
  systemctl restart catcam-stream.service`.

See `docs/configuration.md` for the full key reference and precedence rules.

## Backing up `storage/state/`

`storage/state/cooldown.json` is the only durable state outside of recorded
clips (cooldown timer, notifications on/off). It's small and safe to copy
live (writes are atomic — temp file + `os.replace`, see
`src/catcam/cooldown.py`):

```bash
sudo cp /opt/catcam/storage/state/cooldown.json /path/to/backup/
```

To back up recorded clips too (not required for the app to function, just
your own archival preference):

```bash
sudo rsync -a /opt/catcam/storage/recordings/ /path/to/backup/recordings/
```

`storage/pending/` and `storage/tmp/` are transient (retry queue, in-progress
recordings respectively) and don't need backing up.

## Restarting the main service without SSH

From Telegram: `/restart_service` — runs a fixed, allowlisted
`sudo systemctl restart catcam.service` (see the sudoers rule in
`deploy/sudoers.d/catcam`, no other command can be triggered this way). Use
this when you've changed `.env`/`config.yaml` remotely and can't SSH in
immediately.

## Troubleshooting quick reference

| Symptom | Where to look |
|---|---|
| Bot doesn't respond at all | `systemctl status catcam.service`; `journalctl -u catcam.service` for an `InvalidToken` or similar startup error. |
| `/status` shows camera "NOT DETECTED" | `scripts/check_camera.sh`; check cabling/USB connection; `docs/hardware.md`. |
| `/status` shows stream "not ready" | `systemctl status catcam-stream.service` / `catcam-publisher.service`; `docs/streaming.md`'s troubleshooting table. |
| Clips never arrive despite motion | Check `/cooldown` isn't active; check `/notifications_on`; check `storage/pending/` for stuck failed deliveries (retried automatically every 60s). |
| Disk quota warnings | `scripts/diagnose.sh`'s storage section; oldest `storage/pending/` clips are deleted automatically to stay under `recording.disk_quota_mb`, but sustained over-quota usually means the quota is set too low for your motion frequency. |
| "Restart failed" from `/restart_service` | Confirm `/etc/sudoers.d/catcam` is installed and passes `sudo visudo -c -f /etc/sudoers.d/catcam`. |
