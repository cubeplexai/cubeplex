#!/bin/sh
# Chromium launcher run by supervisord (the [program:chromium] command). Runs on
# every (re)start, so all the profile cleanup below must be idempotent.
set -u

PROFILE=/workspace/.cubebox-browser-profile
PREFS="$PROFILE/Default/Preferences"

# The profile lives on the PVC and is reused across sandboxes for the same user.
# A previous instance killed non-gracefully (sandbox stop, OOM, SIGKILL) leaves:
#  - a stale SingletonLock → new Chromium refuses to start ("profile in use");
#  - "exit_type":"Crashed" in Preferences → Chromium shows a "Restore pages?"
#    bubble and a "Something went wrong opening your profile" dialog on start.
# Clear the lock and rewrite the last-exit state to clean so startup is silent.
rm -f "$PROFILE"/Singleton* 2>/dev/null || true
if [ -f "$PREFS" ]; then
    sed -i \
        's/"exit_type":"[^"]*"/"exit_type":"Normal"/;s/"exited_cleanly":false/"exited_cleanly":true/' \
        "$PREFS" 2>/dev/null || true
fi

# Install the egress MITM CA into Chromium's NSS store so HTTPS interception
# doesn't show "Not Secure". Chromium on Linux ignores /etc/ssl/certs; it reads
# from $HOME/.pki/nssdb. The cert is placed by the egress-ca-trust init container.
MITM_CA=/etc/ssl/certs/cubebox-egress.pem
if [ -f "$MITM_CA" ]; then
    NSS_DB="$HOME/.pki/nssdb"
    mkdir -p "$NSS_DB"
    certutil -N -d "sql:$NSS_DB" --empty-password 2>/dev/null || true
    certutil -A -d "sql:$NSS_DB" -n "cubebox-egress-ca" -t "CT,," -i "$MITM_CA" 2>/dev/null || true
fi

exec /ms-playwright/chrome \
    --no-sandbox \
    --test-type \
    --window-position=0,0 \
    --display="${DISPLAY:-:99.0}" \
    --user-data-dir="$PROFILE" \
    --no-first-run \
    --no-default-browser-check \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --hide-crash-restore-bubble \
    --start-maximized \
    --force-dark-mode \
    --disable-gpu \
    --disable-software-rasterizer \
    --disable-dev-shm-usage \
    --remote-debugging-port=9222 \
    --remote-debugging-address=127.0.0.1 \
    --remote-allow-origins=* \
    about:blank
