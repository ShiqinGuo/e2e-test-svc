#!/bin/sh
set -eu
mkdir -p /tmp/home /tmp/xdg
chmod 700 /tmp/xdg
export XDG_RUNTIME_DIR=/tmp/xdg
Xvfb :99 -screen 0 1600x1000x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
for n in $(seq 1 50); do [ -S /tmp/.X11-unix/X99 ] && break; sleep 0.1; done
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display :99 -localhost -rfbport 5900 -nopw -forever -shared -noxdamage >/tmp/x11vnc.log 2>&1 &
exec node /opt/recorder/server.mjs
