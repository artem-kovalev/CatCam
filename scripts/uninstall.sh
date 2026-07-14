#!/usr/bin/env bash
# Uninstall CatCam from a Raspberry Pi OS host.
#
# Usage:
#   sudo scripts/uninstall.sh            # keeps .env, config.yaml, storage/,
#                                         # mediamtx.yml/mediamtx.env, MediaMTX
#   sudo scripts/uninstall.sh --purge    # also removes the above (data loss!)
#
# Always stops/disables services and removes the sudoers rule and systemd
# unit files. Removing the `catcam` system user is always interactive
# (skipped entirely in a non-interactive/non-tty context) - it never happens
# just because --purge was passed, to avoid destroying evidence of *why*
# something failed (file ownership, logs) as a side effect of a data purge.
#
# Safe to re-run: every removal step tolerates the target already being gone.

set -uo pipefail

INSTALL_DIR=/opt/catcam
ETC_DIR=/etc/catcam
MEDIAMTX_DIR=/opt/mediamtx
SERVICE_USER=catcam

PURGE=0
for arg in "$@"; do
    case "${arg}" in
        --purge) PURGE=1 ;;
        *)
            echo "Unknown argument: ${arg}" >&2
            echo "Usage: $0 [--purge]" >&2
            exit 1
            ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: must be run as root (sudo scripts/uninstall.sh)." >&2
    exit 1
fi

echo "== CatCam uninstall (purge=${PURGE}) =="

# --- 1. Stop and disable services -------------------------------------------

echo
echo "-- Stopping services --"
for unit in catcam.service catcam-publisher.service catcam-stream.service; do
    systemctl stop "${unit}" 2>/dev/null || true
    systemctl disable "${unit}" 2>/dev/null || true
    echo "Stopped/disabled ${unit} (if it existed)."
done

# --- 2. Remove systemd units + sudoers rule ---------------------------------

echo
echo "-- Removing systemd units --"
rm -f /etc/systemd/system/catcam.service
rm -f /etc/systemd/system/catcam-stream.service
rm -f /etc/systemd/system/catcam-publisher.service
systemctl daemon-reload
echo "Removed unit files and reloaded systemd."

echo
echo "-- Removing sudoers rule --"
rm -f /etc/sudoers.d/catcam
echo "Removed /etc/sudoers.d/catcam (if it existed)."

# --- 3. Remove installed code + venv (config/storage kept unless --purge) --

echo
echo "-- Removing application code and venv --"
if [ -d "${INSTALL_DIR}" ]; then
    rm -rf "${INSTALL_DIR}/.venv"
    for entry in src deploy scripts docs requirements.txt pyproject.toml \
        README.md LICENSE .env.example; do
        rm -rf "${INSTALL_DIR:?}/${entry}"
    done
    echo "Removed venv and application code from ${INSTALL_DIR}."
    if [ "${PURGE}" -eq 1 ]; then
        rm -rf "${INSTALL_DIR}"
        echo "PURGE: removed ${INSTALL_DIR} entirely (including config/ and storage/)."
    else
        echo "Kept ${INSTALL_DIR}/config and ${INSTALL_DIR}/storage (pass --purge to remove them too)."
    fi
else
    echo "${INSTALL_DIR} does not exist - nothing to remove."
fi

echo
echo "-- Secrets and MediaMTX --"
if [ "${PURGE}" -eq 1 ]; then
    rm -rf "${ETC_DIR}"
    rm -rf "${MEDIAMTX_DIR}"
    echo "PURGE: removed ${ETC_DIR} (.env, mediamtx.env) and ${MEDIAMTX_DIR}."
else
    echo "Kept ${ETC_DIR} (.env, mediamtx.env) and ${MEDIAMTX_DIR} (pass --purge to remove them too)."
fi

# --- 4. Optional user removal (always interactive) --------------------------

echo
if [ -t 0 ] && getent passwd "${SERVICE_USER}" >/dev/null 2>&1; then
    read -r -p "Remove the '${SERVICE_USER}' system user account? [y/N] " reply
    case "${reply}" in
        [yY]|[yY][eE][sS])
            userdel "${SERVICE_USER}" 2>/dev/null || true
            groupdel "${SERVICE_USER}" 2>/dev/null || true
            echo "Removed user/group '${SERVICE_USER}'."
            ;;
        *)
            echo "Left user '${SERVICE_USER}' in place."
            ;;
    esac
else
    echo "Skipping interactive user-removal prompt (non-interactive shell or user doesn't exist)."
fi

echo
echo "== Uninstall complete =="
