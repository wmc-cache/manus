FROM python:3.11-slim

WORKDIR /app

COPY mcp-filesystem/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制共享库
COPY mcp-shared/mcp_base.py /app/mcp_base.py

# 复制服务代码
COPY mcp-filesystem/server.py .

RUN mkdir -p /tmp/manus_workspace

ENV MANUS_WORKSPACE_BASE=/tmp/manus_workspace
ENV MCP_FILESYSTEM_PORT=8101

EXPOSE 8101

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8101/health')" || exit 1

CMD ["python3", "server.py"]
