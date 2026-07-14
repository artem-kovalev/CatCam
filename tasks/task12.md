# Task 12 — End-to-end verification & acceptance sign-off

## Status: Not started

## Goal

Confirm the assembled system meets every item in the spec's "General Acceptance Criteria," run the full automated test suite, and close out `tasks/summary.md`.

## Depends on

Tasks 1–11 (final gate).

## Spec references

- "General Acceptance Criteria" (full list).

## Assumptions

- Full hardware-level verification (real Raspberry Pi 4, real camera, real Telegram chat) happens on the actual device; this task's automated portion (`pytest`) runs wherever development happens, while the manual checklist below is executed on-device before sign-off.

## Steps

1. Run the complete automated test suite (`pytest tests/`) and confirm all tests from tasks 1, 2, 4, 5, 6, 7, 8 pass together (not just individually) — check for cross-test interference (e.g. shared state files).
2. Execute the manual on-device checklist, recording actual results (not assumptions):
   - Raspberry Pi detects the camera (`scripts/check_camera.sh` passes).
   - Owner can reach the video stream via the documented secure method (Tailscale + MediaMTX) from a device outside the LAN.
   - Physically triggering motion (e.g. moving an object in frame) results in a recorded clip.
   - The clip arrives in the private Telegram chat as a circular video note, not a regular video.
   - A second motion event within the cooldown window is logged (check logs) but does **not** produce a second Telegram message.
   - `/cooldown 5` via the bot changes the interval; restarting `catcam.service` and checking `/cooldown` again shows it persisted as 5.
   - A message sent from a second, non-owner Telegram account is rejected/ignored by the bot.
   - `sudo reboot` (or equivalent) results in both `catcam.service` and `catcam-stream.service` running afterward with no manual step.
   - Temporarily disabling networking (e.g. `sudo ip link set wlan0 down` then back up, or disconnecting Tailscale) during a pending delivery results in the event being delivered once connectivity returns, via the retry queue.
   - `scripts/diagnose.sh` output is legible and correctly identifies at least one deliberately induced failure (e.g. temporarily renaming the FFmpeg binary) as a smoke test of the diagnostics themselves.
3. Update `tasks/summary.md`: mark every task's status as Done (or Blocked, with the specific blocker noted) based on actual completion, not aspiration.
4. Record final unresolved questions/known limitations (if any) in this file's Result section and cross-reference them from `docs/troubleshooting.md` if user-facing.

## Acceptance criteria

- [ ] Every bullet under the spec's "General Acceptance Criteria" is checked off with concrete evidence (command output, screenshot description, or log excerpt referenced in this file's Result section).
- [ ] `pytest tests/` passes in full.
- [ ] `tasks/summary.md` accurately reflects final status of every task.
- [ ] No spec requirement is left silently unmet — anything not fully satisfied is explicitly called out here as a known limitation.

## Result

_Not started._

- Created files: —
- Modified files: —
- Commands executed: —
- Test results: —
- Unresolved questions: —
