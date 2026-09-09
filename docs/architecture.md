# Architecture

`anki-mcp` is a single-container MCP server. The reference deployment pairs it with an [Anki sync server](https://docs.ankiweb.net/sync-server.html) (`anki-sync`) so both services share one SQLite collection. The mechanism is one Docker named volume plus the `FASTANKI_DIR` environment variable.

## Container diagram

```
docker compose stack "anki-sync"
─────────────────────────────────────────────────────────────────
┌─────────────────────────────┐  ┌─────────────────────────────┐
│  service: anki-sync         │  │  service: mcp               │
│  image: python:3.12-slim    │  │  image: anki-mcp:local      │
│                             │  │                             │
│  listens on :8080           │  │  listens on :8765           │
│  (Anki binary sync protocol)│  │  (streamable-http MCP)      │
│                             │  │                             │
│  reads/writes               │  │  reads/writes               │
│   /sync/matthias/           │  │   /sync/matthias/           │
│     collection.anki2        │  │     collection.anki2        │
└──────────────┬──────────────┘  └──────────────┬──────────────┘
               │                                │
               └─────────┬──────────────────────┘
                         │
                  ┌──────▼──────┐
                  │ volume:     │
                  │ anki-sync   │
                  │   /sync/    │
                  │     matthias/│
                  │       collection.anki2
                  │       collection.anki2-wal
                  │       collection.anki2-shm
                  │       media/
                  └─────────────┘
```

## How the two services share one collection

1. `compose.yaml` declares one named volume `anki-sync` and mounts it at `/sync` in both services.
2. The Anki sync server creates `<volume>/<username>/` (where `<username>` is `SYNC_USER1` from `.env`). For `SYNC_USER1=matthias` that is `/sync/matthias/`.
3. `anki-mcp` sets `FASTANKI_DIR=/sync/matthias` so that fastanki's `Collection.open()` opens `/sync/matthias/collection.anki2`.
4. Both services see the same file. SQLite's WAL mode serialises writes correctly between them, and SQLite acquires an exclusive lock on the DB during writes, so MCP writes are atomic w.r.t. the sync server.

## Concurrency model

SQLite allows multiple readers but only one writer. fastanki uses a connection that holds an exclusive write lock for the duration of a `Collection.open()` block, so the MCP tool call that mutates the collection is atomic from the sync server's point of view.

In practice, this means:

- **MCP writes are safe** while the sync server is running. The MCP tool call holds the lock for the duration of the write; the sync server waits.
- **Direct SQL inspection from outside the stack** (e.g. `sqlite3` on the host) must be done while both services are stopped, otherwise SQLite returns `database is locked` (WAL mode, busy timeout < 1 s).
- **Anki Desktop** syncs atomically via the sync server's protocol and is similarly safe.

## FastMCP transport

The MCP server uses [streamable-http](https://modelcontextprotocol.io/specification/2024-11-05/basic/transports#streamable-http) on `/mcp`. The initialization flow is:

```
POST /mcp
Authorization: Bearer <ANKI_MCP_TOKEN>
Content-Type: application/json
Accept: application/json, text/event-stream

{"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
```

The server returns a session id in the `Mcp-Session-Id` response header. Subsequent `tools/call` requests include this header, plus a per-tool JSON payload.

## Tool list (current: 10)

| Tool | Source-of-truth reads | Writes |
|---|---|---|
| `list_decks` | `col.decks()` | — |
| `create_deck` | — | `col.deck_id(name, create=True)` |
| `delete_deck` | — | `col.remove_deck(did)` |
| `add_card` | — | `col.add_note({...})` |
| `add_cloze` | — | `col.add_note({...})` (Cloze model) |
| `find_notes` | `col.find_notes(query)` | — |
| `get_note` | `col.get_note(nid)` | — |
| `update_note` | — | `col.update_note(...)` |
| `delete_note` | — | `col.remove_notes([nid])` |
| `add_media` | — | `col.media.add_file(...)` |

Each tool opens the collection on entry and closes it on exit. The collection is never held across tool calls.
