FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl wget git build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY mcp-execution/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir \
    pandas numpy matplotlib seaborn plotly \
    openpyxl tabulate requests beautifulsoup4

COPY mcp-shared/mcp_base.py /app/mcp_base.py
COPY mcp-execution/server.py .

RUN mkdir -p /tmp/manus_workspace

ENV MANUS_WORKSPACE_BASE=/tmp/manus_workspace
ENV MCP_EXECUTION_PORT=8102

EXPOSE 8102

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8102/health')" || exit 1

CMD ["python3", "server.py"]
