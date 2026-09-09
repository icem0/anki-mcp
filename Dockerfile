FROM python:3.12-slim

# Git nur für potential git+http pip installs
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends curl ca-certificates >/dev/null && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps first (better layer cache)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Code
COPY server.py /app/server.py
COPY src /app/src

ENV PYTHONPATH=/app:/app/src
ENV ANKI_MCP_HOST=0.0.0.0
ENV ANKI_MCP_PORT=8765

EXPOSE 8765

# Healthcheck (TCP only — MCP requires initialize before accepting RPC, so we just confirm the server responds at /mcp; 4xx is fine, only connection refused fails)
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -s --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/mcp | grep -qE '^(2|4)[0-9]{2}$' || exit 1

# Token is supplied via Arcane envContent; if not set, server runs in anonymous mode (token=None → disabled)
CMD ["python", "-m", "server"]
