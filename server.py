"""anki-mcp — FastMCP server wrapping fastanki.

Tools exposed:
- list_decks
- add_card (Basic / Cloze via fastanki)
- find_notes (search by deck / tag / field)
- get_note (read one)
- update_note (edit fields + tags)
- delete_note
- add_media

Transport: streamable-http, port from ANKI_MCP_PORT (default 8765).
Auth: Bearer ANKI_MCP_TOKEN (set via Infisical).
"""

from __future__ import annotations
import os, sys
from pathlib import Path

# ensure local fastanki (src/fastanki) is importable when run from repo root
sys.path.insert(0, str(Path(__file__).parent / "src"))

from fastmcp import FastMCP        # type: ignore
from fastanki import core as fk    # type: ignore

mcp = FastMCP(name="anki-mcp")

# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_decks() -> list[str]:
    """Return all deck names in the collection (sorted)."""
    from fastanki.collection import Collection  # type: ignore
    with Collection.open() as col:
        # fastanki: col.decks() is a method that returns the list directly
        return sorted(col.decks())

@mcp.tool()
def add_card(
    deck: str,
    fields: dict[str, str],
    model: str = "Basic",
    tags: str | None = None,
) -> int:
    """Create a card. Returns the new note id.
    Args:
        deck: deck name (created if missing). Use "::" for nesting.
        fields: {field_name: value}, e.g. {"Front": "q", "Back": "a"}.
        model: notetype name (default "Basic", also "Cloze").
        tags: space-separated tags, optional.
    """
    return fk.add_card(model=model, deck=deck, tags=tags, fields=fields)

@mcp.tool()
def add_cloze(text: str, deck: str = "Default", back_extra: str = "", tags: str | None = None) -> int:
    """Add a Cloze card. `text` uses {{c1::hidden}} syntax."""
    return fk.add_cloze_card(text=text, back_extra=back_extra, deck=deck, tags=tags)

@mcp.tool()
def find_notes(
    deck: str | None = None,
    tag: str | None = None,
    fields: dict[str, str] | None = None,
) -> list[dict]:
    """Find notes by deck / tag / field-substring. Returns [{id, tags, fields}].

    `deck` is forwarded to fastanki for filtering, but is NOT returned per-note
    (Anki 25's Note model has no .deck attribute — deck lives on the card).
    """
    notes = fk.find_notes(deck=deck, tag=tag, fields=fields)
    return [
        {"id": n.id, "tags": list(n.tags), "fields": dict(n.fields.items())}
        for n in notes
    ]

@mcp.tool()
def get_note(note_id: int) -> dict:
    """Read a single note by id. Note has no deck attribute (Anki 25)."""
    n = fk.get_note(note_id)
    return {"id": n.id, "tags": list(n.tags), "fields": dict(n.fields.items())}

@mcp.tool()
def update_note(
    note_id: int,
    fields: dict[str, str] | None = None,
    tags: str | None = None,
    add_tags: str | None = None,
) -> dict:
    """Update a note's fields and/or tags. `tags` REPLACES, `add_tags` APPENDS."""
    fk.update_note(note_id, tags=tags, add_tags=add_tags, **(fields or {}))
    return {"ok": True, "id": note_id}

@mcp.tool()
def delete_note(note_id: int) -> dict:
    """Delete a note (and all its cards) by id."""
    fk.del_note(note_id)
    return {"ok": True, "id": note_id}

@mcp.tool()
def add_media(path: str, fname: str | None = None) -> str:
    """Copy a local file into the collection's media folder. Returns the stored filename."""
    return fk.add_media(path=path, fname=fname)

# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("ANKI_MCP_PORT", "8765"))
    host = os.environ.get("ANKI_MCP_HOST", "0.0.0.0")
    # streamable-http transport — works with most modern MCP clients (incl. Claude Desktop, Cline, Hermes MCP)
    mcp.run(transport="streamable-http", host=host, port=port, log_level="info")
