#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root." >&2
  exit 1
fi

: "${WCM_BROKER_NODE_ID:?Set WCM_BROKER_NODE_ID}"
: "${WCM_BROKER_TOKEN:?Set the one-time displayed Broker credential}"
: "${WCM_BROKER_SERVER_URL:?Set WCM_BROKER_SERVER_URL to wss://.../ws/broker}"

python3 - "$WCM_BROKER_NODE_ID" "$WCM_BROKER_SERVER_URL" <<'PY'
import re
import sys
import uuid

node_id, server_url = sys.argv[1:]
try:
    uuid.UUID(node_id)
except ValueError as exc:
    raise SystemExit("WCM_BROKER_NODE_ID must be a UUID") from exc
if not re.fullmatch(r"wss://[A-Za-z0-9._~:/\[\]-]+/ws/broker/?", server_url):
    raise SystemExit("WCM_BROKER_SERVER_URL must be a plain WSS URL ending in /ws/broker")
PY

install -d -m 700 /etc/web-cluster-manager
install -d -o root -g root -m 700 \
  /opt/web-cluster-manager-broker/broker \
  /opt/web-cluster-manager-broker/agent
install -o root -g root -m 700 \
  "$(dirname "$0")/main.py" \
  /opt/web-cluster-manager-broker/broker/main.py
install -o root -g root -m 600 \
  "$(dirname "$0")/__init__.py" \
  /opt/web-cluster-manager-broker/broker/__init__.py
install -o root -g root -m 600 \
  "$(dirname "$0")/../agent/terminal_crypto.py" \
  /opt/web-cluster-manager-broker/agent/terminal_crypto.py
: > /opt/web-cluster-manager-broker/agent/__init__.py
chmod 600 /opt/web-cluster-manager-broker/agent/__init__.py
umask 077
printf '%s\n' "$WCM_BROKER_TOKEN" > /etc/web-cluster-manager/broker.token
cat > /etc/web-cluster-manager/broker.env <<EOF
WCM_BROKER_NODE_ID=$WCM_BROKER_NODE_ID
WCM_BROKER_TOKEN_FILE=/etc/web-cluster-manager/broker.token
WCM_BROKER_SERVER_URL=$WCM_BROKER_SERVER_URL
EOF
chmod 600 /etc/web-cluster-manager/broker.env

cat > /etc/systemd/system/wcm-broker.service <<EOF
[Unit]
Description=Web Cluster Manager privileged terminal Broker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
EnvironmentFile=/etc/web-cluster-manager/broker.env
WorkingDirectory=/opt/web-cluster-manager-broker
ExecStart=$(command -v python3) /opt/web-cluster-manager-broker/broker/main.py
Restart=on-failure
NoNewPrivileges=false

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now wcm-broker.service
unset WCM_BROKER_TOKEN
