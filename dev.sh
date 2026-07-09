#!/usr/bin/env bash
# Usage: ./dev.sh [start|stop|restart|status]
# Works from any shell (bash, fish, zsh) — shebang runs this under bash regardless.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$SCRIPT_DIR/.dev.pid"
LOGFILE="$SCRIPT_DIR/.dev.log"
UVICORN="$SCRIPT_DIR/.venv/bin/uvicorn"
PORT="${PORT:-8000}"

# Print the listening PID(s) on $PORT, one per line (empty if free).
port_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u
  fi
}

do_stop() {
  if [[ ! -f "$PIDFILE" ]]; then
    echo "Not running."
    return 0
  fi
  pid="$(cat "$PIDFILE")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "Stopped (pid $pid)."
  else
    echo "Process $pid not found — already stopped."
  fi
  rm -f "$PIDFILE"
}

do_start() {
  if [[ ! -x "$UVICORN" ]]; then
    echo "Error: $UVICORN not found. Run 'pip install -r requirements.txt' inside .venv first."
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
  pids="$(port_pids || true)"
  if [[ -n "$pids" ]]; then
    echo "Port $PORT is already in use by PID(s): $(echo $pids | tr '\n' ' ')"
    read -r -p "Kill? [Y/n] " ans || ans="n"
    case "${ans:-Y}" in
      [Nn]*)
        echo "Aborting — port $PORT still in use."
        exit 1
        ;;
      *)
        kill $pids 2>/dev/null || true
        # Wait up to ~3s for the port to be released, then escalate to SIGKILL.
        for _ in $(seq 1 10); do
          [[ -z "$(port_pids || true)" ]] && break
          sleep 0.3
        done
        if [[ -n "$(port_pids || true)" ]]; then
          echo "Still holding $PORT — sending SIGKILL."
          kill -9 $pids 2>/dev/null || true
          sleep 0.3
        fi
        ;;
    esac
  fi
  echo "Starting Faresight…"
  "$UVICORN" app.faresight:app --reload --port "$PORT" > "$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  echo "Started (pid $(cat "$PIDFILE")). Logs: $LOGFILE"
}

cmd="${1:-start}"

case "$cmd" in
  start)   do_start ;;
  stop)    do_stop ;;
  restart) do_stop; do_start ;;
  status)
    if [[ -f "$PIDFILE" ]]; then
      pid="$(cat "$PIDFILE")"
      if kill -0 "$pid" 2>/dev/null; then
        echo "Running (pid $pid)."
      else
        echo "Stale pidfile (pid $pid not found). Run './dev.sh stop' to clean up."
      fi
    else
      echo "Not running."
    fi
    ;;
  *)
    echo "Usage: $0 [start|stop|restart|status]"
    exit 1
    ;;
esac
