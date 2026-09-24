#!/usr/bin/env bash
# Idempotent: ensure the sync daemon is running. Safe to call from a watchdog.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$ROOT/.run/daemon.pid"
LOG="$ROOT/logs/daemon.log"
mkdir -p "$ROOT/.run" "$ROOT/logs"

alive=0
if [[ -f "$PIDFILE" ]]; then
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
    # Confirm it's our loop (daemon_loop.sh or sleep child of it)
    cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    if [[ "$cmd" == *daemon_loop.sh* ]] || [[ "$cmd" == *scripts/daemon_loop* ]]; then
      alive=1
    fi
  fi
fi

if [[ "$alive" -eq 1 ]]; then
  echo "ok pid=$(cat "$PIDFILE")"
  exit 0
fi

# Start detached
chmod +x "$ROOT/scripts/daemon_loop.sh" "$ROOT/scripts/ensure_runner.sh"
nohup "$ROOT/scripts/daemon_loop.sh" >>"$LOG" 2>&1 &
disown || true
sleep 0.5
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "started pid=$(cat "$PIDFILE")"
  exit 0
fi
echo "failed to start" >&2
exit 1
