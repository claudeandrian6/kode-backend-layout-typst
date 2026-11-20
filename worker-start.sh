#!/bin/sh
# worker-start.sh — pastikan worker.py Layout Editor hidup.
# Dijalankan manual lewat SSH, atau otomatis lewat cron Hostinger (hPanel):
#   * * * * * /home/u587972949/typst_editor/worker-start.sh >/dev/null 2>&1
#   @reboot   /home/u587972949/typst_editor/worker-start.sh >/dev/null 2>&1
BASE=/home/u587972949/typst_editor
cd "$BASE" || exit 1
if [ -f worker.pid ] && kill -0 "$(cat worker.pid)" 2>/dev/null; then
  exit 0
fi
if command -v pgrep >/dev/null 2>&1; then
  p=$(pgrep -u "$(id -u)" -f "$BASE/worker.py" | head -1)
  if [ -n "$p" ]; then
    echo "$p" > "$BASE/worker.pid"
    exit 0
  fi
fi
nohup /opt/alt/python311/bin/python3 -u "$BASE/worker.py" >> "$BASE/worker.log" 2>&1 &
pid=$!
sleep 1
if kill -0 "$pid" 2>/dev/null; then
  echo "$pid" > "$BASE/worker.pid"
fi
