# Source of the Neko streaming runtime (server binary, HTML5 client, configs,
# custom Xorg drivers).
ARG BASE_IMAGE=ubuntu:24.04
FROM ghcr.io/m1k1o/neko/chromium:latest AS neko

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Set build-time arguments
ARG DEBIAN_FRONTEND=noninteractive
ARG APT_MIRROR_HOST=
ARG PIP_INDEX_URL=https://pypi.org/simple/
ARG PIP_TRUSTED_HOST=
ARG NPM_REGISTRY=

# Optional build-time HTTP proxy, empty (direct) by default. Needed only where
# the build host can't reach the public internet and there is no mirror knob —
# the GitHub CLI keyring/repo and the Playwright browser CDN. Docker exports
# these into every RUN, so list internal apt/pip/npm mirror hosts in NO_PROXY:
#   --build-arg HTTPS_PROXY=http://10.0.0.1:7890 --build-arg NO_PROXY=mirror.internal
# Lowercase defaults track the uppercase ones (curl and apt read either case).
# Deliberately ARG, never ENV: an ENV would bake the proxy into the sandbox
# runtime, where every agent HTTP call would then try to use it.
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=
ARG http_proxy=${HTTP_PROXY}
ARG https_proxy=${HTTPS_PROXY}
ARG no_proxy=${NO_PROXY}

# Set environment variables early. Deliberately NOT exporting VIRTUAL_ENV:
# a global VIRTUAL_ENV=/opt/venv makes uv/poetry treat /opt/venv as the active
# env and silently install into it (an image layer, lost on recreate). PATH
# alone makes /opt/venv the default interpreter.
ENV PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NODE_PATH=/usr/local/lib/node_modules \
    PATH="/opt/venv/bin:$PATH"

# Configure the optional apt mirror and install packages in a single layer
RUN if [ -n "$APT_MIRROR_HOST" ]; then \
        sed -i -r "s/(archive|security|ports).ubuntu.com/${APT_MIRROR_HOST}/" /etc/apt/sources.list.d/ubuntu.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
        gcc \
        g++ \
        gfortran \
        make \
        build-essential \
        pkg-config \
        libffi-dev \
        libssl-dev \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
        libjpeg-dev \
        libpng-dev \
        libfreetype6-dev \
        libopenblas-dev \
        liblapack-dev \
        vim \
        telnet \
        iputils-ping \
        tzdata \
        curl \
        wget \
        file \
        git \
        ripgrep \
        unzip \
        zip \
        sudo \
        fonts-noto \
        fonts-liberation \
        fonts-noto-cjk \
        fonts-wqy-zenhei \
        fonts-wqy-microhei \
        libreoffice \
        pandoc \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Ubuntu 24.04's archive ships Node 18. Use NodeSource's APT repository for
# the Node 24 LTS line required by agent-browser; nodejs also supplies npm.
RUN curl -fsSL --connect-timeout 30 --max-time 120 \
        -o /tmp/nodesource_setup.sh \
        https://deb.nodesource.com/setup_24.x \
    && bash /tmp/nodesource_setup.sh \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -f /tmp/nodesource_setup.sh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Configure timezone
RUN unlink /etc/localtime \
    && ln -s /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo $TZ > /etc/timezone

# Configure pip. The public PyPI index is the default; private HTTP mirrors
# can be selected with PIP_INDEX_URL and PIP_TRUSTED_HOST.
RUN mkdir -p /etc \
    && printf '[global]\nindex-url = %s\n' "$PIP_INDEX_URL" > /etc/pip.conf \
    && if [ -n "$PIP_TRUSTED_HOST" ]; then \
        printf 'trusted-host = %s\n' "$PIP_TRUSTED_HOST" >> /etc/pip.conf; \
    fi

# Configure npm. Upstream registry by default; NPM_REGISTRY selects a mirror,
# same knob the backend/frontend images take (build-and-push.sh passes it).
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi \
    && npm config set fetch-retries 5 \
    && npm config set fetch-retry-mintimeout 20000 \
    && npm config set fetch-retry-maxtimeout 120000 \
    && npm config set fetch-timeout 600000

