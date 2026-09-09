# Environment variables

This document lists every environment variable used by the stack. The two that hold secrets live in `.env`; the rest are hardcoded in `compose.yaml` and not user-configurable.

Copy `.env.example` to `.env` and fill in the two secrets.

## Secrets (in `.env`)

### `SYNC_USER1`

| | |
|---|---|
| **Used by** | `anki-sync` service |
| **Purpose** | Anki username whose collection is exposed by the sync server |
| **Format** | Plain Anki account username (e.g. `matthias`) |
| **Where the value comes from** | Your Anki Desktop account — File → Switch Profile, or the username shown in the account dialog |
| **Example** | `SYNC_USER1=matthias` |

### `ANKI_MCP_TOKEN`

| | |
|---|---|
| **Used by** | `mcp` service |
| **Purpose** | Bearer token that MCP clients must send on every request (`Authorization: Bearer <token>`) |
| **Format** | Any random string ≥ 32 chars, alphanumeric |
| **Generate** | `openssl rand -hex 32` |
| **Example** | `ANKI_MCP_TOKEN=2b7a5e...c9d4` (64 hex chars) |

Generate it once, paste it into `.env`, and use the same value in your MCP client config:

```json
{
  "mcpServers": {
    "anki": {
      "url": "https://anki.example.com/mcp",
      "headers": { "Authorization": "Bearer <ANKI_MCP_TOKEN>" }
    }
  }
}
```

## Hardcoded in `compose.yaml` (not user-configurable)

### `SYNC_BASE`

| | |
|---|---|
| **Used by** | `mcp` service |
| **Value** | `http://anki-sync:8080/sync` |
| **Purpose** | URL the MCP process could theoretically use to talk to the sync server. Not used by current tools; reserved for future round-trip operations. |

### `ANKI_MCP_HOST`

| | |
|---|---|
| **Used by** | `mcp` service (consumed by FastMCP at startup) |
| **Value** | `0.0.0.0` |
| **Purpose** | Bind address for the FastMCP HTTP server |

### `ANKI_MCP_PORT`

| | |
|---|---|
| **Used by** | `mcp` service (consumed by FastMCP at startup) |
| **Value** | `8765` |
| **Purpose** | Port the FastMCP HTTP server listens on (the host-side port is mapped 1:1) |

### `FASTANKI_DIR`

| | |
|---|---|
| **Used by** | `mcp` service → `fastanki.collection.data_dir()` |
| **Value** | `/sync/matthias` |
| **Purpose** | Override for fastanki's collection path. fastanki defaults to `~/Library/Application Support/Anki2/<profile>` on macOS or `~/.local/share/Anki2/<profile>` on Linux. Setting `FASTANKI_DIR=/sync/matthias` tells fastanki to read and write `/sync/matthias/collection.anki2` — the same file the `anki-sync` container exposes. |
| **Why it matters** | This is the mechanism that lets MCP tools and the Anki sync server share one collection. Without it, MCP would create a fresh empty collection on every start. |

## Volumes

`compose.yaml` declares one named volume:

| Volume | Mounted at | Used by |
|---|---|---|
| `anki-sync:/sync` | `/sync` (both services) | holds `<profile>/collection.anki2` + media |

The `<profile>` directory inside the volume is fixed by the username in `SYNC_USER1`. With `SYNC_USER1=matthias` the collection lives at `/sync/matthias/collection.anki2` inside both containers, which is why `FASTANKI_DIR` is set to `/sync/matthias`.

To inspect the volume from the host:

```bash
docker compose exec anki-sync ls /sync/matthias
# collection.anki2  collection.anki2-shm  collection.anki2-wal  media
```

## Ports

| Service | Host port | Container port | Protocol |
|---|---|---|---|
| `anki-sync` | `8081` | `8080` | HTTP (Anki sync protocol) |
| `mcp` | `8765` | `8765` | HTTP (streamable-http MCP) |

The sync port is a plain HTTP endpoint serving Anki's binary sync protocol — not a REST API. Do not expose it to the public internet; the MCP port is the only public-facing service.

## Healthcheck

The MCP container runs a TCP-only healthcheck on port `8765`. It does not require auth and adds no traffic — it just confirms the listener is open.
