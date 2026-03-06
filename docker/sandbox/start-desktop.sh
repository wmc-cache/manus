#!/bin/bash
export DISPLAY=:99
Xvfb :99 -screen 0 1280x800x24 -ac +extension GLX +render -noreset &
sleep 1
openbox &
sleep 0.5
x11vnc -display :99 -forever -nopw -shared -rfbport 5900 -xkb &
sleep 0.5
if [ "${START_CHROME:-true}" = "true" ]; then
  chromium-browser --no-sandbox --disable-gpu --disable-dev-shm-usage \
    --window-size=1280,800 --start-maximized \
    "${CHROME_START_URL:-about:blank}" &
fi
exec sleep infinity
