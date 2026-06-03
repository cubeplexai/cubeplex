# Source of the Neko streaming runtime (server binary, HTML5 client, configs,
# custom Xorg drivers). Pin/mirror this into the internal registry for builds
# that cannot reach ghcr.
FROM ghcr.io/m1k1o/neko/chromium:latest AS neko

FROM hub.sensedeal.vip/library/ubuntu:24.04

# Set build-time arguments
ARG DEBIAN_FRONTEND=noninteractive

# Set environment variables early
ENV PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    DOTNET_ROOT=/opt/dotnet \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NODE_PATH=/usr/local/lib/node_modules \
    PATH="/opt/dotnet:/opt/venv/bin:$PATH"

# Configure apt mirror and install packages in a single layer
RUN sed -i -r 's/(archive|security|ports).ubuntu.com/mirrors.cloud.aliyuncs.com/' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        vim \
        telnet \
        iputils-ping \
        tzdata \
        curl \
        wget \
        file \
        git \
        unzip \
        zip \
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
        nodejs \
        npm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Configure timezone
RUN unlink /etc/localtime \
    && ln -s /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo $TZ > /etc/timezone

# Configure pip
RUN mkdir -p /etc && printf '[global]\n\
index-url = http://mirrors.cloud.aliyuncs.com/pypi/simple/\n\
\n\
[install]\n\
trusted-host = mirrors.cloud.aliyuncs.com\n' > /etc/pip.conf

# Configure npm registry
RUN npm config set registry https://registry.npmmirror.com \
    && npm config set fetch-retries 5 \
    && npm config set fetch-retry-mintimeout 20000 \
    && npm config set fetch-retry-maxtimeout 120000 \
    && npm config set fetch-timeout 600000

# MiniMax docx skill uses the OpenXML SDK through .NET.
RUN curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && chmod +x /tmp/dotnet-install.sh \
    && /tmp/dotnet-install.sh --channel 8.0 --install-dir /opt/dotnet \
    && rm -f /tmp/dotnet-install.sh

# GitHub CLI (gh) from the official apt repo — Ubuntu's own repo lags and the
# aliyun mirror doesn't carry it. Follows the upstream install instructions.
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && out=$(mktemp) && wget -nv -O$out https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    && cat $out | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && mkdir -p -m 755 /etc/apt/sources.list.d \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y gh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install common frontend packages globally
RUN npm install -g \
    typescript@5.8.3 \
    ts-node@10.9.2 \
    pnpm@9.15.4 \
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

# Configure vim
RUN printf 'set ts=4\n\
set encoding=utf-8\n\
set expandtab\n\
set autoindent\n\
set hlsearch\n\
set termencoding=utf-8\n\
set fileencodings=utf8\n' > /etc/vimrc

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

# Create app directory
RUN mkdir -p /workspace
ENV HOME=/workspace

# ---------------------------------------------------------------------------
# Neko browser-takeover stack (started on demand, see neko/start-browser.sh).
# Lets a user watch and take over the sandbox browser from the cubebox frontend
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
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# agent-browser CLI: the agent drives the streamed Chromium via CDP with it
# (`agent-browser connect 9222`). See the preinstalled `browser` skill.
RUN npm install -g agent-browser

# Neko runtime from the upstream image: server binary, configs, HTML5 client,
# and the two custom Xorg modules (patched dummy video + the "neko" input driver
# that exposes /tmp/xf86-input-neko.sock for event injection).
COPY --from=neko /usr/bin/neko /usr/bin/neko
COPY --from=neko /etc/neko /etc/neko
COPY --from=neko /var/www /var/www
COPY --from=neko /usr/lib/xorg/modules/drivers/dummy_drv.so /usr/lib/xorg/modules/drivers/dummy_drv.so
COPY --from=neko /usr/lib/xorg/modules/input/neko_drv.so /usr/lib/xorg/modules/input/neko_drv.so
# Trim the embedded Neko client UI:
# - hide the "open in new window" (popout) control — useless in the panel;
# - hide every n.eko logo (connect + loading splashes) so none flashes;
# - hide the player-overlay (the "click to play / unmute" prompt).
# The script forces the <video> muted so it autoplays (browser autoplay policy
# only allows muted autoplay; volume=0 alone still triggers the prompt), making
# the now-hidden overlay unnecessary. Keep clipboard + fullscreen.
RUN sed -i 's#</head>#<style>.video-menu li:has(.fa-external-link-alt){display:none!important}.logo{display:none!important}.player-overlay{display:none!important}</style><script>setInterval(function(){var v=document.querySelector("video");if(v){try{v.muted=true;if(v.paused)v.play().catch(function(){});}catch(e){}}},250);</script></head>#' /var/www/index.html

