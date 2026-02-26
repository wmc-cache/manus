#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/manus-mvp-backend/backend"
FRONTEND_DIR="$ROOT_DIR/manus-frontend/client"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

RUN_DIR="$ROOT_DIR/.run"
LOG_DIR="$RUN_DIR/logs"
BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"

BACKEND_PORT=8000
FRONTEND_PORT=3000

mkdir -p "$LOG_DIR"

is_pid_running() {
  local pid="$1"
  kill -0 "$pid" >/dev/null 2>&1
}

ensure_port_free() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1 && lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Stop the existing process first."
    exit 1
  fi
}

start_backend() {
  if [[ -f "$BACKEND_PID_FILE" ]]; then
    local pid
    pid="$(cat "$BACKEND_PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && is_pid_running "$pid"; then
      echo "Backend already running (PID $pid)."
      return
    fi
    rm -f "$BACKEND_PID_FILE"
  fi

  if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Missing virtualenv python: $VENV_PYTHON"
    exit 1
  fi
  if [[ ! -f "$BACKEND_DIR/main.py" ]]; then
    echo "Backend entry not found: $BACKEND_DIR/main.py"
    exit 1
  fi

  ensure_port_free "$BACKEND_PORT"

  (
    cd "$BACKEND_DIR"
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.venv/bin/activate"
    export PYTHONPATH="$BACKEND_DIR"
    nohup uvicorn main:app --host 0.0.0.0 --port "$BACKEND_PORT" >"$LOG_DIR/backend.log" 2>&1 &
    echo "$!" >"$BACKEND_PID_FILE"
  )

  echo "Backend started on :$BACKEND_PORT (PID $(cat "$BACKEND_PID_FILE"))."
}

start_frontend() {
  if [[ -f "$FRONTEND_PID_FILE" ]]; then
    local pid
    pid="$(cat "$FRONTEND_PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && is_pid_running "$pid"; then
      echo "Frontend already running (PID $pid)."
      return
    fi
    rm -f "$FRONTEND_PID_FILE"
  fi

  if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
    echo "Frontend package.json not found: $FRONTEND_DIR/package.json"
    exit 1
  fi
  if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    echo "Missing frontend dependencies. Run: cd $FRONTEND_DIR && npm install"
    exit 1
  fi

  ensure_port_free "$FRONTEND_PORT"

  (
    cd "$FRONTEND_DIR"
    nohup npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" >"$LOG_DIR/frontend.log" 2>&1 &
    echo "$!" >"$FRONTEND_PID_FILE"
  )

  echo "Frontend started on :$FRONTEND_PORT (PID $(cat "$FRONTEND_PID_FILE"))."
}

start_backend
start_frontend

echo
echo "Services are up:"
echo "  Frontend: http://localhost:$FRONTEND_PORT"
echo "  Backend:  http://localhost:$BACKEND_PORT"
echo
echo "Logs:"
echo "  $LOG_DIR/backend.log"
echo "  $LOG_DIR/frontend.log"
echo
echo "Stop command:"
echo "  kill $(cat "$BACKEND_PID_FILE") $(cat "$FRONTEND_PID_FILE")"
