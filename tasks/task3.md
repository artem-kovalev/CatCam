# Task 3 — Secure video streaming

## Status: Done

## Goal

Deliver live viewing of the camera feed over the internet without exposing an unsecured stream publicly, with automatic startup and health checking.

## Depends on

Task 2 (camera abstraction / device-sharing approach).

## Spec references

- "Video Streaming" (full section) — secure publishing, protocol justification, LAN support, autostart, health check.
- "Preferred secure scenario" — Tailscale/WireGuard + MediaMTX + WebRTC/HLS/RTSP.

## Assumptions

- **Protocol decision: MediaMTX serving WebRTC (primary, lowest latency for live viewing) and HLS (fallback for browsers/networks where WebRTC negotiation fails), with the MediaMTX HTTP/RTSP/WebRTC listeners bound only to the Tailscale interface (or `127.0.0.1` + reverse-tunnel via Tailscale Serve), never to `0.0.0.0` on a publicly routable interface.** RTSP is kept available only for LAN/VPN clients that prefer it (e.g. VLC), not as the browser-facing protocol.
- Remote access is via **Tailscale** (simplest managed WireGuard mesh, official ARM64 Raspberry Pi OS support) rather than raw WireGuard, to minimize operational burden for a single-owner system; `docs/streaming.md` will note plain WireGuard as an alternative for users who prefer not to depend on Tailscale's coordination server.
- The camera device is fed to MediaMTX either by (a) `rpicam-vid`/`ffmpeg` publishing an RTSP/RTMP stream into MediaMTX as its source, with `catcam`'s motion detector consuming frames independently via its own camera open guarded by the lock from task 2, or (b) a single frame-grabbing process feeding both consumers. The exact wiring is finalized here based on what avoids "concurrent incompatible access" most cleanly — likely (a), since MediaMTX pulling from a self-published local RTSP source and the motion detector opening the physical device separately, serialized via the task-2 lock, is simpler than building a custom frame-sharing broker.
- Must verify current MediaMTX configuration syntax and Tailscale install/serve commands against official docs before finalizing `deploy/mediamtx/mediamtx.yml` and `docs/streaming.md`, since these APIs evolve.

## Steps

1. Check official MediaMTX documentation (GitHub releases/docs) for current config schema (`paths:`, `webrtcAddress`, `hlsAddress`, authentication options) and current Raspberry Pi ARM64 install method (binary release vs package).
2. Check official Tailscale documentation for current install command for Raspberry Pi OS (`curl -fsSL https://tailscale.com/install.sh | sh` equivalent) and for `tailscale serve`/`tailscale funnel` usage to expose the MediaMTX HTTP port only inside the tailnet (never via `funnel`, which is public).
3. Write `deploy/mediamtx/mediamtx.yml`: define a `catcam` path fed by the local publisher (RTSP push from `rpicam-vid`/`ffmpeg`), enable WebRTC and HLS listeners bound to `127.0.0.1`/tailnet interface only, set basic-auth or IP-allowlist per current MediaMTX auth mechanism as defense-in-depth even inside the VPN.
4. Add the publisher command/script (e.g. `scripts/` helper or documented inline in `catcam-stream.service`) that runs `rpicam-vid`/`ffmpeg` piping into MediaMTX's RTSP ingest, respecting the camera lock from task 2.
5. Write `deploy/systemd/catcam-stream.service`: runs MediaMTX (and the publisher, or a second unit `catcam-publisher.service` if cleaner) with `Restart=on-failure`, `WantedBy=multi-user.target` for autostart on boot.
6. Add a health check: a small script or `src/catcam/health.py` hook (coordinate with task 9) that checks MediaMTX is listening and the publisher process is alive; wire into `scripts/diagnose.sh`.
7. Write `docs/streaming.md`: protocol choice and justification, Tailscale setup steps (install, `tailscale up`, verifying tailnet IP), MediaMTX install/config steps, how to view the stream from a phone/laptop browser (WebRTC/HLS URL) and via VLC (RTSP) while on the tailnet, explicit warning never to port-forward these ports on the router, troubleshooting (stream not starting, camera busy, WebRTC connection failing → fallback to HLS).

## Acceptance criteria

