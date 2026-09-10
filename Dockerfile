FROM python:3.12-slim

# curl for healthcheck; ca-certificates for https (reverse proxy in front of MCP)
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

# Healthcheck: 401 (unauthenticated) is also a valid "server is up" signal,
# since the server deliberately rejects anonymous traffic. Anything 2xx/3xx/4xx
# means the port is listening; only connect-refused / 5xx fails the check.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 CMD curl -s --max-time 2 http://127.0.0.1:8765/mcp -o /dev/null -w '%{http_code}' | grep -qE '^[1-4][0-9]{2}$' || exit 1

# Entrypoint: validates that ANKI_MCP_TOKEN is set, then runs the MCP server.
# Secrets are provided via the compose env_file (or whatever the orchestrator
# uses) — this image has no built-in dependency on any particular secret
# manager. ANKI_MCP_TOKEN itself is never written to disk.
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "server"]