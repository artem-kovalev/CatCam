# Security

## Threat model

CatCam is a **single-owner, private** system — not a multi-tenant or public
service. The assumptions below shape every other decision in this document:

- Exactly one Telegram account (`TELEGRAM_USER_ID`/`TELEGRAM_CHAT_ID`) is
  ever authorized to use the bot. There is no concept of a second legitimate
  user, an admin tier, or a guest tier.
- The live video stream is reachable only over Tailscale (or a plain LAN) —
  never through a forwarded router port. See `docs/streaming.md`'s "Network
  exposure model" for the full reasoning; this document doesn't repeat it,
  only the resulting operator obligations (below).
- The Raspberry Pi itself is physically in the owner's home — this document
  does not cover physical/device-theft threats beyond disk-encryption
  recommendations already standard for Raspberry Pi OS.
- The realistic attackers this design defends against are: an internet-wide
  scanner probing for open ports/exposed services, a second Telegram user
  who somehow finds the bot's username, and a device on the same LAN/tailnet
  that isn't the owner's (a guest's phone, a compromised IoT device).

## What's authorized vs. denied

Every Telegram command handler is wrapped by `telegram_bot.py`'s
`_authorized_only` decorator, which runs **before** any handler body:

- Checks `update.effective_user.id == config.telegram.user_id` **and**
  `update.effective_chat.id == config.telegram.chat_id`.
- On mismatch: replies with a generic `"Not authorized."` (no hint about
  which commands exist, why it failed, or any internal state) and logs the
  rejected `user_id`/`chat_id` (never the bot token) for audit — see
  `docs/telegram-setup.md` §2 for how this same log line doubles as a way to
  discover your own real ids during initial setup.
- No handler — not even `/help` or `/start` — runs before this check passes.
  There is no "public" subset of commands.

This is enforced once, centrally, rather than per-handler, so a new command
added later can't accidentally skip the check — see `src/catcam/
telegram_bot.py`'s module docstring.

If you get complaints from someone else claiming the bot didn't respond to
them: that's the intended behavior, not a bug. Only the configured owner id
gets anything other than the denial message.

## Secret storage

| Secret | Where it lives | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `/etc/catcam/.env` (deployed) or repo-root `.env` (local dev) | Never committed — `.gitignore` excludes `.env`; `.env.example` ships with blank values only. |
| `TELEGRAM_USER_ID` / `TELEGRAM_CHAT_ID` | Same `.env` | Not secret in the cryptographic sense, but keep alongside the token since they jointly define who's authorized. |
| `CATCAM_STREAM_VIEW_PASSWORD` | Same `.env` | Must match `MTX_AUTHINTERNALUSERS_1_PASS` below — see `docs/telegram-setup.md` §3. |
| `MTX_AUTHINTERNALUSERS_1_PASS` / `_2_PASS` (MediaMTX viewer/publisher passwords) | `/etc/catcam/mediamtx.env` | Never hardcoded in the committed `deploy/mediamtx/mediamtx.yml` — see that file's own `SECRETS:` comment block. |

Recommended file permissions on a deployed Pi (both are already applied by
`scripts/install.sh` when it creates these files, but re-check after any
manual edit):

```bash
sudo chmod 600 /etc/catcam/.env /etc/catcam/mediamtx.env
sudo chown catcam:catcam /etc/catcam/.env /etc/catcam/mediamtx.env
```

**No secret is ever logged.** `logging_config.py`'s `_RedactionFilter` scrubs
any Telegram-bot-token-shaped substring (`\d{6,}:[A-Za-z0-9_-]{30,}`) from
every log record before it reaches a handler, as defense-in-depth on top of
"never log the token directly" at every call site (`redact()` is also
applied explicitly around `/restart_service`'s and the global error
handler's exception messages, in case a third-party library string happens
to embed one). MediaMTX passwords are never logged by CatCam's own code
either — they're only ever read from the environment and passed straight
into a connection URL or `subprocess`/library call, never `print`ed or
`logger`'d.

## Token rotation procedure

If a token is ever suspected compromised (accidentally pasted somewhere
public, committed by mistake, etc.):

1. Open [@BotFather](https://t.me/BotFather) → `/mybots` → your bot → "API
   Token" → "Revoke current token". This immediately invalidates the old
   token everywhere.
2. Copy the newly issued token.
3. `sudo $EDITOR /etc/catcam/.env` (or repo-root `.env` in local dev) and
   replace `TELEGRAM_BOT_TOKEN`.
4. `sudo systemctl restart catcam.service` (`.env` is only read at process
   startup — see `docs/operations.md`).
5. Send `/help` from your Telegram account to confirm the bot responds
   again.

The same procedure applies to MediaMTX's passwords (`mediamtx.env` +
`sudo systemctl restart catcam-stream.service`), and to
`CATCAM_STREAM_VIEW_PASSWORD` (must be updated in lockstep with
`MTX_AUTHINTERNALUSERS_1_PASS`, then `catcam.service` restarted too, since
that's the value `/snapshot`/`/record` and the motion pipeline use to read
the RTSP feed).

## `/restart_service` privilege scoping

`/restart_service` is the one command that touches anything outside the
CatCam process itself, so it gets its own recap here (originally decided in
task 8, wired up in task 10):

- `telegram_bot.py`'s `_build_restart_command()` returns a **fixed** argv —
  `["/usr/bin/sudo", "/usr/bin/systemctl", "restart", "catcam.service"]` —
  with zero user-supplied arguments. `subprocess.run(..., shell=False)` means
  there is no shell to inject into even in principle.
- The matching sudoers rule (`deploy/sudoers.d/catcam`, installed to
  `/etc/sudoers.d/catcam` by `scripts/install.sh`) grants the `catcam`
  service account passwordless rights to **exactly that command string** —
  not a wildcard, not "any systemctl subcommand," not "any service name."
  `sudo` strips itself before matching against the rule, so the rule's
  `Cmnd` is `/usr/bin/systemctl restart catcam.service`.
- `scripts/install.sh` validates the rule with `visudo -c -f` before
  accepting it, and removes the file again if validation fails — a broken
  sudoers file is never left in place.
- Net effect: even if a future bug let an unauthorized Telegram update reach
  `_restart_service_impl` (it can't, per `_authorized_only` above, but
  hypothetically), the blast radius is capped at "restart the CatCam
  service" — not arbitrary root command execution.

## Keeping the host updated

- **Raspberry Pi OS**: `sudo apt update && sudo apt full-upgrade` regularly.
  Security patches for the kernel, `libcamera`, and OpenSSL/Python matter
  more here than on a device with no network-facing services at all — this
  one does have three open ports (RTSP/HLS/WebRTC), even though they're
  gated by MediaMTX's own auth (see `docs/streaming.md`).
- **Tailscale**: `sudo apt update && sudo apt install --only-upgrade
  tailscale`, or enable Tailscale's own auto-update
  (`sudo tailscale set --auto-update`) if available on your OS version.
  Tailscale itself is the thing standing between the stream ports and "not
  reachable from outside your tailnet at all" — treat it as security-
  critical, not a convenience feature.
- **MediaMTX**: pinned by `MEDIAMTX_VERSION` in `scripts/install.sh` (default
  `v1.19.2`, matching `docs/streaming.md`). Check
  [the releases page](https://github.com/bluenviron/mediamtx/releases)
  periodically and re-run `MEDIAMTX_VERSION=vX.Y.Z sudo -E scripts/install.sh`
  to upgrade — `install.sh` never re-downloads an already-present version,
  so this is a manual, deliberate step, not automatic.
- **Python dependencies**: `requirements.txt` pins compatible-release ranges
  (e.g. `python-telegram-bot>=20,<21`), not exact versions — periodically
  re-run `sudo scripts/install.sh` after a `pip list --outdated` check in the
  venv to pick up patch releases within those ranges.

## What this design deliberately does not defend against

Being explicit about this rather than silent about it:

- A compromised Telegram account belonging to the owner (2FA on the
  Telegram account itself is the owner's responsibility, outside this repo's
  scope).
- Physical access to the Raspberry Pi's SD card/storage (no disk encryption
  is configured by `scripts/install.sh` — add LUKS/dm-crypt yourself if this
  matters for your threat model).
- A compromised device already inside the tailnet/LAN with the correct
  MediaMTX password (the IP allowlist + password combination in
  `authInternalUsers` is the whole access-control layer for the stream —
  see `docs/streaming.md`'s "Network exposure model" for why both are
  required together).
