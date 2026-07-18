# wildclawbench-ubuntu:v1.4 — v1.3-browser + Playwright + Chromium pre-baked.
#
# WHY: the Creative task `repo_to_homepage` (and any task whose prompt asks for a
# Playwright full-page screenshot) burns its whole 600s wall-clock budget on
# `pip install playwright` + `playwright install chromium` (~180MB) +
# `playwright install-deps chromium` (apt), and never reaches the screenshot
# script → gating `screenshot_exists` FAIL → score 0. The task prompt explicitly
# says "use Playwright + Headless Chromium", so steering the agent to a different
# tool would contradict the task. Baking the deps into the image makes all three
# agent commands no-ops and lets it proceed to the screenshot in seconds.
#
# Build on .150 (this host's docker proxies through clash and EOFs on large
# pushes; .150 reaches the registry directly):
#   ssh 192.168.1.150
#   docker build -t hub.sensedeal.vip/library/wildclawbench-ubuntu:v1.4 \
#     -f wildclawbench-ubuntu.v1.4.Dockerfile .
#   docker push hub.sensedeal.vip/library/wildclawbench-ubuntu:v1.4
# (push may need a retry loop for transient "unknown blob".)
# Then prepull on the 3 opensandbox nodes via the sandbox-prepull-wcb DaemonSet
# (update image tag), and set org default_image / --image to :v1.4.

FROM hub.sensedeal.vip/library/wildclawbench-ubuntu:v1.3-browser

# Playwright Python package + its Chromium binary (lands in /root/.cache/ms-playwright/)
# + the apt system libs Chromium needs. install-deps must run as root (image is root).
# (v1.3 image python is NOT PEP 668 extern-managed — plain pip3 install works.)
#
# Proxy: the v1.3 base image ENV ships http_proxy=http://100.104.40.233:7897 (broken,
# can't reach pypi). Override to the working LAN proxy for the build only, then unset.
# At runtime opensandbox injects its own proxy over the image ENV anyway, so what we
# leave here is moot — unsetting keeps the image clean.
ENV HTTP_PROXY=http://192.168.1.215:7892 \
    HTTPS_PROXY=http://192.168.1.215:7892 \
    http_proxy=http://192.168.1.215:7892 \
    https_proxy=http://192.168.1.215:7892 \
    NO_PROXY=localhost,127.0.0.1,10.0.0.0/8,192.168.0.0/16,100.104.0.0/16 \
    no_proxy=localhost,127.0.0.1,10.0.0.0/8,192.168.0.0/16,100.104.0.0/16
RUN pip3 install playwright \
    && python3 -m playwright install chromium \
    && python3 -m playwright install-deps chromium
ENV HTTP_PROXY="" HTTPS_PROXY="" http_proxy="" https_proxy="" \
    NO_PROXY="" no_proxy=""

# Smoke: confirm Chromium actually launches headless. Fails the build early if not.
RUN python3 -c "\
from playwright.sync_api import sync_playwright; \
p = sync_playwright().start(); \
b = p.chromium.launch(headless=True); \
b.close(); p.stop(); \
print('playwright chromium OK')"

ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