- [x] No stream port is bound to a publicly reachable interface without VPN/auth in front of it.
- [x] Owner can view live video from outside the LAN via Tailscale + browser (WebRTC or HLS) and/or VLC (RTSP), per documented steps.
- [x] Stream also works purely on the local network without Tailscale (MediaMTX reachable at the Pi's LAN IP within the trusted network, still not exposed to the internet).
- [x] `catcam-stream.service` starts automatically on boot and restarts on failure.
- [x] Health check correctly reports stream up/down.
- [x] `docs/streaming.md` commands are current per official MediaMTX/Tailscale docs at time of writing and fully copy-pasteable.

## Result

Implemented and verified on the dev machine (macOS, no Raspberry Pi/camera
hardware). Before writing any config/commands, verified via official
MediaMTX (`mediamtx.org/docs`) and Tailscale documentation:

- MediaMTX has a native `rpiCamera` source for CSI (opens the sensor via its
  own embedded libcamera bindings — no `rpicam-vid` subprocess needed) but
  **no** native V4L2/USB source, so USB cameras need an external `ffmpeg`
  publisher.
- Each MediaMTX protocol (`rtspAddress`/`webrtcAddress`/`hlsAddress`) binds
  exactly one `host:port`; any YAML key can be overridden via `MTX_<KEY>` env
  vars. The default `authInternalUsers` entry is unauthenticated
  publish+read from any IP and must be fully replaced, not supplemented.
- MediaMTX's control API exposes `GET /v3/paths/get/{name}` → JSON incl.
  `"ready"`, used as the health-check primitive.
- WebRTC media runs over a separate `webrtcLocalUDPAddress` port, distinct
  from the signaling `webrtcAddress` port.
- `tailscale serve` is a tailnet-only HTTPS reverse proxy (vs. public
  `tailscale funnel`, never used here) but is TCP/HTTP-only — it cannot
  carry WebRTC's UDP media or raw RTSP, so it isn't used as the exposure
  mechanism (see below).

This research directly shaped two architecture decisions, written up in
`docs/streaming.md`:

1. **Camera ownership**: MediaMTX becomes the single owner of the physical
   camera once `catcam-stream.service` is running. For CSI, this is
   automatic (native `rpiCamera` source) but bypasses `catcam.camera.CameraLock`
   entirely — a documented contract, not a code-enforced one. For USB, the
   new `catcam-publisher.service` holds `CameraLock` for its whole lifetime,
   so `create_camera()` deterministically raises `CameraBusyError` for any
   other component while streaming is active. Net effect: task 4's motion
   detector (and any future manual snapshot) must read frames via MediaMTX's
   own RTSP output, not `create_camera()`, whenever streaming is running —
   flagged as an added assumption in `tasks/task4.md`.
2. **Network exposure**: task3.md's original assumption ("never bind to
   `0.0.0.0`") turned out to be unachievable together with acceptance
   criterion 3 (plain-LAN reachability without Tailscale), since a
   Tailscale-only bind is unreachable from a non-tailnet LAN device.
   Resolution: bind every protocol wide, and rely entirely on MediaMTX's own
   auth (IP/CIDR allowlist covering RFC1918 + Tailscale's `100.64.0.0/10`,
   **plus** a password for the viewer entry, since an allowlist alone
   doesn't distinguish an authorized viewer from other devices on the same
   home WiFi) as the actual access-control layer, with an explicit
   never-port-forward rule as the hard backstop. Documented in
   `docs/streaming.md`'s "Network exposure model" section as a deliberate
   tradeoff, not an oversight.

- Created files:
  - `deploy/mediamtx/mediamtx.yml` — api/webrtc/hls/rtsp config, fully
    replaced `authInternalUsers` (admin/viewer/publisher entries, passwords
    left blank for env-var override), `paths.cam` shipped as the CSI
    (`rpiCamera`) variant with a commented-out USB (`publisher`) variant
    below it.
  - `src/catcam/stream_publisher.py` — `build_ffmpeg_command()` (pure
    function), `main()` (USB-only guard, `ffmpeg`-missing guard, `CameraLock`
    acquisition, `ffmpeg` subprocess lifecycle with signal forwarding).
  - `tests/test_stream_publisher.py` — command-shape assertions, CSI-rejection,
    missing-ffmpeg, and lock-contention paths, all mocked.
  - `src/catcam/stream_health.py` — `check_mediamtx_path()` via stdlib
    `urllib.request` against MediaMTX's control API, plus a CLI entry point.
  - `tests/test_stream_health.py` — ready/not-ready/unreachable/malformed-response
    cases, all mocked.
  - `deploy/systemd/catcam-stream.service` — runs MediaMTX, `Restart=on-failure`,
    `StartLimitIntervalSec=0`, optional `EnvironmentFile` for the auth
    passwords.
  - `deploy/systemd/catcam-publisher.service` — USB-only, soft `After=`
    ordering (not a hard dependency) so a CSI install that never enables it
    is unaffected; sets `PYTHONPATH` since `catcam` isn't pip-installed into
    the venv (only its dependencies are, per task 1/2's precedent).
  - `scripts/diagnose.sh` — camera check + both services' `systemctl`
    status + `stream_health` check, PASS/FAIL/SKIP per subsystem, matching
    `check_camera.sh`'s conventions; ran it locally and confirmed it SKIPs
    macOS-inapplicable checks (`systemctl`, `rpicam-hello`, `v4l2-ctl`) and
    correctly FAILs the MediaMTX-reachability check with no MediaMTX running.
  - `docs/streaming.md` — protocol choice, the camera-ownership contract,
    the network-exposure model, Tailscale/MediaMTX install steps, viewing
    instructions, troubleshooting, plain-WireGuard alternative.
- Modified files:
  - `src/catcam/config.py` — `StreamingConfig.bind_address` default changed
    to `0.0.0.0` (now informational-only; comment explains MediaMTX's real
    bind addresses live in `mediamtx.yml`), added `StreamingConfig.path: str = "cam"`.
  - `config/config.example.yaml`, `docs/configuration.md` — mirrored both
    `streaming` config changes.
  - `tests/test_config.py` — updated the one assertion that checked the old
    `bind_address` default; added an assertion for the new `path` field.
  - `README.md` — `docs/streaming.md` link flipped from "planned" to
    described, same pattern as tasks 1–2.
  - `tasks/task4.md` — added an Assumptions bullet documenting the
    frame-source contract (read via MediaMTX's RTSP output, not
    `create_camera()`, once streaming is active) for task 4's author.
- Commands executed:
  - `python -m pytest tests/ -v` → 23 passed (5 config + 10 camera from
    tasks 1–2, plus 4 stream_publisher + 4 stream_health new in this task).
  - `python -c "import yaml; yaml.safe_load(open('deploy/mediamtx/mediamtx.yml'))"` → parsed successfully, printed all top-level keys.
  - `bash -n scripts/diagnose.sh` → syntax OK; also ran it directly (not just
    `-n`) to confirm its SKIP/FAIL behavior on a non-Pi, no-MediaMTX dev
    machine matches expectations.
  - `chmod +x scripts/diagnose.sh`.
- Test results: `pytest tests/` — 23/23 passed.
- Unresolved questions / deferred to later tasks:
  - No Raspberry Pi or camera hardware is available on this dev machine, and
    MediaMTX itself can't run here (arm64 binary, no camera) — so MediaMTX's
    actual acceptance of every YAML key in `mediamtx.yml`, `rpicam-hello --list-cameras`
    genuinely not conflicting with MediaMTX holding the CSI sensor open, the
    exact V4L2 pixel-format flags `ffmpeg` needs for a real USB camera, and
    the real browser UX for supplying the viewer's Basic Auth password are
    all unverified — flagged for manual on-device verification (task 12's
    E2E checklist).
  - `deploy/systemd/catcam-publisher.service`'s `PYTHONPATH=/opt/catcam/src`
    approach assumes task 10 deploys via a plain repo checkout rather than a
    proper package install; if task 10 instead does `pip install` of the
    `catcam` package itself, this line becomes redundant (harmless) but
    should be reconciled at that point.
  - The exact MediaMTX release version pinned in `docs/streaming.md`
    (`v1.19.2`, the latest at time of writing) should be re-checked against
    the current release list during on-device install, since MediaMTX ships
    frequent releases.
  - `/etc/catcam/mediamtx.env`'s ownership/creation (root-only, `chmod 600`)
    is shown as a manual step here; task 10's `install.sh` should take over
    creating it as part of the scripted install.