# GitHub CLI (gh) from the official apt repo — Ubuntu's own repo lags and the
# aliyun mirror doesn't carry it. Follows the upstream install instructions.
# cli.github.com has no mirror; set HTTPS_PROXY at build time if it is blocked.
# The -s check fails the build on a truncated key rather than leaving an empty
# keyring that turns into a confusing "Unable to locate package gh" later.
RUN mkdir -p -m 755 /etc/apt/keyrings /etc/apt/sources.list.d \
    && curl -fsSL --connect-timeout 30 --max-time 120 \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
        https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    && [ -s /etc/apt/keyrings/githubcli-archive-keyring.gpg ] \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y gh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install common frontend packages globally
RUN npm install -g \
    typescript@5.8.3 \
    ts-node@10.9.2 \
    pnpm@11.0.0 \
    --no-audit \
    --no-fund \
    --prefer-online

# Install office authoring packages separately so transient npm timeouts do not
# force reinstalling the larger generic frontend package set.
RUN npm install -g \
    docx \
    pptxgenjs \
    playwright@1.59.0 \
    && rm -rf /var/lib/apt/lists/*

# Configure vim. Must be /etc/vim/vimrc.local, not /etc/vimrc: Debian builds vim
# with SYS_VIMRC_FILE=/etc/vim/vimrc, so an /etc/vimrc is read by nobody (the
# settings this replaces never took effect — tabstop stayed 8).
COPY vimrc /etc/vim/vimrc.local

# Enable colored ls/grep for all interactive shells (ttyd runs non-login bash).
# Set TERM fallback so dircolors can generate LS_COLORS even when the parent
# process (start-stop-daemon) hasn't set it yet.
RUN printf 'export TERM="${TERM:-xterm-256color}"\neval "$(dircolors -b)"\nalias ls="ls --color=auto"\nalias grep="grep --color=auto"\n' >> /etc/bash.bashrc

# Sandbox env vault entries, refreshed by start_terminal before each (re)connect.
# ttyd freezes its own environ at start, so the file is how a new terminal picks
# up entries added since. Absent when egress injection is off, hence the guard —
# `if` rather than `[ -r … ] &&` so an absent file doesn't leave bashrc, and the
# first prompt's $?, non-zero. Must be bash.bashrc: ttyd runs non-login bash, so
# /etc/profile.d is never read.
RUN printf 'if [ -r /run/cubeplex/sandbox-env.sh ]; then . /run/cubeplex/sandbox-env.sh; fi\n' >> /etc/bash.bashrc

# Create virtual environment and install common Python packages
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir \
        requests \
        numpy \
        pandas \
        matplotlib \
        pillow \
        beautifulsoup4 \
        lxml \
        pyyaml \
        python-dateutil \
        pytz \
        httpx \
        aiohttp \
        python-docx \
        fonttools \
        pdf2image \
        reportlab \
        pdfplumber \
        pypdf \
        openpyxl \
        xlsxwriter \
        python-pptx \
        jupyterlab \
        ipykernel \
        nbformat \
        nbconvert \
        markitdown \
        playwright==1.59.0 \
    && /opt/venv/bin/playwright install --with-deps chromium

# Curated premium libre fonts used by the preinstalled `pptx` (deckcraft) skill
# and available to any deck/doc the agent builds: Inter + serif/display Latin
# (OFL), LXGW WenKai + Smiley Sans + ZCOOL family (OFL CJK). All SIL OFL,
# downloaded from their official upstreams by stage-fonts.sh into ./fonts/
# (gitignored; ~54MB). Run that before building. fc-cache so LibreOffice +
# matplotlib (the skill's overflow checker) resolve them by family name.
COPY fonts/ /usr/share/fonts/truetype/cubeplex/
RUN fc-cache -f

# Create app directory
RUN mkdir -p /workspace
ENV HOME=/workspace

# ---------------------------------------------------------------------------
# Neko browser-takeover stack (started on demand, see neko/start-browser.sh).
# Lets a user watch and take over the sandbox browser from the CubePlex frontend
# while the agent drives the same Chromium over CDP.
# ---------------------------------------------------------------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xserver-xorg-core xserver-xorg-video-dummy x11-xserver-utils xauth \
        openbox pulseaudio dbus-x11 supervisor xclip \
        libgtk-3-0 libxtst6 libxcvt0 \
        libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav \
        libnss3-tools \
        ttyd \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# agent-browser CLI: the agent drives the streamed Chromium via CDP with it
# (`agent-browser connect 9222`). Requires the Node 24/pnpm 11 toolchain above.
RUN npm install -g --allow-scripts=agent-browser agent-browser

# Neko runtime from the upstream image: server binary, configs, HTML5 client,
# and the two custom Xorg modules (patched dummy video + the "neko" input driver
# that exposes /tmp/xf86-input-neko.sock for event injection).
COPY --from=neko /usr/bin/neko /usr/bin/neko
COPY --from=neko /etc/neko /etc/neko
COPY --from=neko /var/www /var/www
COPY --from=neko /usr/lib/xorg/modules/drivers/dummy_drv.so /usr/lib/xorg/modules/drivers/dummy_drv.so
COPY --from=neko /usr/lib/xorg/modules/input/neko_drv.so /usr/lib/xorg/modules/input/neko_drv.so
# Upstream neko.yaml sets implicit_hosting: false (legacy multi-viewer default).
# CubePlex owns watch/takeover at the panel layer (BrowserView overlay), so the
# member must get input on the first click after "Take over" without a second
# in-Neko mouse/control toggle. See issue #423.
RUN sed -i 's/implicit_hosting: false/implicit_hosting: true/' /etc/neko/neko.yaml
# Trim the embedded Neko client UI:
# - hide the "open in new window" (popout) control — useless in the panel;
# - hide every n.eko logo (connect + loading splashes) so none flashes;
# - hide the player-overlay (the "click to play / unmute" prompt);
# - hide Neko's host/control mouse chrome (redundant with CubePlex Take over;
#   with implicit_hosting the interactive toggle is already a no-op status icon).
# The script forces the <video> muted so it autoplays (browser autoplay policy
# only allows muted autoplay; volume=0 alone still triggers the prompt), making
# the now-hidden overlay unnecessary. Keep clipboard + fullscreen, but add an
# explicit close (× / Esc) because upstream clipboard has no close button and
# iframe embeds often make "click outside" unreliable.
RUN sed -i 's#</head>#<style>.video-menu li:has(.fa-external-link-alt){display:none!important}.video-menu li:has(.fa-mouse-pointer),.video-menu li:has(.fa-computer-mouse),.video-menu li:has(.fa-mouse){display:none!important}.logo{display:none!important}.player-overlay{display:none!important}</style><style id="cubeplex-clipboard-close">.clipboard .cubeplex-clipboard-close{position:absolute;top:2px;right:4px;z-index:2;width:22px;height:22px;border:0;border-radius:4px;background:transparent;color:#dcddde;font:700 16px/22px Whitney,Helvetica,Arial,sans-serif;cursor:pointer;opacity:.85}.clipboard .cubeplex-clipboard-close:hover{opacity:1;background:rgba(0,0,0,.25)}.clipboard textarea{padding-right:28px;box-sizing:border-box}</style><script>setInterval(function(){var v=document.querySelector("video");if(v){try{v.muted=true;if(v.paused)v.play().catch(function(){});}catch(e){}}},250);</script><script id="cubeplex-clipboard-close">(function(){function closeClipboard(){if(document.querySelector(".clipboard"))document.body.click()}function ensureClose(panel){if(!panel||panel.querySelector(".cubeplex-clipboard-close"))return;var btn=document.createElement("button");btn.type="button";btn.className="cubeplex-clipboard-close";btn.setAttribute("aria-label","Close clipboard");btn.title="Close";btn.textContent="\u00d7";btn.addEventListener("click",function(e){e.preventDefault();e.stopPropagation();closeClipboard()});panel.appendChild(btn)}function scan(){document.querySelectorAll(".clipboard").forEach(ensureClose)}document.addEventListener("keydown",function(e){if(e.key==="Escape"&&document.querySelector(".clipboard")){e.preventDefault();closeClipboard()}},true);var obs=new MutationObserver(scan);if(document.body){obs.observe(document.body,{childList:true,subtree:true});scan()}else{document.addEventListener("DOMContentLoaded",function(){obs.observe(document.body,{childList:true,subtree:true});scan()})}})();</script></head>#' /var/www/index.html

# Default sandbox user `cubeplex` at uid/gid 1000 (matches backend agent
# run_uid/run_gid). Also used by the Neko browser stack (ENV USER below).
# Remove a pre-existing uid-1000 user conditionally so the build stays
# deterministic across base-image variants (never hard-fail).
RUN (getent passwd 1000 && userdel -r "$(getent passwd 1000 | cut -d: -f1)" 2>/dev/null || true) \
    && (getent group 1000 && groupdel "$(getent group 1000 | cut -d: -f1)" 2>/dev/null || true) \
    && groupadd -g 1000 cubeplex \
    && useradd -u 1000 -g 1000 -m -d /home/cubeplex -s /bin/bash cubeplex \
    && usermod -aG audio,video cubeplex \
    && mkdir -p /var/log/neko /tmp/runtime-neko /home/cubeplex/.config/chromium \
    && chown -R cubeplex:cubeplex /var/log/neko /tmp/runtime-neko /home/cubeplex \
    && chmod 700 /tmp/runtime-neko \
    && chown cubeplex:cubeplex /workspace

# Passwordless root for the sandbox user. The web terminal runs as cubeplex
# (setpriv --reuid, see SandboxService.start_terminal) so that files it creates
# stay writable by the agent, which runs as the same uid — this is what makes
# `sudo apt install` work there. Consequence: uid 1000 is no longer a privilege
# boundary against the agent, since agent commands share that uid. The container
# is the boundary. Matched by username, not group: setpriv --clear-groups wipes
# supplementary groups, so a `sudo` group membership would not survive.
RUN echo 'cubeplex ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-cubeplex \
    && chmod 0440 /etc/sudoers.d/90-cubeplex \
    && visudo -c

# Run Xorg fully rootless (drop the setuid Xorg.wrap) so the neko input socket is
# owned by the cubeplex user, and load the dummy config from the default path
# (Xorg.wrap rejects an absolute -config path).
RUN rm -f /usr/lib/xorg/Xorg.wrap \
    && cp /etc/neko/xorg.conf /etc/X11/xorg.conf \
    && sed -i 's# -config /etc/neko/xorg.conf##' /etc/neko/supervisord.conf \
    && sed -i 's/chown=root:neko/chown=root:root/' /etc/neko/supervisord.conf

# Stream the image's own Playwright Chromium (version-matched to Playwright so
# CDP per-page sessions work); stable symlink to the versioned binary.
RUN ln -s "$(ls -d /ms-playwright/chromium-*/chrome-linux64 | head -1)/chrome" /ms-playwright/chrome

