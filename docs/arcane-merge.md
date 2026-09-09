# Arcane Merge — anki-mcp als Sidecar im anki-sync Projekt

**Status: LIVE 2026-09-09** (LXC 101 / Arcane-Engine)

## Architektur

```
Cloudflare Tunnel → anki-sync.sa-ma.online/sync
                  → anki-mcp.sa-ma.online/mcp
```

```
anki-sync (offiziell, pip anki==25.07.5, port 8080) — Arcane-Project b346dc35
  + anki-mcp (unser Fork, port 8765)         — als zweiter Service im selben Compose
```

Beide Services im **Arcane-Projekt `anki-sync`** (Projekt-ID `b346dc35-a88c-48b2-a608-9beeea77a713`).
Working-Dir auf LXC 101: `/var/lib/docker/volumes/arcane_arcane-data/_data/projects/anki-sync/`.

NICHT das Standalone-Setup (mariadb + anki-sync-api + anki-mcp) aus dem Repo-Root `compose.yaml` —
das hier ist die offizielle Anki-Sync-Server-Variante.

## Files im Arcane-Working-Dir

- `compose.yaml` — beide Services
- `.env` — **LEER, wird manuell befüllt** (Matthias pflegt)
- `mcp/` — Source (vendor + server.py + Dockerfile)

## .env (in Arcane-Working-Dir, NICHT im Repo)

```
SYNC_USER1=matthias:<pwd>
ANKI_MCP_TOKEN=<43 chars>
```

Arcane-Compose referenziert mit `${SYNC_USER1}` und `${ANKI_MCP_TOKEN}` — keine harten Tokens in der YAML.

## compose.yaml (Arcane-Variante)

```yaml
name: anki-sync

services:
  anki-sync:
    container_name: anki-sync
    image: python:3.12-slim
    working_dir: /anki
    command: ["sh","-c","pip install --no-cache-dir anki==25.07.5 && python -m anki.syncserver"]
    ports: ["8081:8080"]
    volumes: ["anki-sync:/sync"]
    environment:
      - SYNC_USER1=${SYNC_USER1}
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "python -c 'import socket; s=socket.socket(); s.settimeout(2); s.connect((\"127.0.0.1\",8080)); s.close()'"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 60s

  mcp:
    container_name: anki-mcp
    build: ./mcp
    ports: ["8765:8765"]
    environment:
      - ANKI_MCP_TOKEN=${ANKI_MCP_TOKEN}
    depends_on:
      anki-sync:
        condition: service_healthy
    restart: unless-stopped

volumes:
  anki-sync: {}
```

## Deploy-Workflow (Disk-basiert, nicht via Arcane-UI)

```bash
# 1. Source/Compose nach LXC 101 pushen
scp -i ~/.ssh/proxmox_helga compose.yaml root@192.168.178.5:/tmp/c.yaml
ssh -i ~/.ssh/proxmox_helga root@192.168.178.5 'pct push 101 /tmp/c.yaml /tmp/c.yaml'
ssh -i ~/.ssh/proxmox_helga root@192.168.178.5 \
  'pct exec 101 -- cp /tmp/c.yaml /var/lib/docker/volumes/arcane_arcane-data/_data/projects/anki-sync/compose.yaml'

# 2. .env befüllen (manuell oder via Infisical-Export)
# /var/lib/docker/volumes/arcane_arcane-data/_data/projects/anki-sync/.env

# 3. Redeploy via Arcane-Engine-API
curl -X POST -H "X-API-Key: $ARCANE_API" \
  http://192.168.178.11:3552/api/environments/0/projects/b346dc35-a88c-48b2-a608-9beeea77a713/redeploy
```

Arcane-Engine-API-Basis = `http://192.168.178.11:3552/api/...` (HTTP, NICHT HTTPS).
Niemals `arcane.sa-ma.online` — das ist SvelteKit-Frontend, redirected nicht zur Engine-API.

## Lessons (für Memory/Skill-Updates)

- Anki-Sync-Server hat **keinen HTTP-Health-Endpoint** (`/sync/health` → 405). TCP-only-Healthcheck via python-socket ist der Workaround.
- `python:3.12-slim` ohne `curl` — Healthcheck entweder mit python-socket oder busybox-wget.
- `pct push` schlägt fehl wenn Ziel-File existiert. Workaround: `pct exec 101 -- bash -c "cat > /tmp/x"`.
- Arcane-Compose liest `.env` automatisch aus dem Working-Dir — keine Arcane-API-Notwendigkeit für Secrets.
- Arcane erkennt Compose-File-Änderungen NICHT automatisch — `POST /redeploy` ist immer nötig.
