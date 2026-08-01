#!/bin/sh
set -eu

SERVER_URL="${1:?usage: WCM_ENROLLMENT_TOKEN=... install.sh wss://host/ws/agent https://host/api/v2 /path/internal-ca.crt /path/key-id.pub}"
API_URL="${2:?API URL is required}"
CA_CERT="${3:?internal CA certificate path is required}"
UPDATE_PUBLIC_KEY="${4:?Ed25519 update public key path is required}"

INSTALL_DIR="/opt/web-cluster-agent"
RUNTIME_DIR="$INSTALL_DIR/runtime"
RELEASES_DIR="$INSTALL_DIR/releases"
BOOTSTRAP_DIR="$RELEASES_DIR/bootstrap-3.1.0"
PAYLOAD_DIR="$BOOTSTRAP_DIR/payload"
TRUSTED_KEYS_DIR="$INSTALL_DIR/trusted_update_keys"
DATA_DIR="/var/lib/web-cluster-agent"
SPOOL_DIR="$DATA_DIR/update-spool"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
KEY_LEAF="$(basename -- "$UPDATE_PUBLIC_KEY")"
if [ -f "$INSTALL_DIR/active.json" ]; then
    FIRST_BOOTSTRAP=0
else
    FIRST_BOOTSTRAP=1
fi

command -v python3 >/dev/null 2>&1 || { echo "Python 3 is required" >&2; exit 1; }
test -f "$CA_CERT" || { echo "CA certificate not found" >&2; exit 1; }
test -f "$UPDATE_PUBLIC_KEY" || { echo "Ed25519 update public key not found" >&2; exit 1; }
case "$KEY_LEAF" in
    *.pub) ;;
    *) echo "Update public key must be named <key-id>.pub" >&2; exit 1 ;;
esac
KEY_ID="${KEY_LEAF%.pub}"
case "$KEY_ID" in
    ""|*[!A-Za-z0-9_.-]*) echo "Update public key ID is invalid" >&2; exit 1 ;;
esac
[ "${#KEY_ID}" -le 64 ] || { echo "Update public key ID is too long" >&2; exit 1; }

if ! id wcm-agent >/dev/null 2>&1; then
    sudo useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin wcm-agent
fi
sudo systemctl stop web-cluster-agent.service web-cluster-agent-updater.service \
    >/dev/null 2>&1 || true
if getent group docker >/dev/null 2>&1; then
    sudo usermod -aG docker wcm-agent
fi

sudo install -d -m 0755 -o root -g root \
    "$INSTALL_DIR" "$RUNTIME_DIR" "$RELEASES_DIR" "$TRUSTED_KEYS_DIR"
sudo install -d -m 0750 -o wcm-agent -g wcm-agent "$DATA_DIR"
sudo install -d -m 0750 -o root -g wcm-agent "$SPOOL_DIR"
sudo install -d -m 0750 -o wcm-agent -g wcm-agent \
    "$SPOOL_DIR/incoming" "$SPOOL_DIR/packages" "$SPOOL_DIR/requests" \
    "$SPOOL_DIR/agent-status" "$SPOOL_DIR/health" "$SPOOL_DIR/cancel"
sudo install -d -m 0750 -o root -g wcm-agent \
    "$SPOOL_DIR/processing" "$SPOOL_DIR/helper-status"
