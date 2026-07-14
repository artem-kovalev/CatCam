# Deployment

Two deployment paths are documented here. **systemd is the primary,
fully-tested path** — native to Raspberry Pi OS, no container runtime
needed, and the simplest way to give the app clean access to `/dev/video*`
and the camera stack. **Docker Compose is documented only, as a secondary
alternative** — it is not built, tested, or used by `scripts/install.sh`.
Camera device passthrough (`--device=/dev/video0` for USB, CSI-specific bind
mounts for libcamera) is meaningfully more complex under Docker than under
systemd, which is the main reason systemd is primary for this project.

## Primary: systemd

### Requirements

- Raspberry Pi 4, Raspberry Pi OS Bookworm or newer (64-bit recommended).
- A CSI or USB camera connected — see `docs/hardware.md`.
- A Telegram bot token and your Telegram user/chat id — see
  `docs/telegram-setup.md`.

### Clean install

```bash
git clone <this-repo-url> catcam-src
cd catcam-src
sudo apt update
sudo apt install -y rpicam-apps v4l-utils ffmpeg python3-venv rsync wget
sudo scripts/install.sh
```

`scripts/install.sh` (run once as root, safe to re-run):

1. Creates the `catcam` system user/group (added to the `video` group for
   camera access) if not already present.
2. Syncs the repository into `/opt/catcam` (excluding `.venv/` and
   `storage/`, which are created/managed separately).
3. Creates `/opt/catcam/.venv` and installs `requirements.txt` into it.
4. Creates `/etc/catcam/.env` from `.env.example` if it doesn't already
   exist — **never auto-fills secrets**; you must edit it yourself.
5. Creates `/opt/catcam/config/config.yaml` from `config.example.yaml` if it
   doesn't already exist.
6. Creates `/etc/catcam/mediamtx.env` (blank password placeholders) if it
   doesn't already exist.
7. Downloads and installs MediaMTX to `/opt/mediamtx` if not already present,
   and installs `deploy/mediamtx/mediamtx.yml` if missing. Downloads
   `MEDIAMTX_VERSION` (default `v1.19.2`, matching `docs/streaming.md`) —
   override for a newer release with
   `MEDIAMTX_VERSION=vX.Y.Z sudo -E scripts/install.sh`.
8. Installs the `/restart_service` sudoers rule
   (`deploy/sudoers.d/catcam` → `/etc/sudoers.d/catcam`), validating it with
   `visudo -c` before accepting it.
9. Installs `catcam.service`, `catcam-stream.service`, and
   `catcam-publisher.service` to `/etc/systemd/system/`, enables
   `catcam-stream.service` and `catcam.service` unconditionally, and enables
   `catcam-publisher.service` only if `config.yaml`'s `camera.type` is `usb`.
10. Runs `scripts/check_camera.sh` as a smoke test (a warning, not a hard
    failure, if no camera is connected yet).

**After install completes, you must still:**

```bash
sudo $EDITOR /etc/catcam/.env            # TELEGRAM_BOT_TOKEN / USER_ID / CHAT_ID
sudo $EDITOR /etc/catcam/mediamtx.env    # MTX_AUTHINTERNALUSERS_{1,2}_PASS
sudo $EDITOR /opt/catcam/config/config.yaml   # camera.type, resolution, etc.
sudo systemctl restart catcam.service catcam-stream.service
scripts/diagnose.sh
```

See `docs/telegram-setup.md` for how to obtain the Telegram values, and
`docs/streaming.md` for the MediaMTX password/network model.

### Verifying autostart survives a reboot

```bash
sudo systemctl reboot
# after it comes back up:
systemctl is-enabled catcam.service catcam-stream.service
systemctl is-active catcam.service catcam-stream.service
scripts/diagnose.sh
```

Both services are `Restart=on-failure` with `StartLimitIntervalSec=0` (never
permanently gives up after repeated failures — see each unit file), and both
are `WantedBy=multi-user.target`, so `systemctl enable` (done by
`install.sh`) is sufficient for them to come up unattended after every boot.

### Upgrading

```bash
cd catcam-src && git pull
sudo scripts/install.sh   # safe to re-run: re-syncs code, re-installs deps,
                           # never touches .env/config.yaml/mediamtx.yml/storage
sudo systemctl restart catcam.service catcam-stream.service
```

See `docs/operations.md` for day-2 details.

### Rollback

```bash
cd catcam-src
git checkout <previous-tag-or-commit>
sudo scripts/install.sh
sudo systemctl restart catcam.service catcam-stream.service
```

Since `install.sh` never touches `/etc/catcam/.env`, `config.yaml`,
`mediamtx.yml`/`mediamtx.env`, or `storage/`, rolling the code back does not
touch your secrets, configuration, or recorded clips.

### Uninstalling

```bash
sudo scripts/uninstall.sh            # keeps .env, config.yaml, storage/,
                                      # mediamtx.yml/env, MediaMTX itself
sudo scripts/uninstall.sh --purge    # also removes all of the above
```

Removing the `catcam` system user is always an interactive y/N prompt,
independent of `--purge`, so a scripted/non-interactive run never deletes the
account (and therefore never risks changing file ownership) without an
operator confirming it.

## Secondary (documented only): Docker Compose

This is a sketch, not a verified deployment path — treat it as a starting
point, not a tested artifact. The main added complexity versus systemd is
camera device passthrough:

- **USB cameras**: pass the device node through directly.
- **CSI cameras**: libcamera needs more than a device node — typically
  `--privileged` or specific `/dev/video*` + `/dev/dma_heap` + cgroup device
  rules, which varies by Raspberry Pi OS/kernel version. This is exactly the
  complexity systemd avoids by running natively on the host.

```yaml
# docker-compose.yml (sketch — not built or tested)
services:
  mediamtx:
    image: bluenviron/mediamtx:1.19.2
    network_mode: host   # simplest way to satisfy the "bind wide, gate by
                          # MediaMTX auth" model from docs/streaming.md
    volumes:
      - ./deploy/mediamtx/mediamtx.yml:/mediamtx.yml:ro
    environment:
      - MTX_AUTHINTERNALUSERS_1_PASS=${MTX_AUTHINTERNALUSERS_1_PASS}
      - MTX_AUTHINTERNALUSERS_2_PASS=${MTX_AUTHINTERNALUSERS_2_PASS}
    devices:
      - /dev/video0:/dev/video0   # USB only; CSI needs more, see above
    restart: unless-stopped

  catcam:
    build: .
    network_mode: host   # simplest correct choice given mediamtx's own
                          # host networking above
    env_file:
      - .env
    volumes:
      - ./config/config.yaml:/app/config/config.yaml:ro
      - catcam-storage:/app/storage
    devices:
      - /dev/video0:/dev/video0   # USB only
    depends_on:
      - mediamtx
    restart: unless-stopped

volumes:
  catcam-storage:
```

A `Dockerfile` isn't included — building one, working out the CSI
device-passthrough details for your specific Pi/kernel, and testing restart
behavior across a reboot are left to whoever adopts this path. If you do,
please keep the systemd path working too, since it remains this project's
primary, tested deployment method.
