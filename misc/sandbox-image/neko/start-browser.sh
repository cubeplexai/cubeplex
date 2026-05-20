#!/bin/sh
# Start the Neko browser stack on demand (idempotent).
#
# opensandbox owns the container's main process (bootstrap.sh -> tail -f
# /dev/null) and runs agent commands through execd, so Neko cannot be the
# container CMD. The backend invokes this script via sandbox.execute the first
# time a live view is requested; repeat calls are a no-op.
set -eu

PIDFILE=/var/run/neko-supervisord.pid

if [ -S /var/run/supervisor.sock ] && supervisorctl status >/dev/null 2>&1; then
    echo "neko stack already running"
    exit 0
fi

mkdir -p /var/log/neko /tmp/runtime-neko
chown neko:neko /var/log/neko /tmp/runtime-neko 2>/dev/null || true

# The Chromium profile lives on the PVC (/workspace is a runtime mount owned by
# root), but Chromium runs as the neko user — create + own the dir at runtime so
# it can write its profile. This is what persists auth state across the session.
mkdir -p /workspace/.cubebox-browser-profile
chown -R neko:neko /workspace/.cubebox-browser-profile 2>/dev/null || true

# Daemonize supervisord; it brings up Xorg, openbox, pulseaudio, neko, chromium.
nohup /usr/bin/supervisord -c /etc/neko/supervisord.conf >/var/log/neko/supervisord.boot.log 2>&1 &
echo $! > "$PIDFILE"

# Wait for the Neko web server to answer.
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "http://127.0.0.1:8080/"; then
        echo "neko stack up"
        exit 0
    fi
    sleep 1
done

echo "neko stack failed to come up; see /var/log/neko/*.log" >&2
exit 1
