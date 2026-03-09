FROM ubuntu:22.04

LABEL maintainer="manus-mvp"
LABEL description="Manus AI Agent Sandbox Environment"
LABEL version="1.0"

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV TERM=xterm-256color

# ---- 1. 系统基础包 ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl wget git unzip zip tar gzip less \
    net-tools iputils-ping dnsutils jq bc procps psmisc \
    build-essential python3 python3-pip python3-venv python3-dev \
    sudo locales \
    && rm -rf /var/lib/apt/lists/*

# ---- 1b. Desktop 环境 (Xvfb + VNC + 窗口管理器) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11vnc openbox xdotool xterm gnupg \
    fonts-wqy-zenhei fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# ---- 1c. Google Chrome ----
RUN apt-get update \
    && wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y /tmp/chrome.deb \
    && rm /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/*

# ---- 2. Node.js 22.x ----
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g pnpm yarn \
    && rm -rf /var/lib/apt/lists/*

# ---- 3. Python 常用包 ----
RUN pip3 install --no-cache-dir \
    requests beautifulsoup4 pandas numpy matplotlib seaborn plotly \
    openpyxl tabulate markdown pyyaml httpx aiohttp fastapi flask pillow

# ---- 4. 创建非 root 用户 ----
RUN groupadd -g 1000 ubuntu \
    && useradd -m -u 1000 -g 1000 -s /bin/bash ubuntu \
    && echo "ubuntu ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/ubuntu

# ---- 5. 创建工作目录 ----
RUN mkdir -p /tmp/workspace && chmod 777 /tmp/workspace \
    && mkdir -p /home/ubuntu/workspace \
    && chown -R ubuntu:ubuntu /home/ubuntu

USER root
WORKDIR /tmp/workspace

RUN echo 'export PS1="\[\033[01;32m\]sandbox\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ "' >> /root/.bashrc \
    && echo 'export PATH="/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"' >> /root/.bashrc

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD test -d /tmp/workspace || exit 1

COPY start-desktop.sh /usr/local/bin/start-desktop.sh
RUN chmod +x /usr/local/bin/start-desktop.sh

EXPOSE 5900

CMD ["/usr/local/bin/start-desktop.sh"]
