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
# doesn't show "Not Secure".
#
# The obvious source — /etc/ssl/certs/cubeplex-egress.pem — is a dangling
# symlink in the main sandbox container: OpenSandbox's egress-ca-trust init
# container `cp`s the cert to /usr/local/share/ca-certificates/ in its OWN
# filesystem layer, then runs update-ca-certificates, which writes symlinks
# to the shared ca-trust emptyDir mounted on /etc/ssl/certs. The symlinks
# survive the init container's death; the target file does not. So
# /etc/ssl/certs/cubeplex-egress.pem -> /usr/local/share/ca-certificates/
# cubeplex-egress.crt points at nothing in the sandbox container.
#
# update-ca-certificates also concatenates every cert into
# /etc/ssl/certs/ca-certificates.crt, and THAT file IS on the shared volume.
# So we extract the cert from the bundle by subject CN.
BUNDLE=/etc/ssl/certs/ca-certificates.crt
MITM_CA=/tmp/cubeplex-egress-ca.pem
if [ -f "$BUNDLE" ]; then
    openssl crl2pkcs7 -nocrl -certfile "$BUNDLE" 2>/dev/null \
        | openssl pkcs7 -print_certs -outform PEM 2>/dev/null \
        | sed -n '/subject=CN = cubeplex-egress-mitm-ca/,/-----END CERTIFICATE-----/p' \
        | sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p' \
        > "$MITM_CA"
fi

if [ -s "$MITM_CA" ]; then
    # Chromium <91 read ~/.pki/nssdb; modern (XDG) Chromium reads
    # ~/.local/share/pki/nssdb. Import into both so a Chromium upgrade in
    # the image doesn't silently break trust on existing profiles.
    for NSS_DB in "$HOME/.pki/nssdb" "$HOME/.local/share/pki/nssdb"; do
        mkdir -p "$NSS_DB"
        certutil -N -d "sql:$NSS_DB" --empty-password 2>/dev/null || true
        certutil -A -d "sql:$NSS_DB" -n "cubeplex-egress-ca" -t "CT,," -i "$MITM_CA" 2>/dev/null || true
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
