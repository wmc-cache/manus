FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl ca-certificates \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libgbm1 \
    libgtk-3-0 libasound2 libxshmfence1 libgles2 \
    fonts-wqy-zenhei fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

COPY mcp-browser/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY mcp-shared/mcp_base.py /app/mcp_base.py
COPY mcp-browser/server.py .

ENV MCP_BROWSER_PORT=8103

EXPOSE 8103

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8103/health')" || exit 1

CMD ["python3", "server.py"]
