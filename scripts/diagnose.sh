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

# Prefer the project venv (has pyyaml/python-dotenv/etc.) over system python3,
# which on a deployed install has no reason to have those installed.
PYTHON_BIN="python3"
if [ -x "${REPO_ROOT}/.venv/bin/python3" ]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/bin/python3"
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
    if (cd "${REPO_ROOT}" && PYTHONPATH="${REPO_ROOT}/src" "${PYTHON_BIN}" -m catcam.stream_health); then
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
    if (cd "${REPO_ROOT}" && PYTHONPATH="${REPO_ROOT}/src" "${PYTHON_BIN}" -m catcam.health); then
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
if [ -f /etc/catcam/.env ]; then
    echo "PASS: /etc/catcam/.env exists."
elif [ -f "${REPO_ROOT}/.env" ]; then
    echo "PASS: ${REPO_ROOT}/.env exists (dev-mode path)."
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
