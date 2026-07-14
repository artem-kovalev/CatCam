# Task 7 — Cooldown / delivery rate limiting

## Status: Done

## Goal

Enforce a configurable cooldown between successful Telegram deliveries, persisted across restarts, with enable/disable control.

## Depends on

Task 1 (config schema/defaults).

## Spec references

- "Delivery Rate Limit" (full section).

## Assumptions

- Persistence via a small local state file (e.g. `storage/state/cooldown_state.json`) rather than a database — matches the single-owner, single-device scope and avoids adding a DB dependency; stores `last_delivery_at` (ISO 8601 UTC timestamp), `interval_minutes`, and `notifications_enabled`.
- "Cooldown measured from last successful delivery" means the timer only resets on confirmed Telegram API success (not on send attempt), consistent with the retry-queue design in task 9.
- Validation range enforced strictly: 1–1440 minutes inclusive; out-of-range input via the bot is rejected with an explanatory reply, state unchanged.

## Steps

1. Implement `src/catcam/cooldown.py`:
   - `CooldownManager` backed by the JSON state file, with atomic writes (write to temp file + `os.replace`) to avoid corruption on power loss (relevant on a Raspberry Pi with no UPS).
   - `is_in_cooldown() -> bool`, `record_delivery_success()` (updates `last_delivery_at` to now), `get_interval_minutes() -> int`, `set_interval_minutes(value: int)` (validates 1–1440, raises `ValueError`/`CooldownConfigError` otherwise), `enable_notifications()`, `disable_notifications()`, `notifications_enabled() -> bool`.
   - Loads existing state on startup if present; otherwise initializes from config defaults (interval 60, enabled True, no prior delivery ⇒ not in cooldown).
2. Add `tests/test_cooldown.py`: fresh state is not in cooldown; after `record_delivery_success()`, `is_in_cooldown()` is true until the interval elapses (use monkeypatched/injectable clock rather than real sleeps); setting an invalid interval (0, 1441, non-integer) raises and does not change stored state; state persists across `CooldownManager` re-instantiation from the same file (simulating a restart); disabling notifications is independently queryable from the cooldown timer (motion during "disabled" state must not send regardless of cooldown, per spec — disable/enable is a separate gate ORed with the cooldown check by the caller in task 9).

## Acceptance criteria

- [x] Default cooldown is 60 minutes when no prior state exists.
- [x] Cooldown state (interval, last delivery time, enabled flag) survives a process restart (re-read from the state file).
- [x] Interval changes are validated to the 1–1440 range; invalid values are rejected without corrupting existing state.
- [x] `is_in_cooldown()` correctly reflects elapsed time since the last **successful** delivery only.
- [x] `tests/test_cooldown.py` passes, including the restart-persistence case.

## Result

Implemented `src/catcam/cooldown.py`:

- `CooldownManager(config: CooldownConfig, clock: Optional[Callable[[], datetime]] = None)` —
  `clock` defaults to `datetime.now(timezone.utc)` but is injectable, which
  is what lets `tests/test_cooldown.py` simulate elapsed time deterministically
  (a `FakeClock` test helper) instead of real `sleep()`s.
- On construction, loads `config.state_file` if it exists (tolerating a
  missing/corrupt file by falling back to config defaults and logging an
  error, rather than crashing startup); otherwise starts from
  `default_minutes`, `notifications_enabled=True`, no prior delivery.
- `is_in_cooldown() -> bool` — `False` if no delivery has ever been
  recorded; otherwise compares `clock() - last_delivery_at` against the
  current interval.
- `record_delivery_success()` — stamps `last_delivery_at = clock()` and
  persists immediately.
- `get_interval_minutes()` / `set_interval_minutes(value)` — the setter
  validates `value` is a real `int` (explicitly rejecting `bool`, which is
  an `int` subclass in Python, and rejecting floats/strings/`None`) and
  within `[config.min_minutes, config.max_minutes]`, raising
  `CooldownConfigError` (a `ValueError` subclass, satisfying the Steps
  text's "`ValueError`/`CooldownConfigError`" wording as a single type)
  otherwise, leaving previously-stored state untouched on rejection.
- `notifications_enabled()` / `enable_notifications()` /
  `disable_notifications()` — a separate persisted flag, deliberately not
  combined into `is_in_cooldown()`'s return value; per the spec and this
  task's own Assumptions, "disabled" and "in cooldown" are independent
  gates a caller (task 9) must OR together itself.
- All state mutations persist via an atomic write: `tempfile.mkstemp()` in
  the same directory as `state_file`, then `os.replace()` — survives a
  power loss mid-write without corrupting the previously-good file; the
  temp file is cleaned up if the write/replace fails.

`CooldownConfig`/`config.py` needed no schema changes — `default_minutes`,
`min_minutes`, `max_minutes`, `state_file` already existed from task 1.

- Created files:
  - `src/catcam/cooldown.py`
  - `tests/test_cooldown.py`
- Modified files:
  - `docs/configuration.md` (added a "Tuning guidance" block under the
    `cooldown` table: success-only timer reset, atomic persistence,
    notifications-as-independent-gate, interval validation rules)
  - `tasks/task7.md` (this file: Status, acceptance criteria, Result)
  - `tasks/summary.md` (status table)
- Commands executed:
  - `python -m pytest tests/test_cooldown.py -v` → 15/15 passed
  - `python -m pytest tests/ -v` → 67 passed, 1 skipped (full suite, tasks
    1–7 combined; the 1 skip is task 6's pre-existing real-FFmpeg
    integration test, unrelated to this task)
- Test results: all passing. `tests/test_cooldown.py` (15 tests) covers:
  fresh state not in cooldown; default interval is 60 with no prior state;
  notifications enabled by default; `is_in_cooldown()` transitions from
  `True` to `False` exactly as the injected clock crosses the interval
  boundary; valid interval changes take effect; a parametrized set of
  invalid interval values (`0`, `1441`, `-5`, `30.5`, `"30"`, `None`) all
  raise and leave the stored interval unchanged; full state (interval,
  enabled flag, cooldown status) survives re-instantiating a fresh
  `CooldownManager` against the same state file (simulated restart);
  disabling/enabling notifications is independently queryable from cooldown
  status in both directions; an invalid `set_interval_minutes()` call after
  a valid one doesn't corrupt the already-persisted valid state; no leftover
  `.cooldown-*.tmp` files remain after a successful atomic write.
- Unresolved questions:
  - **Real Raspberry Pi power-loss behavior** (the actual motivation for
    atomic writes) is inherently unverifiable without hardware — the atomic
    write *mechanism* (temp file + `os.replace`) is standard and portable,
    but a true SIGKILL-mid-write / power-cut scenario is not simulated by
    any test here. Not flagged for task 12 specifically (this is a
    filesystem-semantics assumption, not a camera/FFmpeg one) but worth
    keeping in mind if real-world corruption is ever observed.
  - **The OR-together contract for cooldown + notifications-enabled** is
    documented here and in `docs/configuration.md`, but not yet exercised
    end-to-end since task 9 (the orchestrator that will actually call both
    `is_in_cooldown()` and `notifications_enabled()` before deciding to
    send) doesn't exist yet — this task only guarantees the two methods are
    independently correct, not that task 9 wires them together correctly
    once it's built.
