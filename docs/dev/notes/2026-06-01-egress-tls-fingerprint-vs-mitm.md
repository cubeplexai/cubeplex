# Sandbox egress + Twitter (twitter-cli 401 / 502): GFW, not TLS fingerprint

- **Date:** 2026-06-01
- **Status:** Investigation + decision record. Root cause corrected after an
  isolated A/B/C experiment (see "Correction" below). No platform change made
  yet; current usage relies on a workaround whose cost is documented.
- **Area:** sandbox egress (`deploy/egress-bundle`), credential vault injection
  (`cubeplex/sandbox_env`), OpenSandbox egress sidecar (mitmproxy).

## Symptom

Running `twitter-cli` inside a sandbox returned `HTTP 401 — Cookie expired or
invalid`, even though the same cookies worked on the user's own machine. The
cookies were stored in the env vault as **secret** entries (so they reach the
sandbox as `cbxref_` placeholders) bound to hosts `*.x.com`.

## How the pieces fit

- Secret vault entries never put the real value in the sandbox; the sandbox env
  only holds a `cbxref_<32>` placeholder.
- The egress sidecar runs **mitmproxy in transparent mode**. An nft rule
  redirects the sandbox's outbound TCP 80/443 into mitmproxy:
  `tcp dport { 80, 443 } redirect to :18081`.
- mitmproxy MITM-terminates the TLS, `inject.py` swaps each `cbxref_` token in
  the request headers for the real secret (via the cubeplex exchange endpoint),
  then mitmproxy re-originates the request to the upstream.
- `twitter-cli` uses `curl_cffi` with `impersonate="chrome133a"` to forge
  Chrome's TLS fingerprint — its usual defense against Twitter bot detection.

## Two observed failure modes

| Config | cbxref_ substituted? | Result |
|---|---|---|
| No `TWITTER_PROXY` | yes (real token) | upstream `Server TLS handshake failed` → request fails |
| `TWITTER_PROXY=192.168.1.150:7892` | **no** | `cbxref_` reaches Twitter verbatim → **401** |

The 401 mode is straightforward: `TWITTER_PROXY` makes curl_cffi open a plain
`CONNECT` tunnel to port **7892**. The nft redirect only matches `dport {80,443}`
— port 7892 does not match, so this traffic **never enters mitmproxy**, so
`cbxref_` is never substituted. Twitter gets the literal placeholder → 401.

The first mode (TLS handshake fail) was initially **mis-diagnosed as Twitter
rejecting mitmproxy's non-Chrome TLS fingerprint.** That was wrong — see below.

## Correction: the root cause is GFW, not the TLS fingerprint

The "no proxy" path fails on the **mitmproxy → Twitter** hop, which has **no
proxy and therefore does not cross the GFW**. x.com is blocked in China at the
**TLS handshake stage** (SNI-based RST). That looks almost identical to a
fingerprint rejection. We never isolated the two variables.

Isolation experiment (run inside the sandbox container; plain `curl` uses an
OpenSSL TLS fingerprint, i.e. *not* Chrome — a good stand-in for mitmproxy's own
fingerprint):

| Exp | Fingerprint | Proxy (GFW) | Path | Result |
|---|---|---|---|---|
| **A** `POST guest/activate.json` | OpenSSL (non-Chrome) | yes | bypasses mitmproxy | **HTTP 200** |
| **B** `POST guest/activate.json` | OpenSSL | no | via mitmproxy | **502** (mitmproxy upstream `Server TLS handshake failed`) |
| **C** `account/settings.json`, no token | OpenSSL | yes | bypasses mitmproxy | **HTTP 404** (normal HTTP layer, not a bot-block) |

Conclusions:

- **A proves the fingerprint is irrelevant**: a definitively non-Chrome
  fingerprint gets HTTP 200 from Twitter once the GFW is crossed.
- **B reproduces the original failure and pins it on the GFW**: mitmproxy
  decrypts the request fine (log shows `POST https://172.66.0.227/1.1/
  guest/activate.json HTTP/2.0`) but its upstream TLS to api.x.com is killed —
  `Server TLS handshake failed. connection closed`. Identical to the failures we
  first blamed on fingerprinting.
