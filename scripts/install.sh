#!/usr/bin/env bash
# Clean-install CatCam on a Raspberry Pi OS (Bookworm+, Debian/apt-based) host.
#
# Run as root from a checkout of this repository:
#   sudo scripts/install.sh
#
# Safe to re-run: every step below only creates/writes what's missing, never
# overwrites an operator-edited file (.env, mediamtx.env, config.yaml,
# mediamtx.yml), and `systemctl enable`/`useradd`/`mkdir -p` are naturally
# idempotent.
#
# See docs/deployment.md for the full walkthrough this script implements.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

INSTALL_DIR=/opt/catcam
ETC_DIR=/etc/catcam
MEDIAMTX_DIR=/opt/mediamtx
MEDIAMTX_VERSION="${MEDIAMTX_VERSION:-v1.19.2}"
SERVICE_USER=catcam
SERVICE_GROUP=catcam

if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: must be run as root (sudo scripts/install.sh)." >&2
    exit 1
fi

echo "== CatCam install =="

# --- 1. System user/group ---------------------------------------------------

echo
echo "-- System user --"
if ! getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
    groupadd --system "${SERVICE_GROUP}"
    echo "Created group '${SERVICE_GROUP}'."
else
    echo "Group '${SERVICE_GROUP}' already exists."
fi

if ! getent passwd "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --gid "${SERVICE_GROUP}" --home-dir "${INSTALL_DIR}" \
        --shell /usr/sbin/nologin --groups video "${SERVICE_USER}"
    echo "Created user '${SERVICE_USER}' (member of 'video')."
else
    usermod -aG video "${SERVICE_USER}"
    echo "User '${SERVICE_USER}' already exists (ensured 'video' group membership)."
fi

# --- 2. Application files ----------------------------------------------------

echo
echo "-- Application files --"
mkdir -p "${INSTALL_DIR}"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude='.venv' --exclude='storage' --exclude='.git' \
        --exclude='config/config.yaml' \
        "${REPO_ROOT}/" "${INSTALL_DIR}/"
else
    # rsync ships on Raspberry Pi OS by default; this is a fallback only.
    for entry in src config deploy scripts docs requirements.txt pyproject.toml \
        README.md LICENSE .env.example; do
        if [ -e "${REPO_ROOT}/${entry}" ]; then
            cp -r "${REPO_ROOT}/${entry}" "${INSTALL_DIR}/${entry}"
        fi
    done
fi
echo "Synced repository to ${INSTALL_DIR}."

# --- 3. Python virtualenv + dependencies ------------------------------------

echo
echo "-- Python virtualenv --"
if [ ! -d "${INSTALL_DIR}/.venv" ]; then
    python3 -m venv "${INSTALL_DIR}/.venv"
    echo "Created venv at ${INSTALL_DIR}/.venv."
else
    echo "Venv already exists at ${INSTALL_DIR}/.venv."
fi
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip wheel --quiet
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" --quiet
echo "Installed/updated dependencies from requirements.txt."

# --- 4. Secrets and config ---------------------------------------------------

echo
echo "-- Secrets (.env) --"
mkdir -p "${ETC_DIR}"
if [ ! -f "${ETC_DIR}/.env" ]; then
    cp "${INSTALL_DIR}/.env.example" "${ETC_DIR}/.env"
    chmod 600 "${ETC_DIR}/.env"
    chown "${SERVICE_USER}:${SERVICE_GROUP}" "${ETC_DIR}/.env"
    echo "Created ${ETC_DIR}/.env from .env.example - EDIT IT before starting catcam.service:"
    echo "  sudo \$EDITOR ${ETC_DIR}/.env"
else
    echo "${ETC_DIR}/.env already exists - left untouched."
fi

echo
echo "-- Application config --"
if [ ! -f "${INSTALL_DIR}/config/config.yaml" ]; then
    cp "${INSTALL_DIR}/config/config.example.yaml" "${INSTALL_DIR}/config/config.yaml"
    echo "Created ${INSTALL_DIR}/config/config.yaml from config.example.yaml - review it, in"
    echo "particular camera.type (csi or usb)."
else
    echo "${INSTALL_DIR}/config/config.yaml already exists - left untouched."
fi

echo
echo "-- MediaMTX secrets (mediamtx.env) --"
if [ ! -f "${ETC_DIR}/mediamtx.env" ]; then
    cat >"${ETC_DIR}/mediamtx.env" <<'EOF'
# Passwords for MediaMTX's authInternalUsers (see deploy/mediamtx/mediamtx.yml
# and docs/streaming.md). Fill both in before starting catcam-stream.service.
MTX_AUTHINTERNALUSERS_1_PASS=
MTX_AUTHINTERNALUSERS_2_PASS=
EOF
    chmod 600 "${ETC_DIR}/mediamtx.env"
    chown "${SERVICE_USER}:${SERVICE_GROUP}" "${ETC_DIR}/mediamtx.env"
    echo "Created ${ETC_DIR}/mediamtx.env - EDIT IT before starting catcam-stream.service:"
    echo "  sudo \$EDITOR ${ETC_DIR}/mediamtx.env"
