# Technical Specification for the GPT Agent: CatCam for Raspberry Pi 4 and Telegram

## Agent Role

You are a lead engineer specializing in embedded/Linux systems, Python, computer vision, video streaming, the Telegram Bot API, DevOps, and information security.

Your task is to design, implement, and fully document a private cat monitoring system based on a Raspberry Pi 4 and a connected camera.

Work sequentially through the tasks in the `tasks/` directory. Do not skip stages. After completing each task, update its status and record the created files, decisions made, verification commands, and known limitations.

## Project Goal

Create a solution that:

1. Streams video from the Raspberry Pi camera over the internet.
2. Detects the cat's movement in the frame.
3. Records a short video clip after motion is detected.
4. Converts the clip into the Telegram video note format — a circular video message.
5. Sends the video note to a private Telegram chat.
6. Does not send notifications more frequently than the configured interval.
7. Allows the interval to be changed through the Telegram bot.
8. Is accessible only to the owner.
9. Starts automatically after the Raspberry Pi reboots.
10. Includes complete documentation for assembly, setup, deployment, updates, diagnostics, and recovery.

## Initial Hardware and Environment

- Raspberry Pi 4.
- A compatible Raspberry Pi Camera Module or USB camera.
- Raspberry Pi OS.
- Internet connection.
- A private Telegram bot.
- One primary owner/user.

If the camera model, connection method, or OS characteristics are not specified, the implementation must support configuration through `.env` or YAML and include instructions for at least:

- a CSI/libcamera camera;
- a USB UVC camera through V4L2.

## Required Features

### Video Streaming

Implement live viewing of the Raspberry Pi camera feed over the internet.

Requirements:

- use a secure publishing method;
- do not expose an unsecured video stream to the public internet;
- document the recommended access method;
- support operation on the local network;
- select and justify the protocol: WebRTC, HLS, RTSP over VPN, or another suitable approach;
- provide automatic startup;
- add a health check and troubleshooting instructions.

Preferred secure scenario:

- Tailscale/WireGuard for access to the Raspberry Pi;
- MediaMTX or an equivalent lightweight media server;
- WebRTC/HLS for browser viewing or RTSP inside a private VPN.

The agent must verify that the selected software and commands are current using official documentation before using them.

### Motion Detection

The system must:

- receive frames from the camera;
- detect significant motion;
- reduce false positives;
- allow motion sensitivity to be configured;
- support a region of interest;
- support a minimum motion duration;
- prevent parallel recordings of the same event;
- maintain an event log.

The minimum implementation may use OpenCV background subtraction or frame differencing.

Optional additional implementation:

- lightweight `cat / not cat` classification;
- enabled through configuration;
- must not be required for the basic system to run.

### Event Recording

When confirmed motion is detected:

- record a short video clip;
- recommended default duration: 8–15 seconds;
- include a short pre-roll if technically practical;
- limit file size;
- correctly handle a missing camera or FFmpeg;
- delete temporary files after successful delivery;
- preserve unsent files for retry;
- enforce a disk-space limit.

### Telegram Video Note

The clip must:

- be square;
- use a compatible container and codec;
- be sent correctly using `sendVideoNote`;
- comply with supported duration and file-size limits;
- appear as a circular video message rather than a regular video.

Conversion must be performed with FFmpeg using automatic crop and scale operations.

### Delivery Rate Limit

After an event is successfully sent, a cooldown begins.

Requirements:

- default value: 60 minutes;
- the interval can be configured through the Telegram bot;
- the value persists after restart;
- the cooldown is measured from the last successful delivery;
- motion detected during the cooldown may be logged but must not be sent;
- provide commands to disable and enable notifications;
- validate the configured interval;
- recommended range: 1–1440 minutes.

### Private Telegram Bot

The bot is accessible only to the owner.

Required protection:

- allowed `TELEGRAM_USER_ID`;
- allowed `TELEGRAM_CHAT_ID`;
- all other users are denied without exposing implementation details;
- the token is stored only in `.env` or a system secret file;
- secrets must not be committed to Git;
- logs must not contain tokens.

