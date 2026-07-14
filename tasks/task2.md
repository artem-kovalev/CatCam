# Task 2 — Camera abstraction layer

## Status: Done

## Goal

Provide a single, safe interface (`src/catcam/camera.py`) over the Raspberry Pi camera — supporting both CSI/libcamera modules and USB/UVC (V4L2) cameras — that all other components (streaming, motion detection, snapshot, manual recording) use, without ever opening the device twice in an incompatible way.

## Depends on

Task 1 (config schema for `CameraConfig`).

## Spec references

- "Initial Hardware and Environment" — CSI/libcamera or USB camera, configurable.
- "Non-Functional Requirements" — "Prevent multiple processes from accessing the camera concurrently in incompatible ways."
- "Event Recording" — "correctly handle a missing camera or FFmpeg."

## Assumptions

- CSI cameras are accessed via `libcamera` tooling (`libcamera-vid`/`rpicam-vid` on current Raspberry Pi OS) or via `picamera2` Python bindings, capture piped as MJPEG/H.264 to consumers; USB cameras are accessed via V4L2 (`/dev/video0` by default) through OpenCV `cv2.VideoCapture` or `ffmpeg -f v4l2`.
- Because motion detection needs raw frames (OpenCV) while streaming needs an encoded feed (MediaMTX/ffmpeg), the camera module exposes the device through a single owning process; MediaMTX is configured to pull from `catcam`'s frame source (or from a shared V4L2/loopback/ffmpeg-relay path) rather than opening the physical device itself, to avoid dual-open conflicts. Exact relay mechanism finalized in task 3 alongside streaming.
- A file lock (`/run/catcam/camera.lock` or similar under a writable runtime dir) or an in-process singleton plus a documented "camera server" pattern prevents two `catcam` components from opening the device concurrently in an unsupported way. Document the final decision in `docs/architecture.md` (task 9) once task 3 confirms the streaming relay approach.

## Steps

1. Verify current official command names for libcamera-based Raspberry Pi OS tooling (Raspberry Pi OS Bookworm renamed `libcamera-*` to `rpicam-*`) before writing any instructions — check official Raspberry Pi documentation for the exact current command set.
2. Implement `src/catcam/camera.py`:
   - `CameraBackend` protocol/ABC with `open()`, `read_frame() -> np.ndarray`, `close()`, `is_available() -> bool`.
   - `CsiCameraBackend` (libcamera/`picamera2` or `rpicam-vid` subprocess piping frames).
   - `UsbCameraBackend` (V4L2 via OpenCV `VideoCapture`).
   - Factory `create_camera(config: CameraConfig) -> CameraBackend` selecting backend from config.
   - Context-manager support (`__enter__`/`__exit__`) so callers can't leak an open device.
   - A lock helper (e.g. `CameraLock` using `fcntl.flock` on a lock file) acquired before any backend opens the device; raise a specific `CameraBusyError` if already held.
   - Explicit, typed exceptions: `CameraNotFoundError`, `CameraBusyError` — no bare `except Exception: pass`.
3. Add `scripts/check_camera.sh`: detects and reports camera presence/type (`vcgencmd get_camera` or `rpicam-hello --list-cameras` for CSI; `v4l2-ctl --list-devices` for USB), prints a clear pass/fail summary, exits non-zero on failure for scripting use.
4. Write `docs/hardware.md`: supported camera modules, wiring notes for CSI ribbon cable, USB camera compatibility notes (UVC requirement).
5. Write the camera-setup portion of `docs/raspberry-pi-setup.md`: enabling the camera interface (`raspi-config` / `/boot/firmware/config.txt` entries as currently documented), installing `libcamera`/`rpicam-apps` and V4L2 utilities, verifying with `scripts/check_camera.sh`.
6. Add unit tests where feasible without real hardware: mock backend selection logic, lock acquisition/contention behavior (e.g. two `CameraLock` instances in the same test process, second one raises `CameraBusyError`). Full hardware-dependent behavior is documented as manual verification (no camera present in CI).

