#!/usr/bin/env bash
# Background collector/sync loop (every ~4 minutes). Survives parent shell exit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/logs/daemon.log"
PIDFILE="$ROOT/.run/daemon.pid"
mkdir -p "$ROOT/logs" "$ROOT/.run"

# Rotate daemon log if large
if [[ -f "$LOG" ]] && [[ "$(wc -c < "$LOG")" -gt 1500000 ]]; then
  mv -f "$LOG" "$LOG.1"
fi

echo $$ > "$PIDFILE"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

# Ensure gh git credentials without printing tokens
gh auth setup-git >/dev/null 2>&1 || true

log() {
  echo "$(date '+%Y-%m-%dT%H:%M:%S%z') $*" | tee -a "$LOG"
}

log "daemon start pid=$$"
while true; do
  # Load CF token only if needed elsewhere; sync does not need it
  if /usr/bin/python3 "$ROOT/scripts/sync_once.py" >>"$LOG" 2>&1; then
    :
  else
    log "sync_once exited $?"
  fi
  # 180–300s jitter-ish: fixed 240s (~4 min)
  sleep 240
done
