#!/bin/bash
# Ad-hoc background start; run with sudo, GPIO access needs root. For anything
# long-lived prefer the systemd unit (see README.md) so the exporter restarts on
# failure and survives reboots.
set -euo pipefail

cd "$(dirname "$0")"

echo "starting home-watch"
nohup uv run --frozen home-watch.py >> home-watch.log 2>&1 &
echo "home-watch started with PID $! (logging to home-watch.log)"
