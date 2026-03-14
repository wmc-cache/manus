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

DISPLAY_NUM=":1"
VNC_PORT=5900
CDP_PORT=9222
BROWSER_PROFILE="/tmp/manus-browser-profile"

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
      sleep 2
      kill -9 $pids 2>/dev/null || true
    fi
  fi
}

install_desktop_deps() {
  local need_install=0

  if ! command -v Xvfb >/dev/null 2>&1; then
    need_install=1
  fi
  if ! command -v x11vnc >/dev/null 2>&1; then
    need_install=1
  fi

  if [[ $need_install -eq 1 ]]; then
    echo "Installing desktop dependencies (Xvfb, x11vnc, openbox)..."
    yum install -y xorg-x11-server-Xvfb x11vnc openbox xdpyinfo 2>/dev/null || true
  fi
}

start_desktop() {
  if ! command -v Xvfb >/dev/null 2>&1; then
    echo "Warning: Xvfb not available, skipping desktop setup (browser will run headlessly)"
    return
  fi

  echo "Setting up virtual desktop on display $DISPLAY_NUM..."

  # Stop existing Xvfb on this display
  pkill -f "Xvfb $DISPLAY_NUM" >/dev/null 2>&1 || true
  sleep 0.5
  local lock_num="${DISPLAY_NUM#:}"
  rm -f "/tmp/.X${lock_num}-lock" "/tmp/.X11-unix/X${lock_num}" 2>/dev/null || true

  # Start Xvfb
  nohup Xvfb "$DISPLAY_NUM" -screen 0 1280x800x24 -ac +extension GLX +render -noreset \
    >"$LOG_DIR/xvfb.log" 2>&1 </dev/null &
  echo "Xvfb started on display $DISPLAY_NUM"

  # Wait for display to be ready
  local deadline=$(($(date +%s) + 10))
  while [[ $(date +%s) -lt $deadline ]]; do
    if DISPLAY="$DISPLAY_NUM" xdpyinfo >/dev/null 2>&1; then
      echo "Display $DISPLAY_NUM ready"
      break
    fi
    sleep 0.5
  done

  # Start openbox window manager
  if command -v openbox >/dev/null 2>&1; then
    pkill -x openbox >/dev/null 2>&1 || true
    sleep 0.2
    DISPLAY="$DISPLAY_NUM" nohup openbox >"$LOG_DIR/openbox.log" 2>&1 </dev/null &
    echo "Openbox started"
  fi

  # Start x11vnc
  if command -v x11vnc >/dev/null 2>&1; then
    pkill -x x11vnc >/dev/null 2>&1 || true
    sleep 0.3
    nohup x11vnc -display "$DISPLAY_NUM" -forever -nopw -shared \
      -rfbport "$VNC_PORT" -xkb \
      >"$LOG_DIR/x11vnc.log" 2>&1 </dev/null &
    echo "x11vnc started on port $VNC_PORT"

    # Wait for VNC port
    local vnc_deadline=$(($(date +%s) + 10))
    while [[ $(date +%s) -lt $vnc_deadline ]]; do
      if python3 -c "import socket; s=socket.create_connection(('127.0.0.1',$VNC_PORT),1); s.close()" 2>/dev/null; then
        echo "VNC port $VNC_PORT ready"
        break
      fi
      sleep 0.5
    done
  fi
}

start_chrome() {
  # Check if CDP is already running
  if python3 -c "import socket; s=socket.create_connection(('127.0.0.1',$CDP_PORT),1); s.close()" 2>/dev/null; then
    echo "Chrome CDP already available on port $CDP_PORT"
    return
  fi

  # Find Chrome binary
  local browser_bin=""
  for bin in google-chrome google-chrome-stable chromium-browser chromium; do
    if command -v "$bin" >/dev/null 2>&1; then
      browser_bin="$bin"
      break
    fi
  done

  if [[ -z "$browser_bin" ]]; then
    echo "Warning: No Chrome/Chromium found, browser tool will not work"
    return
  fi

  echo "Starting $browser_bin with CDP on port $CDP_PORT..."
  mkdir -p "$BROWSER_PROFILE"

  # Kill any existing instance bound to our profile
  pkill -f "remote-debugging-port=${CDP_PORT}" >/dev/null 2>&1 || true
  sleep 0.5

  local extra_args=""
  # Use the virtual display if available, otherwise headless
  if command -v Xvfb >/dev/null 2>&1 && DISPLAY="$DISPLAY_NUM" xdpyinfo >/dev/null 2>&1; then
    local display_arg="DISPLAY=$DISPLAY_NUM"
  else
    echo "No virtual display available, starting Chrome in headless mode"
    extra_args="--headless=new"
    local display_arg=""
  fi

  eval "DISPLAY=\"$DISPLAY_NUM\" nohup \"$browser_bin\" \
    --no-sandbox \
    --disable-gpu \
    --disable-dev-shm-usage \
    --no-first-run \
    --no-default-browser-check \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port=$CDP_PORT \
    --user-data-dir=$BROWSER_PROFILE \
    --window-size=1280,800 \
    $extra_args \
    about:blank \
    >\"$LOG_DIR/chromium.log\" 2>&1 </dev/null &"

  # Wait for CDP port
  local cdp_deadline=$(($(date +%s) + 15))
  while [[ $(date +%s) -lt $cdp_deadline ]]; do
    if python3 -c "import socket; s=socket.create_connection(('127.0.0.1',$CDP_PORT),1); s.close()" 2>/dev/null; then
      echo "Chrome CDP ready on port $CDP_PORT"
      return
    fi
    sleep 0.5
  done
  echo "Warning: Chrome CDP port $CDP_PORT not ready after 15s, check $LOG_DIR/chromium.log"
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
    MANUS_USE_MCP=true \
    MANUS_USE_DOCKER=false \
    DISPLAY="$DISPLAY_NUM" \
    VNC_HOST=localhost \
    VNC_PORT="$VNC_PORT" \
    MCP_FILESYSTEM_URL=http://localhost:8101 \
    MCP_EXECUTION_URL=http://localhost:8102 \
    MCP_RESEARCH_URL=http://localhost:8104 \
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

install_desktop_deps
start_desktop
start_chrome
start_backend
start_frontend

echo
echo "Services are up:"
echo "  Frontend: http://localhost:$FRONTEND_PORT"
echo "  Backend:  http://localhost:$BACKEND_PORT"
echo "  VNC:      localhost:$VNC_PORT"
echo "  Chrome CDP: http://127.0.0.1:$CDP_PORT"
echo
echo "Logs:"
echo "  $LOG_DIR/backend.log"
echo "  $FRONTEND_BUILD_LOG_FILE"
echo "  $LOG_DIR/frontend.log"
echo "  $LOG_DIR/xvfb.log"
echo "  $LOG_DIR/x11vnc.log"
echo "  $LOG_DIR/chromium.log"
echo
echo "Stop command:"
echo "  kill $(cat "$BACKEND_PID_FILE") $(cat "$FRONTEND_PID_FILE")"
