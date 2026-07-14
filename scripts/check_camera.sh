#!/usr/bin/env bash
# Detect and report CSI/USB camera availability on Raspberry Pi OS (Bookworm+).
#
# CSI detection uses `rpicam-hello --list-cameras` (rpicam-apps). Note:
# `vcgencmd get_camera` is legacy-stack only and unreliable on Bookworm, so it
# is intentionally NOT used here.
# USB detection uses `v4l2-ctl --list-devices` (v4l-utils package).
#
# Exit status: 0 if at least one usable camera was found, 1 otherwise.

set -uo pipefail

csi_found=0
usb_found=0

echo "== CatCam camera check =="

echo
echo "-- CSI camera (rpicam-apps) --"
if command -v rpicam-hello >/dev/null 2>&1; then
    csi_output="$(rpicam-hello --list-cameras 2>&1)"
    csi_status=$?
    echo "${csi_output}"
    if [ "${csi_status}" -eq 0 ] && ! echo "${csi_output}" | grep -qi "no cameras available"; then
        echo "PASS: CSI camera detected."
        csi_found=1
    else
        echo "FAIL: no CSI camera detected."
    fi
else
    echo "SKIP: rpicam-hello not found (install rpicam-apps for CSI camera support)."
fi

echo
echo "-- USB camera (V4L2) --"
if command -v v4l2-ctl >/dev/null 2>&1; then
    usb_output="$(v4l2-ctl --list-devices 2>&1)"
    usb_status=$?
    echo "${usb_output}"
    if [ "${usb_status}" -eq 0 ] && [ -n "$(echo "${usb_output}" | tr -d '[:space:]')" ]; then
        echo "PASS: USB camera device(s) detected."
        usb_found=1
    else
        echo "FAIL: no USB camera detected."
    fi
else
    echo "SKIP: v4l2-ctl not found (install v4l-utils for USB camera support)."
fi

echo
echo "== Summary =="
if [ "${csi_found}" -eq 1 ] || [ "${usb_found}" -eq 1 ]; then
    echo "PASS: at least one camera is available."
    exit 0
else
    echo "FAIL: no camera detected (CSI or USB)."
    exit 1
fi
