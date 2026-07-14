#!/usr/bin/env bash
# Aggregate health check for CatCam.
#
# Runs, in order: camera detection, FFmpeg presence, catcam-stream.service
# (MediaMTX) status, catcam-publisher.service status (SKIP if not enabled —
# expected for CSI deployments), a MediaMTX control-API readiness check,
# catcam.service (main application) status, storage/disk quota, .env
# presence (existence only — contents are never printed), and a tail of the
# most recent log lines (informational, not counted in the pass/fail total).
#
# Works both on a deployed install (paths under /opt/catcam, /etc/catcam) and
# from a repo checkout in local/dev use (falls back to repo-relative paths).
#
# Exit status: 0 if every applicable check passed, 1 otherwise.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Prefer a venv that actually has pyyaml/python-dotenv/etc. over system
# python3, which has no reason to have those installed. `diagnose.sh` is
# often run from a plain git checkout (no venv of its own) even on a real
# deployed Pi where the actual venv+secrets live at /opt/catcam - so prefer
# that deployed install's venv/source over the checkout's own (usually
# absent) venv whenever it's present, regardless of where this script itself
# was invoked from. CATCAM_SRC_ROOT (not REPO_ROOT) is used for the actual
# `python -m catcam.*` invocations below so config/storage paths resolve
# against the real deployed install, not the checkout.
PYTHON_BIN="python3"
CATCAM_SRC_ROOT="${REPO_ROOT}"
if [ -x /opt/catcam/.venv/bin/python3 ]; then
    PYTHON_BIN=/opt/catcam/.venv/bin/python3
    CATCAM_SRC_ROOT=/opt/catcam
elif [ -x "${REPO_ROOT}/.venv/bin/python3" ]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/bin/python3"
fi

# On a deployed install, secrets live in /etc/catcam/.env - catcam.service
# only sees them via systemd's `EnvironmentFile=` directive, which populates
# the real process environment *before* Python starts. That mechanism doesn't
# exist for a manual `python -m catcam.*` invocation here, and `load_dotenv()`
# (called with no explicit path) searches upward from config.py's own file
# location (e.g. /opt/catcam/src/catcam/), which never reaches /etc/catcam -
# so without sourcing it ourselves first, every check below would spuriously
# fail with "Missing required environment variable(s)" even on a fully
# correctly configured install.
ENV_FILE=""
if [ -f /etc/catcam/.env ]; then
    ENV_FILE=/etc/catcam/.env
elif [ -f "${REPO_ROOT}/.env" ]; then
    ENV_FILE="${REPO_ROOT}/.env"
fi

overall_status=0

echo "== CatCam diagnostics =="

echo
echo "-- Camera --"
if "${SCRIPT_DIR}/check_camera.sh"; then
    echo "PASS: camera check."
else
    echo "FAIL: camera check."
    overall_status=1
fi

echo
echo "-- FFmpeg --"
if command -v ffmpeg >/dev/null 2>&1; then
    echo "PASS: ffmpeg found ($(command -v ffmpeg))."
else
    echo "FAIL: ffmpeg not found - required for recording (task 5) and video-note conversion (task 6)."
    overall_status=1
fi

echo
echo "-- catcam-stream.service (MediaMTX) --"
if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet catcam-stream.service; then
        echo "PASS: catcam-stream.service is active."
    else
        echo "FAIL: catcam-stream.service is not active."
        overall_status=1
    fi
else
    echo "SKIP: systemctl not found (not running on a systemd host)."
fi

echo
echo "-- catcam-publisher.service (USB cameras only) --"
if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-enabled --quiet catcam-publisher.service 2>/dev/null; then
        if systemctl is-active --quiet catcam-publisher.service; then
            echo "PASS: catcam-publisher.service is active."
        else
            echo "FAIL: catcam-publisher.service is enabled but not active."
            overall_status=1
        fi
    else
        echo "SKIP: catcam-publisher.service is not enabled (expected for CSI cameras)."
    fi
else
    echo "SKIP: systemctl not found (not running on a systemd host)."
fi

echo
echo "-- MediaMTX stream readiness --"
if command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    if (
        cd "${CATCAM_SRC_ROOT}" \
        && { [ -z "${ENV_FILE}" ] || { set -a; \
             # shellcheck disable=SC1090
             . "${ENV_FILE}"; set +a; }; } \
        && PYTHONPATH="${CATCAM_SRC_ROOT}/src" "${PYTHON_BIN}" -m catcam.stream_health
    ); then
        :
    else
        overall_status=1
    fi
else
    echo "SKIP: ${PYTHON_BIN} not found."
fi

echo
echo "-- catcam.service (main application) --"
if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet catcam.service; then
        echo "PASS: catcam.service is active."
    else
        echo "FAIL: catcam.service is not active."
        overall_status=1
    fi
else
    echo "SKIP: systemctl not found (not running on a systemd host)."
fi

echo
echo "-- Storage / disk quota --"
if command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    if (
        cd "${CATCAM_SRC_ROOT}" \
        && { [ -z "${ENV_FILE}" ] || { set -a; \
             # shellcheck disable=SC1090
             . "${ENV_FILE}"; set +a; }; } \
        && PYTHONPATH="${CATCAM_SRC_ROOT}/src" "${PYTHON_BIN}" -m catcam.health
    ); then
        echo "PASS: storage/disk usage within limits."
    else
        echo "FAIL: storage/disk usage check failed (see above)."
        overall_status=1
    fi
else
    echo "SKIP: ${PYTHON_BIN} not found."
fi

echo
echo "-- Environment file (.env) --"
# Existence only - never print contents, this file holds the Telegram bot token.
if [ -n "${ENV_FILE}" ]; then
    echo "PASS: ${ENV_FILE} exists."
else
    echo "FAIL: no .env found at /etc/catcam/.env or ${REPO_ROOT}/.env."
    overall_status=1
fi

echo
echo "-- Recent log lines (informational) --"
log_file=""
if [ -f /opt/catcam/storage/logs/catcam.log ]; then
    log_file=/opt/catcam/storage/logs/catcam.log
elif [ -f "${REPO_ROOT}/storage/logs/catcam.log" ]; then
    log_file="${REPO_ROOT}/storage/logs/catcam.log"
fi
if [ -n "${log_file}" ]; then
    echo "Last 20 lines of ${log_file}:"
    tail -n 20 "${log_file}"
else
    echo "SKIP: no log file found yet (nothing logged since last start, or not deployed yet)."
fi

echo
echo "== Summary =="
if [ "${overall_status}" -eq 0 ]; then
    echo "PASS: all applicable checks passed."
else
    echo "FAIL: one or more checks failed (see above)."
fi

exit "${overall_status}"
