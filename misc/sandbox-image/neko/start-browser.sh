#!/bin/sh
# Start the Neko browser stack on demand (idempotent).
#
# opensandbox owns the container's main process (bootstrap.sh -> tail -f
# /dev/null) and runs agent commands through execd, so Neko cannot be the
# container CMD. The backend invokes this script via sandbox.execute the first
# time a live view is requested; repeat calls are a no-op.
set -eu

PIDFILE=/var/run/neko-supervisord.pid
SUPERVISORD_CONF=/etc/neko/supervisord.conf

# Serialize concurrent invocations: without a lock, two pings can both pass the
# pre-check before the supervisor socket exists and both launch supervisord,
# racing on the socket/ports. flock makes check+start atomic; the lock releases
# when the script (fd 9) exits.
exec 9>/var/run/neko-start.lock
flock 9

# Idempotency check must target the same supervisord (config/socket) we start
# below. Use a daemon-level `pid` check, not `status`: `supervisorctl status`
# returns non-zero if any managed child is down, which would falsely fall
# through and launch a second supervisord against the running one.
if [ -S /var/run/supervisor.sock ] && supervisorctl -c "$SUPERVISORD_CONF" pid >/dev/null 2>&1; then
    echo "neko stack already running"
    exit 0
fi

mkdir -p /var/log/neko /tmp/runtime-neko
chown neko:neko /var/log/neko /tmp/runtime-neko 2>/dev/null || true

# The Chromium profile lives on the PVC (/workspace is a runtime mount owned by
# root), but Chromium runs as the neko user — create + own the dir at runtime so
# it can write its profile. This is what persists auth state across the session.
mkdir -p /workspace/.cubeplex-browser-profile
chown -R neko:neko /workspace/.cubeplex-browser-profile 2>/dev/null || true

# Daemonize supervisord; it brings up Xorg, openbox, pulseaudio, neko, chromium.
# Close the lock fd (9) in the child: otherwise supervisord (and its children)
# inherit it and hold the flock for their whole lifetime, so every later
# start-browser.sh blocks forever on flock and the live-view request hangs (500).
nohup /usr/bin/supervisord -c /etc/neko/supervisord.conf >/var/log/neko/supervisord.boot.log 2>&1 9>&- &
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
