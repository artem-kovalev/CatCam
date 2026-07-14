# Video Streaming

Live viewing of the camera feed via [MediaMTX](https://mediamtx.org), reachable
over [Tailscale](https://tailscale.com) from anywhere, or on the plain LAN
without Tailscale — never exposed on the public internet.

## Protocol choice

- **WebRTC** (primary) — lowest latency, best for live viewing from a phone
  or laptop browser.
- **HLS** (fallback) — plain HTTP, works when a client/network can't
  negotiate WebRTC (e.g. some restrictive NATs, older browsers).
- **RTSP** — for VLC or other RTSP-capable clients on the tailnet/LAN; not
  the primary browser-facing protocol.

All three are served by a single MediaMTX instance from `deploy/mediamtx/mediamtx.yml`.

## Camera ownership contract

MediaMTX is the single process that opens the physical camera device once
streaming is running:

- **CSI cameras**: MediaMTX's native `rpiCamera` source (`source: rpiCamera`
  in `mediamtx.yml`) opens the CSI sensor directly via its own embedded
  libcamera bindings. This does **not** go through `catcam.camera.CameraLock`
  at all — it's not Python code calling into `camera.py`. There is no way to
  enforce mutual exclusion here in code; **do not** call `create_camera()`
  on a CSI device while `catcam-stream.service` is running, or the two will
  race the same sensor at the driver level.
- **USB cameras**: `catcam-publisher.service` (`src/catcam/stream_publisher.py`)
  runs `ffmpeg` for as long as the service is up, acquiring
  `catcam.camera.CameraLock` for its entire lifetime and publishing into
  MediaMTX's `cam` path. Since this service runs continuously from boot
  (task 10), any other component that calls `create_camera()` on the same
  USB device will get a `CameraBusyError` — deterministically, not just
  occasionally.

**Net effect for both camera types:** once `catcam-stream.service` is active
(the normal always-on state), any CatCam component that needs frames — the
motion detector (task 4), a future manual-snapshot command — must read them
from MediaMTX itself, e.g.:

```python
import cv2
cap = cv2.VideoCapture("rtsp://catcam-viewer:<password>@127.0.0.1:8554/cam")
```

not by calling `create_camera()` directly. `create_camera()` remains valid
only when streaming is stopped (local development/debugging without
MediaMTX installed at all).

## Network exposure model

MediaMTX binds each protocol to exactly one `host:port` — it cannot bind a
single protocol to two addresses at once. That's in tension with this
project's two acceptance requirements together:

- reachable via Tailscale from outside the LAN, **and**
- reachable on the plain LAN with Tailscale not involved at all.

A Tailscale-only bind (the `100.x.x.x` CGNAT address) is unreachable from a
plain LAN device that isn't in the tailnet, so it would break the second
requirement. Binding only to the LAN IP would break the first. **This is why
`mediamtx.yml` binds every protocol wide (`:8554`/`:8889`/`:8888`) and relies
entirely on MediaMTX's own authentication as the access-control layer** —
this is a deliberate tradeoff, not an oversight:

- The default MediaMTX `authInternalUsers` entry is unauthenticated
  publish+read from any IP — it is fully replaced in `mediamtx.yml`, not
  merely supplemented.
- The `catcam-viewer` entry requires **both** an IP/CIDR allowlist (RFC1918
  private ranges + Tailscale's `100.64.0.0/10` CGNAT range) **and** a
  password. The allowlist alone isn't enough on a home LAN: "any device on
  the same WiFi" includes guests, IoT devices, etc., all in the same private
  range — the password closes that gap independent of network topology.
- The control API (`apiAddress`) binds to `127.0.0.1` only — nothing off-box
  ever needs it.
- The `catcam-publisher` entry (USB only) is restricted to `127.0.0.1` —
  `ffmpeg` always runs on the same host as MediaMTX.

**You must never port-forward ports 8554, 8888, 8889, or 8189 on your
router.** MediaMTX's own auth is defense-in-depth on top of that rule, not a
substitute for it. As an optional extra layer, you can add an OS firewall
rule (`ufw`/`nftables`) restricting those ports to the same private/tailnet
ranges used in `authInternalUsers` — this is not scripted here (see tasks 10/11
for broader deployment/security hardening) but is a reasonable addition.

`config/config.yaml`'s `streaming.bind_address` is informational only (used
to build connect-URLs for docs/health output) — it does not control any
actual bind address; that lives solely in `deploy/mediamtx/mediamtx.yml`.

## Setting up Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Follow the printed URL to authenticate the Pi into your tailnet. Verify your
tailnet IP/MagicDNS name:

```bash
tailscale ip -4
tailscale status
```

Do **not** use `tailscale funnel` for this — funnel exposes a port to the
public internet, which is exactly what this project must avoid.
`tailscale serve` (tailnet-only reverse proxy) isn't used either: it's an
HTTPS-only reverse proxy and can't carry WebRTC's UDP media or raw RTSP, so
it can't be the exposure mechanism here — see "Network exposure model" above
for what is used instead.

## Installing MediaMTX

```bash
# Confirm architecture (should print "aarch64" on a 64-bit Raspberry Pi OS install)
uname -m

# Download the current arm64 release — check
# https://github.com/bluenviron/mediamtx/releases for the latest version tag
MEDIAMTX_VERSION=v1.19.2
wget "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_arm64.tar.gz"
sudo mkdir -p /opt/mediamtx
sudo tar xzf "mediamtx_${MEDIAMTX_VERSION}_linux_arm64.tar.gz" -C /opt/mediamtx
sudo cp deploy/mediamtx/mediamtx.yml /opt/mediamtx/mediamtx.yml
```

Set the viewer/publisher passwords (never in the committed `mediamtx.yml`):

```bash
sudo mkdir -p /etc/catcam
sudo tee /etc/catcam/mediamtx.env >/dev/null <<'EOF'
MTX_AUTHINTERNALUSERS_1_PASS=choose-a-strong-viewer-password
MTX_AUTHINTERNALUSERS_2_PASS=choose-a-strong-publisher-password
EOF
sudo chmod 600 /etc/catcam/mediamtx.env
```

Install and start the service:

```bash
sudo cp deploy/systemd/catcam-stream.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now catcam-stream.service
```

**If using a USB camera** (`camera.type: usb`), also swap `mediamtx.yml`'s
`paths.cam` block to the commented-out `source: publisher` variant, and
enable the publisher. (`scripts/install.sh` — task 10's recommended install
path — does this swap automatically on a fresh install when it detects
`camera.type: usb` in `config.yaml`; the manual steps below are only needed
if you installed `mediamtx.yml` by hand instead.)

```bash
sudo cp deploy/systemd/catcam-publisher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now catcam-publisher.service
```

Do not enable `catcam-publisher.service` for CSI cameras — it will fail
immediately by design (see `src/catcam/stream_publisher.py`).

## Verifying

```bash
scripts/diagnose.sh
```

Or directly:

```bash
curl -s http://127.0.0.1:9997/v3/paths/get/cam
```

`"ready": true` means MediaMTX is actively receiving frames from the camera.

## Viewing the stream

- **Browser (WebRTC or HLS)**, from a device on your tailnet or LAN:
  - WebRTC: `http://<pi-tailscale-or-lan-ip>:8889/cam` (username
    `catcam-viewer`, the password set above)
  - HLS fallback: `http://<pi-tailscale-or-lan-ip>:8888/cam/`
- **VLC (RTSP)**: open network stream
  `rtsp://catcam-viewer:<password>@<pi-tailscale-or-lan-ip>:8554/cam`

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Stream path never becomes `ready` (CSI) | Check `rpicam-hello --list-cameras` (see `docs/hardware.md`); confirm no other process is holding the sensor. |
| Stream path never becomes `ready` (USB) | Check `systemctl status catcam-publisher.service` and its logs (`journalctl -u catcam-publisher.service`); confirm `camera.type: usb` in `config/config.yaml` and that ffmpeg is installed. |
| "Camera busy" errors from other components | Expected: once streaming is active, other components must read frames via MediaMTX's RTSP output, not `create_camera()` — see "Camera ownership contract" above. |
| WebRTC won't connect, HLS works fine | Confirm port `8189/udp` (`webrtcLocalUDPAddress`, the actual media port — separate from the `8889` signaling port) is reachable, not just `8889`. Falls back to HLS automatically in most players if WebRTC negotiation fails; browsers may need a manual reload to trigger the fallback. |
| Can't reach the stream from outside the LAN | Confirm `tailscale status` shows the Pi online and you're authenticated into the same tailnet; never rely on port-forwarding (never enable it). |
| 401/403 from MediaMTX | Wrong password, or connecting from an IP outside `authInternalUsers`' allowlist (see "Network exposure model"). |

## Alternative: plain WireGuard instead of Tailscale

Tailscale is used here because it's the simplest managed WireGuard mesh with
official ARM64 Raspberry Pi OS support, minimizing operational burden for a
single-owner system. If you'd rather not depend on Tailscale's coordination
server, a plain WireGuard tunnel to the Pi achieves the same "not on the
public internet" property — set it up per the
[WireGuard quickstart](https://www.wireguard.com/quickstart/), then use the
Pi's WireGuard-assigned IP wherever this document says "tailnet IP". The rest
of this document (MediaMTX config, auth, viewing) is unaffected either way.
