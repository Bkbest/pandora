#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="pandora-sandbox"
MCP_SERVICE_NAME="pandora-sandbox-mcp"
RUN_USER="bkbest21"
PORT="8000"
MCP_PORT="3000"

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/.venv"

if [[ "$(id -un)" != "$RUN_USER" ]]; then
  echo "Please run this script as user '$RUN_USER' (current: $(id -un))."
  exit 1
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
"$VENV_DIR/bin/pip" install -e "$APP_DIR/../../"

if ! groups "$RUN_USER" | grep -q "\bdocker\b"; then
  echo "User '$RUN_USER' is not in the 'docker' group. Adding it now (requires sudo)."
  sudo usermod -aG docker "$RUN_USER"
  echo "Re-login may be required for docker group membership to take effect."
fi

# Check and build Docker image if needed
DOCKER_IMAGE="${SANDBOX_PYTHON_IMAGE:-sandbox-python}"
if ! docker image inspect "$DOCKER_IMAGE" >/dev/null 2>&1; then
    echo "Docker image '$DOCKER_IMAGE' not found. Building..."
    if [[ -f "$APP_DIR/Dockerfile.alpine" ]]; then
        docker build -t "$DOCKER_IMAGE" -f "$APP_DIR/Dockerfile.alpine" "$APP_DIR"
    else
        echo "Error: Dockerfile.alpine not found in $APP_DIR"
        exit 1
    fi
fi

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
MCP_UNIT_PATH="/etc/systemd/system/${MCP_SERVICE_NAME}.service"

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

sudo tee "$MCP_UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=Pandora Code Sandbox MCP Server (Streamable HTTP)
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
Environment=SANDBOX_ROOT=${APP_DIR}/sandboxes
Environment=SANDBOX_PYTHON_IMAGE=${DOCKER_IMAGE}
Environment=MCP_HOST=0.0.0.0
Environment=MCP_PORT=${MCP_PORT}
ExecStart=${VENV_DIR}/bin/python -m app.mcp_server
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

# Keep the FastAPI service installed, but disabled
sudo systemctl disable --now "${SERVICE_NAME}.service" 2>/dev/null || true

# Enable the MCP service
sudo systemctl enable --now "${MCP_SERVICE_NAME}.service"

echo "Deployed. FastAPI service is installed but disabled: ${SERVICE_NAME}.service"
echo "MCP service is enabled: ${MCP_SERVICE_NAME}.service"
echo "Check status with: sudo systemctl status ${MCP_SERVICE_NAME}.service"
echo "Logs: sudo journalctl -u ${MCP_SERVICE_NAME}.service -f"
