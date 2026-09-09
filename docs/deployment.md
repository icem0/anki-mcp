# Deployment

Standard `docker compose` workflow. The stack is two services (`anki-sync`, `mcp`) and one named volume.

## First-time setup

```bash
git clone https://github.com/icem0/anki-mcp.git
cd anki-mcp

cp .env.example .env
$EDITOR .env
# Required:
#   SYNC_USER1=matthias
#   ANKI_MCP_TOKEN=$(openssl rand -hex 32)

docker compose up -d --build
docker compose ps
```

Expected output (status transitions to `healthy` within ~30 s):

```
NAME            STATUS
anki-sync       Up 12 seconds (healthy)
anki-mcp        Up 12 seconds (healthy)
```

## Verifying the MCP endpoint

```bash
TOKEN=$(grep ^ANKI_MCP_TOKEN .env | cut -d= -f2)

curl -s -X POST http://localhost:8765/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
```

A successful response includes a `Mcp-Session-Id` header and a JSON-RPC result.

## List available tools

```bash
curl -s -X POST http://localhost:8765/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <session-id-from-initialize>" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | jq '.result.tools[].name'
```

Should print 10 tool names.

## Update to a new version

```bash
git pull
docker compose build --pull
docker compose up -d
docker compose ps   # confirm both healthy
```

## Inspect the collection volume

```bash
docker volume inspect anki-sync_anki-sync
docker compose exec anki-sync ls /sync/matthias
```

You should see:

```
collection.anki2      # main database
collection.anki2-shm  # WAL shared memory
collection.anki2-wal  # WAL log
media/                # media files (images, audio, etc.)
```

## Back up the collection

```bash
docker compose exec anki-sync sqlite3 /sync/matthias/collection.anki2 ".backup '/sync/matthias/backup.anki2'"
docker compose cp anki-sync:/sync/matthias/backup.anki2 ./backup-$(date +%F).anki2
```

## Logs

```bash
docker compose logs -f mcp
docker compose logs -f anki-sync
```

## Stop, restart, remove

```bash
docker compose stop            # stop, keep containers
docker compose restart         # restart in place
docker compose down            # stop and remove containers, keep volume
docker compose down -v         # stop, remove containers, AND delete the collection volume
```

**Warning:** `docker compose down -v` deletes the named volume and therefore the collection. Use it only if you are certain Anki Desktop has already synced everything to AnkiWeb and you want a clean slate.

## Reverse proxy

The MCP endpoint (`/mcp`) is the only public-facing service. The sync port (`8081`) should not be exposed to the public internet. A typical setup:

- Public: `https://anki.example.com/mcp` → `http://mcp:8765/mcp` (TLS termination, auth, rate limiting)
- Private (LAN only): `http://localhost:8081/sync` (Anki Desktop clients on the same network)

Any reverse proxy works (nginx, Caddy, Traefik, Cloudflare Tunnel, etc.). Auth is already handled by the Bearer token, so the proxy can pass `Authorization` through unchanged.