# neko user at uid 1000. Remove a pre-existing uid-1000 user conditionally so the
# build stays deterministic across base-image variants (never hard-fail).
RUN (getent passwd 1000 && userdel -r "$(getent passwd 1000 | cut -d: -f1)" 2>/dev/null || true) \
    && (getent group 1000 && groupdel "$(getent group 1000 | cut -d: -f1)" 2>/dev/null || true) \
    && groupadd -g 1000 neko \
    && useradd -u 1000 -g 1000 -m -d /home/neko -s /bin/bash neko \
    && usermod -aG audio,video neko \
    && mkdir -p /var/log/neko /tmp/runtime-neko /home/neko/.config/chromium \
    && chown -R neko:neko /var/log/neko /tmp/runtime-neko /home/neko \
    && chmod 700 /tmp/runtime-neko

# Run Xorg fully rootless (drop the setuid Xorg.wrap) so the neko input socket is
# owned by the neko user, and load the dummy config from the default path
# (Xorg.wrap rejects an absolute -config path).
RUN rm -f /usr/lib/xorg/Xorg.wrap \
    && cp /etc/neko/xorg.conf /etc/X11/xorg.conf \
    && sed -i 's# -config /etc/neko/xorg.conf##' /etc/neko/supervisord.conf

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

ENV USER=neko \
    DISPLAY=:99.0 \
    XDG_RUNTIME_DIR=/tmp/runtime-neko \
    NEKO_SERVER_BIND=:8080 \
    NEKO_PLUGINS_ENABLED=true \
    NEKO_PLUGINS_DIR=/etc/neko/plugins/ \
    NEKO_WEBRTC_ICELITE=false \
    NEKO_WEBRTC_ICESERVERS_FRONTEND="[{\"urls\":[\"${NEKO_TURN_URL}\"],\"username\":\"${NEKO_TURN_USER}\",\"credential\":\"${NEKO_TURN_CRED}\"}]" \
    NEKO_WEBRTC_ICESERVERS_BACKEND="[{\"urls\":[\"${NEKO_TURN_URL}\"],\"username\":\"${NEKO_TURN_USER}\",\"credential\":\"${NEKO_TURN_CRED}\"}]"

# ---------------------------------------------------------------------------
# User-installable runtime layer on /workspace (PVC). Set AFTER all baseline
# installs so the build-time `pip install` / `npm install -g` land in
# /opt/venv and /usr/local/lib/node_modules (image layers, not PVC).
#
# Effect at runtime:
#   pip install xxx        -> /workspace/.python-packages (persisted on PVC)
#   npm install -g xxx     -> /workspace/.npm-global      (persisted on PVC)
#   python -c "import xxx" -> sees user packages + baseline (PYTHONPATH +
#                              site.py-discovered venv site-packages)
#   require("xxx")         -> sees user globals + baseline globals (NODE_PATH)
#   user-installed CLIs    -> on PATH (workspace bin dirs first)
#
# Why PIP_PREFIX (not PIP_TARGET): pip --target's known issues include not
# emitting console_scripts when bin/ already has entries, and treating the
# install as a flat dir with no awareness of /opt/venv's packages (so deps
# get installed redundantly). --prefix uses the standard sysconfig layout
# (prefix/bin, prefix/lib/pythonX.Y/site-packages), generates entry points
# correctly, and sees baseline packages so dependency resolution dedups.
# Trade-off: `pip uninstall xxx` still refuses to touch /workspace packages
# (the venv is at /opt/venv); `rm -rf` the package dir to remove cleanly.
# ---------------------------------------------------------------------------
ENV PIP_PREFIX=/workspace/.python-packages \
    PYTHONPATH=/workspace/.python-packages/lib/python3.12/site-packages:/opt/venv/lib/python3.12/site-packages \
    NPM_CONFIG_PREFIX=/workspace/.npm-global \
    NODE_PATH=/workspace/.npm-global/lib/node_modules:/usr/local/lib/node_modules \
    PATH="/workspace/.npm-global/bin:/workspace/.python-packages/bin:/opt/dotnet:/opt/venv/bin:$PATH"

WORKDIR /workspace
