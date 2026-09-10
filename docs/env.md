# Environment variables

This document lists every environment variable used by the stack. The ones
that hold secrets live in `.env` and are loaded by the `mcp` service's
`env_file`. Everything else is hardcoded in `compose.yaml` and not
user-configurable.

Copy `.env.example` to `.env` and fill in the values.

## Secrets (in `.env`)

### `SYNC_USER1`

| | |
|---|---|
| **Used by** | `anki-sync` service |
| **Purpose** | Anki username whose collection is exposed by the sync server |
| **Format** | `<username>:<password>` (or `<username>:<phc-hash>` if `PASSWORDS_HASHED=1`) |
| **Where the value comes from** | Your Anki Desktop account — File → Switch Profile, or the username shown in the account dialog |
| **Example** | `SYNC_USER1=alice:s3cret` |

### `SYNC_USER1_NAME`

| | |
|---|---|
| **Used by** | `mcp` service → sets `FASTANKI_DIR` |
| **Purpose** | Username part of `SYNC_USER1`, needed separately because compose interpolation does not support `${VAR%%:*}` substring expansion |
| **Format** | Same as the username portion of `SYNC_USER1` |
| **Example** | `SYNC_USER1_NAME=alice` |

### `ANKI_MCP_TOKEN`

| | |
|---|---|
| **Used by** | `mcp` service (consumed by FastMCP at startup, validated by the entrypoint) |
| **Purpose** | Bearer token MCP clients must send on every request (`Authorization: Bearer <ANKI_MCP_TOKEN>`) |
| **Format** | Any random string ≥ 32 chars, alphanumeric |
| **Where the value comes from** | Generate it yourself — e.g. `openssl rand -hex 32`. Treat it like a password. |
| **Example** | `ANKI_MCP_TOKEN=9f3a1c8b2e5d7f0a4c6b8d0e2f4a6c8e...` |

Use the same value in your MCP client config:

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

### `ANKI_MCP_HOST`
- **Value:** `0.0.0.0`
- **Purpose:** Bind address for the FastMCP HTTP server.

### `ANKI_MCP_PORT`
- **Value:** `8765`
- **Purpose:** Port the FastMCP HTTP server listens on (the host-side port is mapped 1:1).

### `FASTANKI_DIR`
- **Value:** `/sync/${SYNC_USER1_NAME}`
- **Purpose:** Override for fastanki's collection path. fastanki defaults to `~/Library/Application Support/Anki2/<profile>` on macOS or `~/.local/share/Anki2/<profile>` on Linux. Setting `FASTANKI_DIR=/sync/<username>` tells fastanki to read and write `/sync/<username>/collection.anki2` — the same file the `anki-sync` container exposes.
- **Why it matters:** This is the mechanism that lets MCP tools and the Anki sync server share one collection. Without it, MCP would create a fresh empty collection on every start.

## Volumes

`compose.yaml` declares two named volumes:

| Volume | Mounted at | Used by | Purpose |
|---|---|---|---|
| `anki-sync` | `/sync` | both services | holds `<profile>/collection.anki2` + media |
| `anki-mcp-data` | `/data` | `mcp` | reserved (not currently used; declared for future local-cache needs) |

The `<profile>` directory inside the volume is fixed by the username in `SYNC_USER1`. With `SYNC_USER1=alice:...` the collection lives at `/sync/alice/collection.anki2` inside both containers, which is why `FASTANKI_DIR` is set to `/sync/alice`.

To inspect the volume from the host:

```bash
docker compose exec anki-sync ls /sync/<username>
# collection.anki2  collection.anki2-shm  collection.anki2-wal  media
```

## Ports

| Service | Host port | Container port | Protocol |
|---|---|---|---|
| `anki-sync` | `8081` | `8080` | HTTP (Anki sync protocol) |
| `mcp` | `8765` | `8765` | HTTP (streamable-http MCP) |

The sync port is a plain HTTP endpoint serving Anki's binary sync protocol — not a REST API. Do not expose it to the public internet; the MCP port is the only public-facing service.

## Healthcheck

The MCP container runs a TCP-only healthcheck on port `8765`. It does not require auth and adds no traffic — it just confirms the listener is open. (A 401 response is also a "server is up" signal, so the check accepts any 1xx–4xx status code as healthy.)