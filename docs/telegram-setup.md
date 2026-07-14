# Telegram Bot Setup

CatCam's bot (`src/catcam/telegram_bot.py`) is private to a single owner:
every command is rejected unless it comes from the exact Telegram user id (and
chat id) configured in `.env`. This walks through creating the bot, finding
the three required secrets, and getting them into `.env`.

## 1. Create the bot via BotFather

1. Open a chat with [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot` and follow the prompts (choose a display name, then a
   unique username ending in `bot`).
3. BotFather replies with an HTTP API token that looks like
   `123456789:AAFabcDEF-01234567890abcdefghijklmno` - this is
   `TELEGRAM_BOT_TOKEN`. Copy it now; you can always fetch it again later via
   `/mybots` -> your bot -> "API Token", but never post it anywhere public
   (anyone with this token fully controls the bot).
4. Send your new bot a `/start` message from your own Telegram account so it
   has a chat open to reply into (required before it can message you first).

## 2. Find your `TELEGRAM_USER_ID` and `TELEGRAM_CHAT_ID`

Both are numeric Telegram ids, not usernames. In a private 1:1 chat with your
bot, your user id and the chat id are the same number.

**Simplest method:** message [@userinfobot](https://t.me/userinfobot) - it
immediately replies with your numeric user id. Use that same number for both
`TELEGRAM_USER_ID` and `TELEGRAM_CHAT_ID`.

**Alternative, using CatCam's own rejection log** (useful if you'd rather not
add a third-party bot): every unauthorized attempt is logged with the
sender's id before being rejected (`catcam.telegram_bot` logger, "Rejected
unauthorized access: user_id=... chat_id=..."), *never* the bot token. So:

1. Temporarily set `TELEGRAM_USER_ID=0` and `TELEGRAM_CHAT_ID=0` in `.env`
   (placeholders that will never match a real Telegram account, but are still
   valid integers so the app starts).
2. Start the bot (`python -m catcam.telegram_bot`, or restart
   `catcam.service` if already deployed) and send it `/start`.
3. Check the log (`storage/logs/catcam.log`, or `journalctl -u
   catcam.service` once deployed) for the rejected `user_id=...` /
   `chat_id=...` line - that's your real id.
4. Replace the placeholders in `.env` with the real values and restart.

## 3. Populate `.env`

```bash
cp .env.example .env   # if you haven't already, from task 1
```

```
TELEGRAM_BOT_TOKEN=123456789:AAFabcDEF-01234567890abcdefghijklmno
TELEGRAM_USER_ID=987654321
TELEGRAM_CHAT_ID=987654321
```

`config.py` requires all three at startup (`catcam.config.ConfigError` if any
are missing or non-numeric) - there is no way to start the bot without them.

Optionally, if you're running the camera stream (task 3) and want
`/snapshot`/`/record` to read frames from it rather than opening the camera
directly, also set:

```
CATCAM_STREAM_VIEW_PASSWORD=<same value as MTX_AUTHINTERNALUSERS_1_PASS in /etc/catcam/mediamtx.env>
```

See `docs/configuration.md`'s Secrets table for what happens if this is left
unset.

## 4. Restart to pick up new secrets

`.env` is only read at process startup:

```bash
# Local/manual run:
python -m catcam.telegram_bot

# Deployed (task 10):
sudo systemctl restart catcam.service
```

## 5. Verify

Send `/help` from your own Telegram account - you should get the command
list back. Send the same commands from a second Telegram account (or ask a
friend to try) - every command should get a generic "Not authorized." reply
with no hint about the bot's internals, and no state should change (e.g. a
`/cooldown 5` from that account must not actually change the cooldown
interval).

## Commands

| Command | Description |
|---|---|
| `/start` | Greets you and shows the button menu. |
| `/help` | Lists every command. |
| `/status` | Camera/stream/cooldown/notifications/disk status. |
| `/cooldown [minutes]` | Shows the current cooldown, or sets it (1-1440). |
| `/notifications_on` / `/notifications_off` | Resume/pause automatic delivery. |
| `/snapshot` | Sends a single still frame. |
| `/record <seconds>` | Records and sends a clip (clamped to 30s max). |
| `/stream` | How to view the live stream (Tailscale/LAN only). |
| `/restart_service` | Restarts `catcam.service` via a fixed, allowlisted `sudo systemctl restart` call - see task 10 for the matching `sudoers.d` rule. |

Most read-only, non-destructive commands (`/status`, `/snapshot`,
`/notifications_on`/`off`, `/stream`) also have inline buttons under
`/start`/`/help`; they call the exact same handler code as the typed
commands. `/record`, `/cooldown`, and `/restart_service` are typed-command
only, so an accidental tap can't trigger a recording, a config change, or a
service restart.