Minimum commands:

- `/start` — status and short help;
- `/status` — camera, stream, detector, cooldown, and disk status;
- `/cooldown` — show the current interval;
- `/cooldown <minutes>` — change the interval;
- `/notifications_on`;
- `/notifications_off`;
- `/snapshot` — receive the current frame;
- `/record <seconds>` — manually record a clip within allowed limits;
- `/stream` — receive a secure link or connection instructions;
- `/help`;
- `/restart_service` — optional, only with a secure allowlist and without shell injection.

The interface may use inline buttons, but all commands must also work without them.

## Non-Functional Requirements

- Primary language: Python 3.
- Compatible with Raspberry Pi 4.
- Minimize CPU and RAM usage.
- Remain stable during temporary internet outages.
- Provide a retry queue.
- Prevent multiple processes from accessing the camera concurrently in incompatible ways.
- Use structured logs.
- Configure log rotation.
- Start automatically through systemd or Docker Compose.
- Select and fully implement one primary deployment method.
- A second deployment method may be documented as an alternative.
- All commands must be ready to copy and run.
- All paths, service names, and parameters must be consistent between the code and documentation.
- Code must include error handling and type hints.
- Use `.env` and/or YAML for configuration.
- The repository must include `.env.example`.
- The repository must not contain real secrets.

## Recommended Repository Structure

```text
catcam/
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── pyproject.toml
├── requirements.txt
├── config/
│   └── config.example.yaml
├── src/
│   └── catcam/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── camera.py
│       ├── motion.py
│       ├── recorder.py
│       ├── video_note.py
│       ├── telegram_bot.py
│       ├── cooldown.py
│       ├── storage.py
│       ├── health.py
│       └── logging_config.py
├── scripts/
│   ├── install.sh
│   ├── check_camera.sh
│   ├── diagnose.sh
│   └── uninstall.sh
├── deploy/
│   ├── systemd/
│   │   ├── catcam.service
│   │   └── catcam-stream.service
│   └── mediamtx/
│       └── mediamtx.yml
├── tests/
│   ├── test_config.py
│   ├── test_cooldown.py
│   ├── test_authorization.py
│   ├── test_motion.py
│   └── test_video_note.py
├── docs/
│   ├── architecture.md
│   ├── hardware.md
│   ├── raspberry-pi-setup.md
│   ├── telegram-setup.md
│   ├── streaming.md
│   ├── deployment.md
│   ├── configuration.md
│   ├── operations.md
│   ├── troubleshooting.md
│   ├── security.md
│   └── testing.md
└── tasks/
    ├── summary.md
    ├── task1.md
    └── ...
```

The agent may change the structure but must explain the changes.

## Task Execution Rules

For each task:

1. Read the goal and dependencies.
2. Document any necessary assumptions without blocking progress.
3. Implement the required result.
4. Add or update tests.
5. Run verification checks.
6. Update the documentation.
7. Mark the acceptance criteria.
8. Record the following in the `Result` section:
   - created files;
   - modified files;
   - commands executed;
   - test results;
   - unresolved questions.

Do not use placeholders such as `TODO`, `implement later`, pseudocode instead of required implementation, or commands that do not match the selected operating system.

## General Acceptance Criteria

The project is complete when:

- The Raspberry Pi detects the camera.
- The owner can access the video stream through a secure connection.
- Motion triggers event recording.
- The clip arrives in Telegram as a video note.
- Motion detected before the cooldown expires does not trigger another delivery.
- The interval can be changed through the bot and persists after restart.
- An unauthorized user cannot control the bot.
- Services start automatically after reboot.
- The system recovers after a temporary network outage.
- Logs and diagnostics make common failures identifiable.
- All tests pass.
- The complete installation can be performed on a clean Raspberry Pi OS system by following the documentation.
- The documentation contains no hidden steps or unexplained variables.
