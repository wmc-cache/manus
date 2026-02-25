#!/bin/bash
# ============================================================
# 构建 Manus 沙箱 Docker 镜像
# ============================================================
# 用法: ./build-sandbox.sh [--no-cache]
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="manus-sandbox:latest"
NETWORK_NAME="manus-sandbox-net"

echo "============================================"
echo "  构建 Manus 沙箱镜像"
echo "============================================"

# 构建参数
BUILD_ARGS=""
if [[ "$1" == "--no-cache" ]]; then
    BUILD_ARGS="--no-cache"
    echo "  模式: 无缓存构建"
fi

# 构建镜像
echo ""
echo "[1/3] 构建沙箱镜像: ${IMAGE_NAME}"
docker build ${BUILD_ARGS} \
    -t "${IMAGE_NAME}" \
    -f "${SCRIPT_DIR}/sandbox/Dockerfile" \
    "${SCRIPT_DIR}/sandbox/"

echo ""
echo "[2/3] 确保沙箱网络存在: ${NETWORK_NAME}"
docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1 || \
    docker network create --driver bridge "${NETWORK_NAME}"

echo ""
echo "[3/3] 验证镜像"
docker run --rm "${IMAGE_NAME}" bash -c '
    echo "  Python: $(python3 --version)"
    echo "  Node.js: $(node --version)"
    echo "  npm: $(npm --version)"
    echo "  User: $(whoami) (UID=$(id -u))"
    echo "  Workdir: $(pwd)"
'

echo ""
echo "============================================"
echo "  沙箱镜像构建完成!"
echo "  镜像: ${IMAGE_NAME}"
echo "  网络: ${NETWORK_NAME}"
echo "============================================"
echo ""
echo "启用 Docker 沙箱:"
echo "  export MANUS_DOCKER_SANDBOX=true"
echo "  然后重启后端服务"
