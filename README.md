# anki-mcp

MCP server for headless Anki card management. Runs as a sidecar to a sync server (e.g. `anki-sync`) and writes directly to the same SQLite collection (`collection.anki2`) that the Anki Desktop client syncs.

LLM agents connect via the [Model Context Protocol](https://modelcontextprotocol.io) (streamable-http) and call one of 10 tools: `list_decks`, `create_deck`, `delete_deck`, `add_card`, `add_cloze`, `find_notes`, `get_note`, `update_note`, `delete_note`, `add_media`.

Built on top of [AnswerDotAI/fastanki](https://github.com/AnswerDotAI/fastanki) (vendored under `src/fastanki/`, version 3.0).

## Prerequisites

- Docker Engine 24+ with Compose v2 (`docker compose` plugin)
- An existing Anki Desktop profile whose `collection.anki2` you want to manage
- (Optional) an [Anki sync server](https://docs.ankiweb.net/sync-server.html) — `anki-mcp` is designed to share its collection with one, but it can also run standalone

## Quick start

```bash
git clone https://github.com/icem0/anki-mcp.git
cd anki-mcp

# 1. Configure secrets
cp .env.example .env
$EDITOR .env   # set SYNC_USER1 and ANKI_MCP_TOKEN

# 2. Build and start
docker compose up -d --build

# 3. Verify the MCP endpoint responds
curl -s -X POST http://localhost:8765/mcp \
  -H "Authorization: Bearer $(grep ^ANKI_MCP_TOKEN .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
```

The first `list_decks` call will return what is currently in `collection.anki2` at the path pointed to by `FASTANKI_DIR`.

## Repo layout

```
anki-mcp/
├── server.py            # FastMCP server, 10 tools
├── src/fastanki/        # vendored fastanki 3.0 (added to sys.path at startup)
├── Dockerfile           # python:3.12-slim + requirements
├── compose.yaml         # 2-service stack: anki-sync + mcp, shared collection volume
├── requirements.txt
├── pyproject.toml       # fastanki metadata
├── .env.example
├── docs/
│   ├── env.md           # complete env-var reference
│   ├── architecture.md  # how the two services share one collection
│   └── deployment.md    # standard docker compose workflow
└── .gitignore
```

## MCP tools

| Tool | Args | Returns |
|---|---|---|
| `list_decks` | — | `["Default", "MPA", ...]` (sorted) |
| `create_deck(name)` | deck name (use `::` for nesting) | `{name, id}` — idempotent, no error if exists |
| `delete_deck(name)` | deck name | `{name, removed}` — deletes deck + subdecks + cards |
| `add_card(deck, fields, model="Basic", tags=None)` | deck name, `{Field: value}`, notetype, space-separated tags | note id |
| `add_cloze(text, deck="Default", back_extra="", tags=None)` | cloze text with `{{c1::hidden}}` | note id |
| `find_notes(deck?, tag?, fields?)` | all optional, AND-joined | `[{id, tags, fields}]` |
| `get_note(note_id)` | note id | `{id, tags, fields}` |
| `update_note(note_id, fields?, tags?, add_tags?)` | id + mutators | `{ok, id}` — `tags` REPLACES, `add_tags` APPENDS |
| `delete_note(note_id)` | note id | `{ok, id}` — deletes note + its cards |
| `add_media(path, fname=None)` | absolute file path, optional rename | stored filename |

## Environment variables

See [docs/env.md](docs/env.md) for the complete reference. Two values are secrets, the rest are hardcoded in `compose.yaml`.

## Architecture

See [docs/architecture.md](docs/architecture.md) for how `anki-sync` and `anki-mcp` share one SQLite collection via a Docker named volume and the `FASTANKI_DIR` environment variable.

## Deployment

See [docs/deployment.md](docs/deployment.md) for the standard `docker compose` workflow (build, start, update, inspect).

## License

Apache-2.0 (inherited from upstream `fastanki`).
