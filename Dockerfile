FROM python:3.12-slim

# curl for healthcheck; ca-certificates for https (Infisical API)
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends curl ca-certificates >/dev/null && \
    rm -rf /var/lib/apt/lists/*

# Infisical CLI (static binary, single-file, ~30 MB). Used to inject secrets
# at container start without baking them into the image or shipping them via
# env_file. See https://infisical.com/docs/cli/overview
#
# NOTE: asset naming changed around v0.42. The old
#   infisical/releases/download/infisical-cli-vX.Y.Z/infisical-cli-linux-amd64
# path 404s. Current scheme (>= v0.43) is
#   Infisical/cli/releases/download/vX.Y.Z/cli_X.Y.Z_linux_amd64.tar.gz
ARG INFISICAL_CLI_VERSION=0.43.130
RUN curl -fsSL -o /tmp/infisical.tar.gz \
        "https://github.com/Infisical/cli/releases/download/v${INFISICAL_CLI_VERSION}/cli_${INFISICAL_CLI_VERSION}_linux_amd64.tar.gz" && \
    tar -xzf /tmp/infisical.tar.gz -C /usr/local/bin infisical && \
    chmod +x /usr/local/bin/infisical && \
    rm /tmp/infisical.tar.gz && \
    infisical --version

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
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD curl -s --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/mcp | grep -qE '^[1-4][0-9]{2}$' || exit 1

# Entrypoint: secrets are fetched at runtime from the configured secret manager
# (Infisical) before the MCP server starts. The container requires
# INFISICAL_TOKEN, INFISICAL_PROJECT_ID and INFISICAL_ENV in its environment
# (typically via compose env_file or k8s secret). ANKI_MCP_TOKEN itself is
# never written to disk.
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "server"]
