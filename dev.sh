#!/usr/bin/env bash
# Usage: ./dev.sh [start|stop|status]
# Works from any shell (bash, fish, zsh) — no venv activation needed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$SCRIPT_DIR/.dev.pid"
LOGFILE="$SCRIPT_DIR/.dev.log"
UVICORN="$SCRIPT_DIR/.venv/bin/uvicorn"

_detect_shell() {
  case "${SHELL:-}" in
    */fish) echo "fish" ;;
    */zsh)  echo "zsh"  ;;
    *)      echo "bash" ;;
  esac
}

_activate_hint() {
  case "$(_detect_shell)" in
    fish) echo "source .venv/bin/activate.fish" ;;
    *)    echo "source .venv/bin/activate" ;;
  esac
}

cmd="${1:-start}"

case "$cmd" in
  start)
    if [[ ! -x "$UVICORN" ]]; then
      echo "Error: $UVICORN not found. Install dependencies first:"
      echo "  $(_activate_hint) && pip install -r requirements.txt"
      exit 1
    fi
    if [[ -f "$PIDFILE" ]]; then
      pid="$(cat "$PIDFILE")"
      if kill -0 "$pid" 2>/dev/null; then
        echo "Already running (pid $pid). Run './dev.sh stop' first."
        exit 1
      fi
      rm -f "$PIDFILE"
    fi
    echo "Starting Faresight…"
    "$UVICORN" app.faresight:app --reload >> "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    echo "Started  → http://localhost:8000  (pid $(cat "$PIDFILE"))"
    echo "Logs     → $LOGFILE"
    ;;

  stop)
    if [[ ! -f "$PIDFILE" ]]; then
      echo "No pidfile found — is the app running?"
      exit 1
    fi
    pid="$(cat "$PIDFILE")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "Stopped (pid $pid)."
    else
      echo "Process $pid not found — already stopped."
    fi
    rm -f "$PIDFILE"
    ;;

  status)
    if [[ -f "$PIDFILE" ]]; then
      pid="$(cat "$PIDFILE")"
      if kill -0 "$pid" 2>/dev/null; then
        echo "Running (pid $pid) → http://localhost:8000"
      else
        echo "Stale pidfile (pid $pid not found). Run './dev.sh stop' to clean up."
      fi
    else
      echo "Not running."
    fi
    ;;

  *)
    echo "Usage: $0 [start|stop|status]"
    exit 1
    ;;
esac
