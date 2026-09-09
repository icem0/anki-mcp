# anki-mcp

MCP server for headless Anki card management, 24/7. No Anki Desktop required.

LLM agents connect via MCP (Model Context Protocol) → `list_decks`, `add_card`, `update_note`, `delete_note`, `find_notes`, `get_note`, `add_media`.

Built on top of [AnswerDotAI/fastanki](https://github.com/AnswerDotAI/fastanki) (vendored under `src/fastanki/`).

## Architecture

```
┌──────────────┐                                  ┌────────────────┐
│  LLM Agent   │ ◀── MCP/streamable-http ───────▶ │   anki-mcp     │
│  (Cline,     │   Bearer auth via Infisical      │   :8765        │
│   Claude,    │                                  │   FastMCP      │
│   Hermes)    │                                  └────────┬───────┘
└──────────────┘                                           │
                                                vendored  │
                                                           ▼
                                                   ┌────────────────┐
                                                   │ fastanki core  │
                                                   │ (collection.py)│
                                                   └────────┬───────┘
                                                            ▼
                                                   ┌────────────────┐
                                                   │ collection.anki2│
                                                   │ (SQLite /data) │
                                                   └────────────────┘
```

Separate from `anki-sync` (port 8081) — `anki-mcp` owns its own collection. Optional periodic sync via `ANKI_SYNC_URL` env.

## MCP Tools

| Tool | Purpose |
|---|---|
| `list_decks` | All deck names |
| `add_card(deck, fields, model="Basic", tags=None)` | Create a card, returns note id |
| `add_cloze(text, deck, back_extra="", tags=None)` | Cloze card |
| `find_notes(deck?, tag?, fields?)` | Search notes |
| `get_note(note_id)` | Read one note |
| `update_note(note_id, fields?, tags?, add_tags?)` | Edit fields/tags |
| `delete_note(note_id)` | Delete note + its cards |
| `add_media(path, fname=None)` | Import a media file |

## Deploy (Arcane)

```bash
# 1. Secret setzen
infisical secrets set anki-mcp-token=<random-32+chars>

# 2. compose.yaml in Arcane hochladen
# 3. Container startet auf :8765
```

## Local test

```bash
pip install -r requirements.txt
ANKI_COLLECTION_PATH=./test.anki2 ANKI_MCP_PORT=8765 python server.py
# → MCP-server on http://localhost:8765/mcp
```

## License

Apache-2.0 (inherited from upstream `fastanki`).