- **C** confirms even an authenticated endpoint reaches Twitter's HTTP layer
  (404, not a TLS reset or 403 block) over a non-Chrome fingerprint.

So: **every "no proxy" failure was the GFW (mitmproxy's upstream hop isn't
tunneled), not Twitter rejecting the fingerprint.** twitter-cli's curl_cffi
impersonation is, at least for these endpoints, not load-bearing.

Still unverified: an authenticated **write** (posting a tweet) over a non-Chrome
fingerprint. TLS/fingerprint layers don't block and the auth layer only checks
the token, so it should pass, but it hasn't been tested with a real token yet.

## Why the `TWITTER_PROXY` workaround "works" — and what it costs

Because port 7892 skips the nft redirect, the Twitter traffic **bypasses the
entire egress control plane**:

- **Host allow-list is gone.** The `*.x.com` restriction lives in `inject.py`,
  keyed off the SNI seen by mitmproxy. Traffic that skips mitmproxy gets no host
  check — the tunnel can reach **anything**. (This sandbox also had
  `defaultAction: allow`, so nothing else constrained it.)
- **`cbxref_` protection is gone.** To authenticate, the cookies had to become
  **plain** vault entries, so the real token now lives in the sandbox where the
  agent / user code can read it.

Verified working: plain tokens + proxy posted the 0.6.0 release tweet
(`https://x.com/i/status/2061444950781468840`; the only remaining error was a
business-layer "tweet too long", proving auth fully passed).

**The workaround trades away two security boundaries (credential isolation +
egress host control) just to cross the GFW.** Acceptable for one-off personal
use; not acceptable as a platform default.

## The clean solution: give mitmproxy a per-host upstream proxy

Since the fingerprint is irrelevant and the only real problem is "mitmproxy's
upstream hop must cross the GFW," the fix is small — no curl_cffi, no
fingerprint forgery, no request-takeover addon. Just route mitmproxy's upstream
for GFW-blocked hosts through the proxy, using mitmproxy's native per-flow
`via`:

```python
# transparent mode: the client sends no CONNECT, so set `via` in the request
# hook (lazy connection_strategy means the upstream isn't dialed yet here).
def request(flow):
    if needs_tunnel(flow.request.host):       # e.g. *.x.com
        flow.server_conn.via = ("http", ("192.168.1.150", 7892))
```

Data flow:

```
sandbox (sees only cbxref_)
  → nft redirects 443 → mitmproxy (substitutes cbxref_, enforces host allow-list)
    → upstream via 192.168.1.150:7892 (GFW egress) → Twitter (200)
```

Everything is preserved, with the sandbox unprivileged and unaware:

| Capability | Owner | Sandbox aware? |
|---|---|---|
| `cbxref_` substitution | mitmproxy (unchanged) | no — only sees placeholder |
| host allow-list | mitmproxy (unchanged) | no |
| GFW egress | mitmproxy per-host `via` | no — no `TWITTER_PROXY` needed |
| TLS fingerprint | mitmproxy default OpenSSL | — Twitter doesn't care |

The key shift: the upstream proxy stops being a **sandbox env var (a privilege)**
and becomes a **sidecar config detail**. Trust boundary is unchanged from today —
mitmproxy already decrypts the real secret; we only point its upstream at the
GFW proxy. This is an order of magnitude simpler than the curl_cffi idea we
considered before isolating the fingerprint variable.

Open items before doing it for real:
- Decide where `needs_tunnel` host list comes from (static config vs. derived
  from egress policy). Likely the egress sidecar config, kept in the trusted
  layer.
- The upstream proxy address (`192.168.1.150:7892`) is a user-LAN clash/mihomo
  instance today; production needs a real, reachable GFW egress.
- Confirm an authenticated write over the default OpenSSL fingerprint (the one
  unverified item above).

## Decision

- **One-off personal twitter-cli:** plain tokens + `TWITTER_PROXY` works, but
  that single stream is unmanaged (no host allow-list, real token in sandbox).
- **If this becomes a platform capability:** add the per-host upstream `via` in
  the egress sidecar. Keeps cbxref_ isolation + host control + GFW egress, with
  the default fingerprint — Twitter accepts it.

