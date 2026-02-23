#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="pandora-sandbox"
RUN_USER="bkbest21"
PORT="8000"

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/.venv"

if [[ "$(id -un)" != "$RUN_USER" ]]; then
  echo "Please run this script as user '$RUN_USER' (current: $(id -un))."
  exit 1
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

if ! groups "$RUN_USER" | grep -q "\bdocker\b"; then
  echo "User '$RUN_USER' is not in the 'docker' group. Adding it now (requires sudo)."
  sudo usermod -aG docker "$RUN_USER"
  echo "Re-login may be required for docker group membership to take effect."
fi

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=Pandora Code Sandbox Service
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
Environment=SANDBOX_ROOT=${APP_DIR}/sandboxes
ExecStart=${VENV_DIR}/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}.service"

echo "Deployed. Check status with: sudo systemctl status ${SERVICE_NAME}.service"
echo "Logs: sudo journalctl -u ${SERVICE_NAME}.service -f"
