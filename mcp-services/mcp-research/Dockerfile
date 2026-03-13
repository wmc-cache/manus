FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY mcp-research/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp-shared/mcp_base.py /app/mcp_base.py
COPY mcp-research/server.py .

ENV MCP_RESEARCH_PORT=8104

EXPOSE 8104

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8104/health')" || exit 1

CMD ["python3", "server.py"]
