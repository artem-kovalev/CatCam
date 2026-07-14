# Task 11 — Security & documentation completion

## Status: Not started

## Goal

Close out remaining documentation (security posture, troubleshooting, testing guide) and audit the whole repository for secret leakage or undocumented steps before final verification.

## Depends on

Tasks 1–10 (documents/audits everything built so far).

## Spec references

- "Private Telegram Bot" — secrets handling.
- "Non-Functional Requirements" — no real secrets in repo.
- "General Acceptance Criteria" — logs/diagnostics make failures identifiable; documentation has no hidden steps or unexplained variables.

## Assumptions

- Security review is a manual audit pass (grep for token-like strings, review `.gitignore` coverage, review log output samples) rather than an automated scanner, given repo size — consistent with "select and fully implement" being about the runtime system, not tooling.

## Steps

1. Write `docs/security.md`: threat model (private single-owner bot, LAN + VPN-only stream, no public ports), secret storage (`.env` only, file permissions recommendation e.g. `chmod 600`), token rotation procedure (revoke via BotFather, update `.env`, restart service), what's authorized vs denied for non-owner users, `/restart_service` privilege scoping recap from task 8/10, recommendation to keep Raspberry Pi OS and Tailscale updated.
2. Write `docs/troubleshooting.md`: symptom → cause → fix table covering at least: camera not detected, FFmpeg missing, stream unreachable, bot not responding, unauthorized-user complaints, cooldown not resetting, disk full, video note not rendering as circular, log location and how to read them (referencing `scripts/diagnose.sh` from task 10 as the first troubleshooting step).
3. Write `docs/testing.md`: how to run `pytest` locally, what's unit-tested vs requires real hardware for manual verification, and the manual end-to-end test procedure (trigger motion in front of camera → confirm video note arrives → confirm second event during cooldown doesn't send → confirm `/cooldown 5` then restart persists 5).
4. Audit pass: grep the repo for accidental secrets (`grep -rniE "bot[0-9]+:|AAF[a-z0-9_-]{30,}"` pattern typical of Telegram tokens, and for the literal env var names to ensure only `.env.example`/docs reference them, never a real value), confirm `.gitignore` excludes `.env`, `config/config.yaml`, `storage/`, logs; confirm no handler/logger in the codebase interpolates the raw token or full `Update` objects.
5. Finalize `README.md`: project summary, feature list matching the spec's "Project Goal," quick links to every `docs/*.md`, and a condensed quick-start pointing at `docs/deployment.md`.

## Acceptance criteria

- [ ] `docs/security.md`, `docs/troubleshooting.md`, `docs/testing.md` exist and are complete/accurate.
- [ ] Repo-wide grep finds zero real secret values; `.gitignore` covers every path that could contain one.
- [ ] `README.md` accurately reflects the implemented feature set with working links to all docs.
- [ ] No doc references a file, command, service name, or env var that doesn't actually exist in the repo (cross-check against tasks 1–10's Result sections).

## Result

_Not started._

- Created files: —
- Modified files: —
- Commands executed: —
- Test results: —
- Unresolved questions: —
