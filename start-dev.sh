#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/manus-mvp-backend/backend"
FRONTEND_DIR="$ROOT_DIR/manus-frontend/client"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
BACKEND_ENV_FILE="$BACKEND_DIR/.env"
FRONTEND_SERVER_SCRIPT="$FRONTEND_DIR/scripts/serve-dist.mjs"
FRONTEND_DIST_DIR="$FRONTEND_DIR/dist"

RUN_DIR="$ROOT_DIR/.run"
LOG_DIR="$RUN_DIR/logs"
BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
FRONTEND_BUILD_LOG_FILE="$LOG_DIR/frontend-build.log"

BACKEND_PORT=8000
FRONTEND_PORT=3000

mkdir -p "$LOG_DIR"

is_pid_running() {
  local pid="$1"
  kill -0 "$pid" >/dev/null 2>&1
}

load_env_file() {
  local env_file="$1"
  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
}

stop_port_service() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    local pids
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      echo "Stopping services on port $port (PIDs: $pids)"
      kill $pids 2>/dev/null || true
      # 等待进程完全终止
      sleep 2
      # 强制杀死仍在运行的进程
      kill -9 $pids 2>/dev/null || true
    fi
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

  stop_port_service "$BACKEND_PORT"

  (
    cd "$BACKEND_DIR"
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.venv/bin/activate"
    load_env_file "$BACKEND_ENV_FILE"
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
  if [[ ! -f "$FRONTEND_SERVER_SCRIPT" ]]; then
    echo "Frontend static server script not found: $FRONTEND_SERVER_SCRIPT"
    exit 1
  fi

  stop_port_service "$FRONTEND_PORT"

  cd "$FRONTEND_DIR"

  echo "Building frontend assets..."
  if ! npm run build >"$FRONTEND_BUILD_LOG_FILE" 2>&1; then
    echo "Frontend build failed. See: $FRONTEND_BUILD_LOG_FILE"
    exit 1
  fi
  if [[ ! -f "$FRONTEND_DIST_DIR/index.html" ]]; then
    echo "Frontend build output missing: $FRONTEND_DIST_DIR/index.html"
    exit 1
  fi

  nohup /usr/bin/node "$FRONTEND_SERVER_SCRIPT" "$FRONTEND_PORT" >"$LOG_DIR/frontend.log" 2>&1 &
  echo "$!" >"$FRONTEND_PID_FILE"

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
echo "  $FRONTEND_BUILD_LOG_FILE"
echo "  $LOG_DIR/frontend.log"
echo
echo "Stop command:"
echo "  kill $(cat "$BACKEND_PID_FILE") $(cat "$FRONTEND_PID_FILE")"
