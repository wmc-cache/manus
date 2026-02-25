# ============================================================
# Manus Sandbox 容器镜像
# ============================================================
# 基于 Ubuntu 22.04，预装 Python、Node.js 和常用开发工具链
# 以非 root 用户 (ubuntu:1000) 运行，确保安全隔离
# ============================================================

FROM ubuntu:22.04

LABEL maintainer="manus-mvp"
LABEL description="Manus AI Agent Sandbox Environment"
LABEL version="1.0"

# 避免交互式安装提示
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV TERM=xterm-256color

# ---- 1. 系统基础包 ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 基础工具
    bash \
    ca-certificates \
    curl \
    wget \
    git \
    unzip \
    zip \
    tar \
    gzip \
    less \
    # 网络工具
    net-tools \
    iputils-ping \
    dnsutils \
    # 文本处理
    jq \
    bc \
    # 进程管理
    procps \
    psmisc \
    # 构建工具
    build-essential \
    # Python
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    # 其他
    sudo \
    locales \
    && rm -rf /var/lib/apt/lists/*

# ---- 2. Node.js 22.x ----
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g pnpm yarn \
    && rm -rf /var/lib/apt/lists/*

# ---- 3. Python 常用包 ----
RUN pip3 install --no-cache-dir \
    requests \
    beautifulsoup4 \
    pandas \
    numpy \
    matplotlib \
    seaborn \
    plotly \
    openpyxl \
    tabulate \
    markdown \
    pyyaml \
    httpx \
    aiohttp \
    fastapi \
    flask \
    pillow

# ---- 4. 创建非 root 用户 ----
RUN groupadd -g 1000 ubuntu \
    && useradd -m -u 1000 -g 1000 -s /bin/bash ubuntu \
    && echo "ubuntu ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/ubuntu

# ---- 5. 创建工作目录 ----
# /tmp/workspace 用于 volume 挂载（避免 docker exec cwd 问题）
RUN mkdir -p /tmp/workspace \
    && chmod 777 /tmp/workspace \
    && mkdir -p /home/ubuntu/workspace \
    && chown -R ubuntu:ubuntu /home/ubuntu

# ---- 6. 配置用户环境 ----
USER root
WORKDIR /tmp/workspace

# 配置 bash 环境
RUN echo 'export PS1="\[\033[01;32m\]sandbox\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ "' >> /root/.bashrc \
    && echo 'export PATH="/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"' >> /root/.bashrc

# 健康检查：检查 /tmp/workspace 目录是否可访问
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD test -d /tmp/workspace || exit 1

# 默认命令：保持容器运行
CMD ["sleep", "infinity"]
