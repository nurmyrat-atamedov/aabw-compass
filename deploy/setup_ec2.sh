#!/usr/bin/env bash
# One-shot setup for an Amazon Linux 2023 EC2 box.
# Usage (from the repo on the box):  bash deploy/setup_ec2.sh
set -euo pipefail

APP_DIR="/home/ec2-user/aabw-compass"
cd "$APP_DIR"

# AL2023 default python3 is 3.9; install 3.11 (the app needs 3.10+ syntax).
sudo dnf install -y python3.11 python3.11-pip >/dev/null 2>&1 || true
PYBIN="$(command -v python3.11 || command -v python3)"
echo "Using interpreter: $PYBIN ($($PYBIN --version))"

"$PYBIN" -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

sudo cp deploy/compass.service /etc/systemd/system/compass.service
sudo systemctl daemon-reload
sudo systemctl enable --now compass

echo "Compass is up on http://0.0.0.0:8000"
echo "Check:  systemctl status compass   |   curl -s localhost:8000/api/meta | head -c 200"
