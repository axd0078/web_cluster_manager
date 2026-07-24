#!/bin/sh
set -eu

SERVER_URL="${1:?usage: WCM_ENROLLMENT_TOKEN=... install.sh wss://host/ws/agent https://host/api/v2 /path/internal-ca.crt}"
API_URL="${2:?API URL is required}"
CA_CERT="${3:?internal CA certificate path is required}"
: "${WCM_ENROLLMENT_TOKEN:?WCM_ENROLLMENT_TOKEN environment variable is required}"

INSTALL_DIR="/opt/web-cluster-agent"
DATA_DIR="/var/lib/web-cluster-agent"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

command -v python3 >/dev/null 2>&1 || { echo "Python 3 is required" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || echo "Warning: Docker CLI not found; host monitoring will work without container management"
test -f "$CA_CERT" || { echo "CA certificate not found: $CA_CERT" >&2; exit 1; }

sudo mkdir -p "$INSTALL_DIR" "$DATA_DIR"
if ! id wcm-agent >/dev/null 2>&1; then sudo useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin wcm-agent; fi
if getent group docker >/dev/null 2>&1; then sudo usermod -aG docker wcm-agent; fi
sudo cp "$SCRIPT_DIR"/*.py "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
sudo cp "$CA_CERT" "$INSTALL_DIR/ca.crt"
sudo chown -R wcm-agent:wcm-agent "$DATA_DIR"
sudo python3 -m pip install -r "$INSTALL_DIR/requirements.txt" --quiet

# Perform single-use enrollment before creating the service. The raw enrollment
# token isn't written to the unit file or command line.
sudo env WCM_ENROLLMENT_TOKEN="$WCM_ENROLLMENT_TOKEN" python3 "$INSTALL_DIR/main.py" \
  --enroll-only --server "$SERVER_URL" --api "$API_URL" \
  --ca-cert "$INSTALL_DIR/ca.crt" --data-dir "$DATA_DIR"
sudo chown -R wcm-agent:wcm-agent "$DATA_DIR"

sudo tee /etc/systemd/system/web-cluster-agent.service >/dev/null <<EOF
[Unit]
Description=Web Cluster Manager Agent
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=wcm-agent
Group=wcm-agent
ExecStart=/usr/bin/python3 $INSTALL_DIR/main.py --server $SERVER_URL --api $API_URL --ca-cert $INSTALL_DIR/ca.crt --data-dir $DATA_DIR
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

sudo systemctl daemon-reload
sudo systemctl enable --now web-cluster-agent
echo "Agent securely enrolled and started: $SERVER_URL"