else
    echo "${ETC_DIR}/mediamtx.env already exists - left untouched."
fi

chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}"

# --- 5. MediaMTX --------------------------------------------------------------

echo
echo "-- MediaMTX --"
mkdir -p "${MEDIAMTX_DIR}"
if [ ! -x "${MEDIAMTX_DIR}/mediamtx" ]; then
    arch="$(uname -m)"
    case "${arch}" in
        aarch64) mtx_arch="arm64" ;;
        armv7l) mtx_arch="armv7" ;;
        *)
            echo "FAIL: unsupported architecture '${arch}' for MediaMTX auto-install." >&2
            echo "Install MediaMTX manually per docs/streaming.md, then re-run this script." >&2
            exit 1
            ;;
    esac
    tmp_tarball="$(mktemp -d)/mediamtx.tar.gz"
    wget -q -O "${tmp_tarball}" \
        "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_${mtx_arch}.tar.gz"
    tar xzf "${tmp_tarball}" -C "${MEDIAMTX_DIR}"
    rm -f "${tmp_tarball}"
    echo "Installed MediaMTX ${MEDIAMTX_VERSION} to ${MEDIAMTX_DIR}."
else
    echo "MediaMTX binary already present at ${MEDIAMTX_DIR}/mediamtx."
fi
if [ ! -f "${MEDIAMTX_DIR}/mediamtx.yml" ]; then
    cp "${REPO_ROOT}/deploy/mediamtx/mediamtx.yml" "${MEDIAMTX_DIR}/mediamtx.yml"
    echo "Installed ${MEDIAMTX_DIR}/mediamtx.yml."
else
    echo "${MEDIAMTX_DIR}/mediamtx.yml already exists - left untouched."
fi
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${MEDIAMTX_DIR}"

# --- 6. sudoers rule for /restart_service ------------------------------------

echo
echo "-- sudoers rule (Telegram /restart_service) --"
install -m 440 "${REPO_ROOT}/deploy/sudoers.d/catcam" /etc/sudoers.d/catcam
if ! visudo -c -f /etc/sudoers.d/catcam >/dev/null; then
    echo "FAIL: /etc/sudoers.d/catcam failed validation; removing it." >&2
    rm -f /etc/sudoers.d/catcam
    exit 1
fi
echo "Installed and validated /etc/sudoers.d/catcam."

# --- 7. systemd units ---------------------------------------------------------

echo
echo "-- systemd units --"
cp "${REPO_ROOT}/deploy/systemd/catcam.service" /etc/systemd/system/catcam.service
cp "${REPO_ROOT}/deploy/systemd/catcam-stream.service" /etc/systemd/system/catcam-stream.service
cp "${REPO_ROOT}/deploy/systemd/catcam-publisher.service" /etc/systemd/system/catcam-publisher.service
systemctl daemon-reload

camera_type="$("${INSTALL_DIR}/.venv/bin/python" -c \
    "import yaml; print(yaml.safe_load(open('${INSTALL_DIR}/config/config.yaml'))['camera']['type'])")"
echo "Detected camera.type='${camera_type}' from config.yaml."

systemctl enable --now catcam-stream.service
if [ "${camera_type}" = "usb" ]; then
    systemctl enable --now catcam-publisher.service
    echo "Enabled catcam-publisher.service (camera.type: usb)."
else
    systemctl disable --now catcam-publisher.service >/dev/null 2>&1 || true
    echo "Left catcam-publisher.service disabled (camera.type: csi doesn't need it)."
fi
systemctl enable --now catcam.service

echo "Enabled and started catcam.service, catcam-stream.service."

# --- 8. Smoke test -------------------------------------------------------------

echo
echo "-- Camera smoke test --"
if "${REPO_ROOT}/scripts/check_camera.sh"; then
    echo "PASS: camera detected."
else
    echo "WARNING: no camera detected yet - connect it and re-run scripts/diagnose.sh."
fi

echo
echo "== Install complete =="
echo "Next steps:"
echo "  1. Fill in real secrets: sudo \$EDITOR ${ETC_DIR}/.env"
echo "  2. Fill in MediaMTX passwords: sudo \$EDITOR ${ETC_DIR}/mediamtx.env"
echo "  3. Review ${INSTALL_DIR}/config/config.yaml for your hardware"
echo "  4. sudo systemctl restart catcam.service catcam-stream.service"
echo "  5. Run scripts/diagnose.sh to verify everything end-to-end"