COPY neko/chromium.conf /etc/neko/supervisord/chromium.conf
COPY neko/start-browser.sh /usr/local/bin/start-browser.sh
COPY neko/launch-chrome.sh /usr/local/bin/launch-chrome.sh
RUN chmod +x /usr/local/bin/start-browser.sh /usr/local/bin/launch-chrome.sh

# WebRTC needs a TURN server: the OpenSandbox gateway proxies HTTP/WS only, so
# Neko's media (UDP) can't traverse it — without a relay, ICE fails and the live
# view never shows a picture. Point Neko at a coturn reachable from both the
# viewer's browser and the sandbox pod. These are build-time overridable:
#   docker build --build-arg NEKO_TURN_URL=turn:<host>:3478 \
#                --build-arg NEKO_TURN_USER=... --build-arg NEKO_TURN_CRED=...
# Defaults target the test-cluster coturn; production MUST override them.
ARG NEKO_TURN_URL=turn:192.168.1.208:3478
ARG NEKO_TURN_USER=neko
ARG NEKO_TURN_CRED=neko

# Desktop 1280×900 (16:10-ish) instead of Neko's default 1280×720 (16:9).
# Side-panel live view is tall; a taller virtual desktop fills more of the
# rail when letterboxed and looks less flat during watch/takeover (#424).
# Frontend BrowserView fits the iframe host to the same aspect.
# First click after CubePlex "Take over" grants control (no in-Neko mouse step).
# Env wins over /etc/neko/neko.yaml if the image is rebuilt from a newer base.
# USER=cubeplex is for Neko/supervisord child processes (chromium conf uses
# %(ENV_USER)s). Container control plane stays root; agent commands run as
# cubeplex via OpenSandbox RunCommandOpts.uid/gid from the backend.
ENV USER=cubeplex \
    DISPLAY=:99.0 \
    XDG_RUNTIME_DIR=/tmp/runtime-neko \
    NEKO_SERVER_BIND=:8080 \
    NEKO_DESKTOP_SCREEN=1280x900@30 \
    NEKO_PLUGINS_ENABLED=true \
    NEKO_PLUGINS_DIR=/etc/neko/plugins/ \
    NEKO_SESSION_IMPLICIT_HOSTING=true \
    NEKO_WEBRTC_ICELITE=false \
    NEKO_WEBRTC_ICESERVERS_FRONTEND="[{\"urls\":[\"${NEKO_TURN_URL}\"],\"username\":\"${NEKO_TURN_USER}\",\"credential\":\"${NEKO_TURN_CRED}\"}]" \
    NEKO_WEBRTC_ICESERVERS_BACKEND="[{\"urls\":[\"${NEKO_TURN_URL}\"],\"username\":\"${NEKO_TURN_USER}\",\"credential\":\"${NEKO_TURN_CRED}\"}]"

