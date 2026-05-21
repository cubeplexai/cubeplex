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
        unzip \
        zip \
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
# - hide the big n.eko logo on the connecting splash (keep the small spinner).
# Keep clipboard + fullscreen.
RUN sed -i 's#</head>#<style>.video-menu li:has(.fa-external-link-alt){display:none!important}.loading .logo{display:none!important}</style></head>#' /var/www/index.html

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
RUN chmod +x /usr/local/bin/start-browser.sh

ENV USER=neko \
    DISPLAY=:99.0 \
    XDG_RUNTIME_DIR=/tmp/runtime-neko \
    NEKO_SERVER_BIND=:8080 \
    NEKO_PLUGINS_ENABLED=true \
    NEKO_PLUGINS_DIR=/etc/neko/plugins/

WORKDIR /workspace
