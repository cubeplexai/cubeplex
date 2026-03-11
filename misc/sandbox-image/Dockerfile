FROM hub.sensedeal.vip/library/ubuntu:24.04

# Set build-time arguments
ARG DEBIAN_FRONTEND=noninteractive

# Set environment variables early
ENV PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

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
        fonts-noto-cjk \
        fonts-wqy-zenhei \
        fonts-wqy-microhei \
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
RUN npm config set registry https://registry.npmmirror.com

# Install common frontend packages globally
RUN npm install -g \
    typescript \
    ts-node \
    vite \
    create-react-app \
    next \
    pnpm

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
        aiohttp

# Create app directory
RUN mkdir -p /workspace
ENV HOME=/workspace

WORKDIR /workspace