# ---------------------------------------------------------------------------
# User-installable runtime layer on /workspace (PVC). Configured AFTER all
# baseline installs so the build-time `pip install` / `npm install -g` land in
# /opt/venv and /usr/local/lib/node_modules (image layers, not PVC).
#
# Effect at runtime:
#   pip install xxx        -> /workspace/.python-packages (persisted on PVC)
#   npm install -g xxx     -> /workspace/.npm-global      (persisted on PVC)
#   python -c "import xxx" -> sees user packages + baseline (sitecustomize.py
#                              prepends the overlay to sys.path)
#   require("xxx")         -> sees user globals + baseline globals (NODE_PATH)
#   user-installed CLIs    -> on PATH (workspace bin dirs first)
#   python -m venv / uv    -> clean self-contained envs, untouched by the
#                              overlay (see below)
#
# The overlay is wired into /opt/venv only — via its site-level pip.conf and
# an executable .pth in its site-packages — NOT via global PIP_PREFIX /
# PYTHONPATH env vars. Global env vars leak into every interpreter: PIP_PREFIX
# hijacks `pip install` inside agent-created venvs (packages land in the
# overlay, not the venv), and PYTHONPATH outranks a venv's own site-packages
# (overlay/baseline versions shadow whatever the venv installed), so venvs
# looked broken. Site-level scoping keeps the persistence behaviour for the
# default interpreter and leaves agent-created envs alone.
#
# Why --prefix (not --target): pip --target's known issues include not
# emitting console_scripts when bin/ already has entries, and treating the
# install as a flat dir with no awareness of /opt/venv's packages (so deps
# get installed redundantly). --prefix uses the standard sysconfig layout
# (prefix/bin, prefix/lib/pythonX.Y/site-packages), generates entry points
# correctly, and sees baseline packages so dependency resolution dedups.
# Trade-off: `pip uninstall xxx` still refuses to touch /workspace packages
# (the venv is at /opt/venv); `rm -rf` the package dir to remove cleanly.
#
# Mechanism: an executable .pth line (not sitecustomize.py — Debian/Ubuntu
# ship /usr/lib/pythonX.Y/sitecustomize.py in the stdlib dir, which shadows a
# venv-local one). The .pth front-inserts the overlay (site.addsitedir alone
# appends, which would let baseline versions shadow user-upgraded packages)
# and derives pythonX.Y from the running interpreter, so a Python bump can't
# silently desync a hardcoded path.
# ---------------------------------------------------------------------------
RUN printf '[install]\nprefix = /workspace/.python-packages\n' > /opt/venv/pip.conf \
    && PYVER="$(/opt/venv/bin/python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')" \
    && printf 'import sys, site; _o = f"/workspace/.python-packages/lib/python{sys.version_info[0]}.{sys.version_info[1]}/site-packages"; _b = len(sys.path); site.addsitedir(_o); _n = sys.path[_b:]; del sys.path[_b:]; sys.path[:0] = _n\n' \
        > "/opt/venv/lib/python${PYVER}/site-packages/zz-workspace-overlay.pth"

ENV NPM_CONFIG_PREFIX=/workspace/.npm-global \
    NODE_PATH=/workspace/.npm-global/lib/node_modules:/usr/local/lib/node_modules \
    PATH="/workspace/.npm-global/bin:/workspace/.python-packages/bin:/opt/venv/bin:$PATH"

WORKDIR /workspace
