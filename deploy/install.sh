#!/usr/bin/env bash
# 85pi で 1 回だけ実行する（更新は git pull のあと systemctl --user restart imx519-edge）。
# venv は apt の picamera2 / libcamera / numpy / simplejpeg / smbus2 を使うため --system-site-packages で作る。
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d .venv ] || python3 -m venv --system-site-packages .venv
.venv/bin/pip install -q -e .

mkdir -p ~/.config/imx519_edge ~/.config/systemd/user ~/captures
[ -f ~/.config/imx519_edge/config.toml ] || cp deploy/config.example.toml ~/.config/imx519_edge/config.toml
cp deploy/imx519-edge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now imx519-edge

# tailnet だけに HTTPS で出す（85pi のほかのサービスと同じやり方）
tailscale serve --bg --https=12443 http://127.0.0.1:8519
echo "https://$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'):12443"
