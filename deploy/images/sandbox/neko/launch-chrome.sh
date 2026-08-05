#!/bin/sh
# Chromium launcher run by supervisord (the [program:chromium] command). Runs on
# every (re)start, so all the profile cleanup below must be idempotent.
set -u

PROFILE=/workspace/.cubeplex-browser-profile
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
# doesn't show "Not Secure". The egress-ca-trust init container drops the cert
# here as a real file on the shared trust dir (see the webhook's patch.py).
# Don't be tempted by /etc/ssl/certs/cubeplex-egress.pem next to it: that one is
# a symlink into the init container's own filesystem layer, so it dangles here.
# Absent whenever egress interception is off, hence the -s guard.
#
# certutil -N on an *existing* NSS DB can spin forever (busy-wait on a lock /
# re-init path) and block this launcher so Chromium never starts — supervisord
# still shows chromium RUNNING (the shell is alive) but CDP :9222 is dead.
# Only create the DB when missing, skip re-import when present, and bound every
# certutil with timeout so a bad DB never wedges the browser stack.
MITM_CA=/etc/ssl/certs/cubeplex-egress-ca.pem

import_mitm_ca() {
    NSS_DB="$1"
    mkdir -p "$NSS_DB"
    if [ ! -f "$NSS_DB/cert9.db" ]; then
        timeout 5 certutil -N -d "sql:$NSS_DB" --empty-password 2>/dev/null || true
    fi
    # Already imported → skip (certutil -A on a duplicate can also hang/prompt).
    if timeout 5 certutil -L -d "sql:$NSS_DB" 2>/dev/null | grep -q "cubeplex-egress-ca"; then
        return 0
    fi
    timeout 5 certutil -A -d "sql:$NSS_DB" -n "cubeplex-egress-ca" -t "CT,," -i "$MITM_CA" \
        2>/dev/null || true
}

if [ -s "$MITM_CA" ]; then
    # Chromium <91 read ~/.pki/nssdb; modern (XDG) Chromium reads
    # ~/.local/share/pki/nssdb. Import into both so a Chromium upgrade in
    # the image doesn't silently break trust on existing profiles.
    for NSS_DB in "${HOME:-/home/cubeplex}/.pki/nssdb" \
        "${HOME:-/home/cubeplex}/.local/share/pki/nssdb"; do
        import_mitm_ca "$NSS_DB"
    done
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