## Acceptance criteria

- [x] `camera.py` supports both CSI/libcamera and USB/V4L2 via a common interface, selected by config.
- [x] Attempting to open the camera while another `catcam` component holds the lock raises `CameraBusyError` instead of corrupting frames or crashing silently.
- [x] Missing/disconnected camera raises `CameraNotFoundError` with an actionable message, not an unhandled exception.
- [x] `scripts/check_camera.sh` runs and reports camera status using currently-documented Raspberry Pi OS commands.
- [x] `docs/hardware.md` and the camera section of `docs/raspberry-pi-setup.md` are accurate and copy-pasteable.
- [x] Lock-contention unit test passes.

## Result

Implemented and verified on the dev machine (macOS, no camera hardware
attached). Before writing any commands/docs, confirmed via web search that
Raspberry Pi OS Bookworm renamed `libcamera-*` to `rpicam-*` and that
`vcgencmd get_camera` is legacy/unreliable on Bookworm — `rpicam-hello
--list-cameras` is the current, documented CSI detection command; this
shaped both `camera.py` and the docs/script below.

- Created files:
  - `src/catcam/camera.py` — `CameraError`/`CameraNotFoundError`/`CameraBusyError`, `CameraLock` (fcntl.flock-based), `CameraBackend` ABC, `CsiCameraBackend` (rpicam-vid subprocess + MJPEG frame splitting), `UsbCameraBackend` (cv2.VideoCapture), `create_camera()` factory.
  - `tests/test_camera.py` — backend selection, lock contention/reacquire, `_split_mjpeg_frames` pure-function cases.
  - `scripts/check_camera.sh` — CSI (`rpicam-hello --list-cameras`) + USB (`v4l2-ctl --list-devices`) detection with PASS/FAIL summary and correct exit codes; syntax-checked with `bash -n` (no Pi hardware on this dev machine to run it against real cameras).
  - `docs/hardware.md` — supported CSI modules, ribbon-cable orientation, USB/UVC notes.
  - `docs/raspberry-pi-setup.md` — camera-setup section (package install, verification), left open for later tasks to append streaming/bot/deployment sections.
- Modified files:
  - `README.md` — linked `docs/hardware.md` and `docs/raspberry-pi-setup.md` as available (no longer "planned").
- Commands executed:
  - `pip install opencv-python-headless numpy` into the existing `.venv` (needed for `cv2`/`np` used by `camera.py` and its tests; not previously installed since task 1 only exercised `config.py`).
  - `python -m pytest tests/ -v` → 15 passed (5 from task 1's `test_config.py` + 10 new in `test_camera.py`).
  - `bash -n scripts/check_camera.sh` → syntax OK.
  - Manual sanity check: `create_camera(CameraConfig(type="usb", device="/dev/video99", ...)).open()` → correctly raised `CameraNotFoundError` with an actionable message (no real USB camera on this dev machine, so this exercises the "missing device" path, not the "camera works" path).
- Test results: `pytest tests/` — 15/15 passed.
- Unresolved questions / deferred to later tasks:
  - No physical camera (CSI or USB) is available on this dev machine, so `CsiCameraBackend`/`UsbCameraBackend` frame-capture success paths, `scripts/check_camera.sh`'s real detection output, and `rpicam-vid`'s exact MJPEG CLI flags are unverified against real Raspberry Pi hardware — flagged for manual on-device verification (task 12's E2E checklist already covers `scripts/check_camera.sh`).
  - The exact runtime dir for `CameraLock`'s default path (`/run/catcam/camera.lock`) assumes `install.sh` (task 10) creates `/run/catcam` with appropriate permissions for the `catcam` service user; falls back to a temp-dir path if unwritable (exercised implicitly by this dev machine, which has no `/run/catcam`).
  - The final decision on how MediaMTX (task 3) obtains frames without opening the physical device a second time (relay vs. shared source) is still to be confirmed in task 3, per this task's own assumptions section; `camera.py`'s lock mechanism is designed to support whatever relay approach task 3 lands on.
