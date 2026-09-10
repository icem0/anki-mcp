# Environment variables

This document lists every environment variable used by the stack. The ones
that hold secrets live in `.env` (and the MCP service's own bearer token lives
in a secret manager, not here). Everything else is hardcoded in
`compose.yaml` and not user-configurable.

Copy `.env.example` to `.env` and fill in the values.

## Secrets (in `.env`)

### `SYNC_USER1`

| | |
|---|---|
| **Used by** | `anki-sync` service |
| **Purpose** | Anki username whose collection is exposed by the sync server |
| **Format** | Plain Anki account username (e.g. `matthias`) |
| **Where the value comes from** | Your Anki Desktop account — File → Switch Profile, or the username shown in the account dialog |
| **Example** | `SYNC_USER1=matthias` |

### `INFISICAL_TOKEN`

| | |
|---|---|
| **Used by** | `mcp` service (consumed by the container entrypoint) |
| **Purpose** | Service-account token that lets the entrypoint fetch `ANKI_MCP_TOKEN` from the secret manager at startup |
| **Format** | Service-account token issued by the secret manager |
| **Where the value comes from** | The secret manager's service-accounts page. Should be a *read-only*, *path-scoped* token. Never use a personal / org-wide token for automation. |
| **Example** | `INFISICAL_TOKEN=st.828ccb…` |

### `INFISICAL_PROJECT_ID`, `INFISICAL_ENV`, `INFISICAL_SECRET_PATH`, `INFISICAL_SITE_URL`

| | |
|---|---|
| **Used by** | `mcp` service → entrypoint → `infisical run` |
| **Purpose** | Locate `ANKI_MCP_TOKEN` inside the secret manager |
| **Defaults** | `prod`, `/anki-mcp`, `https://infisical.sa-ma.online` (override only if your secret manager runs elsewhere) |

## Secret-manager-resident secrets (NOT in `.env`)

### `ANKI_MCP_TOKEN`

| | |
|---|---|
| **Used by** | `mcp` service (consumed by FastMCP at startup) |
| **Purpose** | Bearer token MCP clients must send on every request (`Authorization: Bearer *** |
| **Format** | Any random string ≥ 32 chars, alphanumeric |
| **Where it lives** | The secret manager, in the workspace/environment/path configured by the `INFISICAL_*` variables. Fetched at container start by the entrypoint and injected into the process env. Never written to disk. |
| **How to rotate** | Update the value in the secret manager. Restart the container (`docker compose restart mcp`). The new value takes effect on the next start. |

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
- **Value:** `/sync/${SYNC_USER1}`
- **Purpose:** Override for fastanki's collection path. fastanki defaults to `~/Library/Application Support/Anki2/<profile>` on macOS or `~/.local/share/Anki2/<profile>` on Linux. Setting `FASTANKI_DIR=/sync/matthias` tells fastanki to read and write `/sync/matthias/collection.anki2` — the same file the `anki-sync` container exposes.
- **Why it matters:** This is the mechanism that lets MCP tools and the Anki sync server share one collection. Without it, MCP would create a fresh empty collection on every start.

## Volumes

`compose.yaml` declares two named volumes:

| Volume | Mounted at | Used by | Purpose |
|---|---|---|---|
| `anki-sync` | `/sync` | both services | holds `<profile>/collection.anki2` + media |
| `anki-mcp-data` | `/data` | `mcp` | reserved (not currently used; declared for future local-cache needs) |

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

The MCP container runs a TCP-only healthcheck on port `8765`. It does not require auth and adds no traffic — it just confirms the listener is open. (A 401 response is also a "server is up" signal, so the check accepts any 1xx–4xx status code as healthy.)
