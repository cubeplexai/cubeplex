#!/bin/sh
# Start the Neko browser stack on demand (idempotent).
#
# opensandbox owns the container's main process (bootstrap.sh -> tail -f
# /dev/null) and runs agent commands through execd, so Neko cannot be the
# container CMD. The backend invokes this script via sandbox.execute the first
# time a live view is requested; the browser skill also calls it before
# agent-browser connect. Repeat calls must leave Chromium reachable on CDP
# 9222 — not just supervisord alive (closing the last tab can kill Chromium
# while Neko/Xorg keep running, leaving an empty desktop + connection refused).
set -eu

# Everything below needs root: the lock and pidfile live in /var/run, the
# runtime dirs get chowned, and supervisord drops privileges itself. The
# backend calls this with as_root=True, but agent commands run as
# sandbox.run_uid (1000) — so re-exec through sudo instead of dying on the
# first write, which also broke the "already running" fast path below.
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"

# sudo resets USER to root, but neko/chromium.conf expands %(ENV_USER)s into its
# `user=` and HOME directives — losing it runs Chromium and openbox as root and
# leaves a root-owned profile on the PVC that CubePlex can never write again.
export USER="${SUDO_USER:-${USER:-cubeplex}}"

PIDFILE=/var/run/neko-supervisord.pid
SUPERVISORD_CONF=/etc/neko/supervisord.conf
CDP_URL="http://127.0.0.1:9222/json/version"
NEKO_URL="http://127.0.0.1:8080/"

# Serialize concurrent invocations: without a lock, two pings can both pass the
# pre-check before the supervisor socket exists and both launch supervisord,
# racing on the socket/ports. flock makes check+start atomic; the lock releases
# when the script (fd 9) exits.
exec 9>/var/run/neko-start.lock
flock 9

cdp_ready() {
    curl -fsS -o /dev/null --max-time 2 "$CDP_URL" 2>/dev/null
}

neko_ready() {
    curl -fsS -o /dev/null --max-time 2 "$NEKO_URL" 2>/dev/null
}

# Profile must exist and be owned by the sandbox user before every Chromium
# (re)start — same reason as cold boot.
ensure_profile() {
    mkdir -p /workspace/.cubeplex-browser-profile
    chown -R cubeplex:cubeplex /workspace/.cubeplex-browser-profile 2>/dev/null || true
}

# Chromium status line is e.g. "chromium   RUNNING   pid 123, uptime ..."
chromium_running() {
    supervisorctl -c "$SUPERVISORD_CONF" status chromium 2>/dev/null | grep -q RUNNING
}

# Wait until CDP answers (agent-browser connect) and optionally Neko (live view).
wait_for_ready() {
    need_neko="${1:-0}"
    i=0
    while [ "$i" -lt 30 ]; do
        if cdp_ready; then
            if [ "$need_neko" -eq 0 ] || neko_ready; then
                return 0
            fi
        fi
        i=$((i + 1))
        sleep 1
    done
    return 1
}

# Supervisord is up, but Chromium may be STOPPED/FATAL/EXITED (user closed the
# last tab/window) or RUNNING with a dead CDP port (stuck process). Heal it.
ensure_chromium() {
    ensure_profile

    if chromium_running && cdp_ready; then
        echo "chromium already ready (CDP 9222)"
        return 0
    fi

    if chromium_running; then
        echo "chromium RUNNING but CDP down; restarting"
        supervisorctl -c "$SUPERVISORD_CONF" restart chromium >/dev/null
    else
        # FATAL/STOPPED/EXITED/BACKOFF — start clears FATAL; restart is a
        # fallback if the process is in a weird transitional state.
        echo "chromium not running; starting"
        if ! supervisorctl -c "$SUPERVISORD_CONF" start chromium >/dev/null 2>&1; then
            supervisorctl -c "$SUPERVISORD_CONF" restart chromium >/dev/null
        fi
    fi

    if wait_for_ready 0; then
        echo "chromium ready (CDP 9222)"
        return 0
    fi

    echo "chromium failed to become ready on CDP 9222; see /var/log/neko/chromium.log" >&2
    supervisorctl -c "$SUPERVISORD_CONF" status chromium >&2 || true
    return 1
}

# Idempotency check must target the same supervisord (config/socket) we start
# below. Use a daemon-level `pid` check, not bare `status`: `supervisorctl
# status` returns non-zero if any managed child is down, which would falsely
# fall through and launch a second supervisord against the running one.
if [ -S /var/run/supervisor.sock ] && supervisorctl -c "$SUPERVISORD_CONF" pid >/dev/null 2>&1; then
    echo "neko stack already running"
    ensure_chromium
    exit $?
fi

mkdir -p /var/log/neko /tmp/runtime-neko
chown cubeplex:cubeplex /var/log/neko /tmp/runtime-neko 2>/dev/null || true
ensure_profile

# Daemonize supervisord; it brings up Xorg, openbox, pulseaudio, neko, chromium.
# Close the lock fd (9) in the child: otherwise supervisord (and its children)
# inherit it and hold the flock for their whole lifetime, so every later
# start-browser.sh blocks forever on flock and the live-view request hangs (500).
nohup /usr/bin/supervisord -c /etc/neko/supervisord.conf >/var/log/neko/supervisord.boot.log 2>&1 9>&- &
echo $! > "$PIDFILE"

# Wait for Neko (live view) and Chromium CDP (agent-browser) together.
if wait_for_ready 1; then
    echo "neko stack up"
    exit 0
fi

echo "neko stack failed to come up; see /var/log/neko/*.log" >&2
supervisorctl -c "$SUPERVISORD_CONF" status >&2 || true
exit 1