## Appendix: egress nft host-control mechanism (verified live)

Understanding exactly where the host allow/deny is enforced matters for any
design that changes how mitmproxy's upstream connection is made (upstream proxy,
via, etc.). Here is the live nft ruleset from a sandbox egress sidecar, with
annotations.

### Two nft tables, two purposes

**① `table ip nat` — transparent redirect to mitmproxy**

```
chain OUTPUT {
  # DNS → custom resolver (:15353) so only allowed hosts resolve.
  udp/tcp dport 53 redirect to :15353
  # Loopback exempt (mitmproxy's own upstream connects go out directly).
  tcp daddr 127.0.0.0/8 return
  # Sandbox HTTP/HTTPS → mitmproxy (:18081) for MITM + cbxref_ substitution.
  tcp skuid != 10042 tcp dport { 80, 443 } redirect to :18081
}
```

This table only steers traffic — it performs no allow/deny. Ports other than
80/443 are not redirected (why TWITTER_PROXY on 7892 bypassed mitmproxy).

**② `table inet opensandbox` — egress filter (host allow/deny, policy drop)**

```
chain egress {
  type filter hook output priority filter; policy drop;   ← default deny
  ct state established,related accept
  meta mark 0x00000001 accept                             ← sidecar's own traffic
  oifname "lo" accept
  tcp dport 853 drop; udp dport 853 drop                  ← block DoT
  ip daddr @deny_v4 drop                                  ← explicit denies
  ip daddr @dyn_allow_v4 accept                           ← dynamic allow (DNS-resolved IPs, 6m TTL)
  ip daddr @allow_v4 accept                               ← static allow (e.g. exchange host)
  counter drop                                            ← everything else dropped
}
```

The `dyn_allow_v4` set is populated by the DNS proxy (:15353): when a host
in the policy's allow-list resolves, the real IP is added with a 6-minute TTL.
This is the actual enforcement point for `network_rules`.

### Why "addon via" preserves host control but "TWITTER_PROXY" doesn't

The key: the filter hook inspects `daddr` on the **sandbox container's own
outgoing connections**, not mitmproxy's upstream connections (those carry
`mark 0x00000001` and are accepted unconditionally).

| Scenario | sandbox connection daddr | filter sees | result |
|---|---|---|---|
| **TWITTER_PROXY** (old) | `192.168.1.150:7892` (proxy) | proxy IP only | real target never judged → **bypass** |
| **addon via** (new) | `api.x.com:443` (real target) | real target IP | host allow/deny enforced → **no bypass** |

With `TWITTER_PROXY`, the sandbox itself opened a connection to the proxy
(daddr = proxy, port 7892, not redirected by table ①), so the filter only saw
the proxy IP — the real target was hidden inside a CONNECT tunnel the filter
couldn't inspect. With the addon `via` design, the sandbox still connects to the
real target on 443, gets redirected to mitmproxy, and only then does mitmproxy
use the proxy for its upstream hop (with mark 0x00000001, exempt from the
filter). The filter judges the real target exactly as before.

### Config dependency

The proxy's own IP must be reachable from the sidecar (mitmproxy's marked
traffic is exempt from the filter, so this is about network reachability, not
nft rules). Today `192.168.1.150` is already in `allow_v4` because the exchange
endpoint lives there. A different proxy host would need the same treatment.

## Related (same session, separate commits — already on main)

- `3678d8f8` — strip OpenSandbox-internal headers (`OPENSANDBOX-EGRESS-AUTH`,
  `OpenSandbox-Secure-Access`) from the browser live-view endpoint so they don't
  trip the 501 safeguard in `ws_browser.get_live_view`.
- `aa35a4d4` — install the egress MITM CA into Chromium's NSS store
  (`$HOME/.pki/nssdb`) so the sandbox browser stops showing "Not Secure".
  (Note: curl_cffi/libcurl read the system CA bundle `/etc/ssl/certs`, which the
  egress init container already populates — so curl-based tools trust the MITM
  CA without extra work; only Chromium's separate NSS store needed fixing.)