sudo install -m 0644 -o root -g root "$SCRIPT_DIR/launcher.py" "$RUNTIME_DIR/launcher.py"
sudo install -m 0644 -o root -g root "$SCRIPT_DIR/update_helper.py" "$RUNTIME_DIR/update_helper.py"
sudo install -m 0644 -o root -g root "$SCRIPT_DIR/update_package.py" "$RUNTIME_DIR/update_package.py"
sudo install -m 0644 -o root -g root "$SCRIPT_DIR/requirements.txt" "$RUNTIME_DIR/requirements.txt"
if [ "$FIRST_BOOTSTRAP" -eq 1 ]; then
    sudo install -d -m 0755 -o root -g root "$BOOTSTRAP_DIR" "$PAYLOAD_DIR"
    for source in "$SCRIPT_DIR"/*.py; do
        [ "$(basename -- "$source")" = "updater.py" ] && continue
        sudo install -m 0644 -o root -g root "$source" "$PAYLOAD_DIR/$(basename -- "$source")"
    done
    sudo install -m 0644 -o root -g root "$SCRIPT_DIR/requirements.txt" "$PAYLOAD_DIR/requirements.txt"
fi
sudo install -m 0644 -o root -g root "$CA_CERT" "$INSTALL_DIR/ca.crt"
sudo install -m 0644 -o root -g root "$UPDATE_PUBLIC_KEY" "$TRUSTED_KEYS_DIR/$KEY_LEAF"

if [ ! -x "$RUNTIME_DIR/venv/bin/python" ]; then
    sudo python3 -m venv --copies "$RUNTIME_DIR/venv"
fi
if [ "$FIRST_BOOTSTRAP" -eq 1 ] && [ ! -x "$BOOTSTRAP_DIR/venv/bin/python" ]; then
    sudo python3 -m venv --copies "$BOOTSTRAP_DIR/venv"
fi
sudo "$RUNTIME_DIR/venv/bin/python" -I -m pip install \
    --disable-pip-version-check -r "$RUNTIME_DIR/requirements.txt" --quiet
if [ "$FIRST_BOOTSTRAP" -eq 1 ]; then
    sudo "$BOOTSTRAP_DIR/venv/bin/python" -I -m pip install \
        --disable-pip-version-check -r "$PAYLOAD_DIR/requirements.txt" --quiet
fi

if [ "$FIRST_BOOTSTRAP" -eq 1 ]; then
sudo python3 - "$INSTALL_DIR/active.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
value = {
    "schema_version": 1,
    "release_id": "bootstrap-3.1.0",
    "version": "3.1.0",
    "entrypoint": "main.py",
    "activation_execution_id": "",
    "activation_operation": "",
}
temporary = path.with_name(".active.json.tmp")
temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
temporary.chmod(0o644)
temporary.replace(path)
PY
fi

if [ ! -f "$DATA_DIR/agent_config.json" ]; then
    : "${WCM_ENROLLMENT_TOKEN:?WCM_ENROLLMENT_TOKEN is required for first enrollment}"
    sudo -u wcm-agent env \
        WCM_ENROLLMENT_TOKEN="$WCM_ENROLLMENT_TOKEN" \
        WCM_AGENT_DATA_DIR="$DATA_DIR" \
        WCM_UPDATE_SPOOL_DIR="$SPOOL_DIR" \
        WCM_UPDATE_TRUSTED_KEYS_DIR="$TRUSTED_KEYS_DIR" \
        WCM_UPDATE_ACTIVE_POINTER="$INSTALL_DIR/active.json" \
        WCM_ENABLE_AGENT_UPDATES=true \
        "$BOOTSTRAP_DIR/venv/bin/python" -I "$PAYLOAD_DIR/main.py" \
        --enroll-only --server "$SERVER_URL" --api "$API_URL" \
        --ca-cert "$INSTALL_DIR/ca.crt" --data-dir "$DATA_DIR"
fi
sudo find "$DATA_DIR" -xdev -path "$SPOOL_DIR" -prune -o \
    -exec chown wcm-agent:wcm-agent {} +
sudo chown root:wcm-agent "$SPOOL_DIR"
sudo chown -R root:wcm-agent "$SPOOL_DIR/processing" "$SPOOL_DIR/helper-status"
sudo chown -R wcm-agent:wcm-agent \
    "$SPOOL_DIR/incoming" "$SPOOL_DIR/packages" "$SPOOL_DIR/requests" \
    "$SPOOL_DIR/agent-status" "$SPOOL_DIR/health" "$SPOOL_DIR/cancel"
sudo chmod 0750 "$DATA_DIR" "$SPOOL_DIR" "$SPOOL_DIR"/*

sudo tee /etc/systemd/system/web-cluster-agent.service >/dev/null <<EOF
[Unit]
Description=Web Cluster Manager Agent
After=network-online.target docker.service web-cluster-agent-updater.service
Wants=network-online.target

[Service]
Type=simple
User=wcm-agent
Group=wcm-agent
ExecStart=$RUNTIME_DIR/venv/bin/python -I $RUNTIME_DIR/launcher.py --install-root $INSTALL_DIR --agent-data-dir $DATA_DIR --server $SERVER_URL --api $API_URL --ca-cert $INSTALL_DIR/ca.crt
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/web-cluster-agent-updater.service >/dev/null <<EOF
[Unit]
Description=Privileged Web Cluster Agent Update Helper
After=local-fs.target
Before=web-cluster-agent.service

[Service]
Type=simple
User=root
Group=root
ExecStart=$RUNTIME_DIR/venv/bin/python -I $RUNTIME_DIR/update_helper.py --install-root $INSTALL_DIR --spool-root $SPOOL_DIR --trusted-keys $TRUSTED_KEYS_DIR
Restart=always
RestartSec=5
PrivateNetwork=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$INSTALL_DIR $DATA_DIR /run
RestrictAddressFamilies=AF_UNIX
LockPersonality=true
MemoryDenyWriteExecute=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now web-cluster-agent-updater.service
sudo systemctl enable --now web-cluster-agent.service
echo "Trusted Agent bootstrap installed: $SERVER_URL"
