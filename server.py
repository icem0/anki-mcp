"""anki-mcp — FastMCP server wrapping fastanki.

Tools exposed:
- list_decks
- create_deck   (NEW)
- delete_deck   (NEW)
- add_card (Basic / Cloze via fastanki)
- find_notes (search by deck / tag / field)
- get_note (read one)
- update_note (edit fields + tags)
- delete_note
- add_media

Transport: streamable-http, port from ANKI_MCP_PORT (default 8765).
Auth: Bearer ANKI_MCP_TOKEN (set via Infisical).

Collection location: fastanki uses $FASTANKI_DIR/collection.anki2 (default ~/.fastanki).
In the anki-sync stack, FASTANKI_DIR=/sync/matthias so the MCP operates on the same
SQLite DB the Anki Desktop client syncs. The anki-sync container MUST be stopped
while MCP writes (exclusive lock).
"""

from __future__ import annotations
import os, sys
from pathlib import Path

# ensure local fastanki (src/fastanki) is importable when run from repo root
sys.path.insert(0, str(Path(__file__).parent / "src"))

from fastmcp import FastMCP        # type: ignore
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier  # type: ignore
from fastanki import core as fk    # type: ignore

# Auth: ANKI_MCP_TOKEN from env (set via compose env_file / Infisical).
# If the var is missing, refuse to start — no anonymous access.
_token = os.environ.get("ANKI_MCP_TOKEN", "").strip()
if not _token:
    sys.stderr.write(
        "FATAL: ANKI_MCP_TOKEN is not set. Refusing to start without auth.\n"
    )
    sys.exit(1)

_auth = StaticTokenVerifier(
    tokens={
        _token: {
            "client_id": "anki-mcp-operator",
            "scopes": ["mcp:tools"],
        }
    },
    required_scopes=["mcp:tools"],
)

mcp = FastMCP(name="anki-mcp", auth=_auth)

# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_decks() -> list[str]:
    """Return all deck names in the collection (sorted)."""
    from fastanki.collection import Collection  # type: ignore
    with Collection.open() as col:
        return sorted(col.decks())


@mcp.tool()
def check_sync_state() -> dict:
    """Inspect the live collection's sync state vs. the last MCP write.

    Returns:
        {
            "current":     {scm, usn, mod_ms, mtime_iso, card_count, note_count, revlog_count, db_bytes},
            "remembered":  same shape, or None if we never wrote,
            "drift":       diff between current and remembered (scm/mod_ms moved?),
            "branches":    list of /sync/<user>#<hash>/collection.anki2 paths,
            "branch_count": int,
            "db_path":     absolute path
        }

    Use this to detect that the desktop or anki-sync container has
    written in between MCP calls (drift.scm or drift.mod_ms is set), or
    that a sync conflict was logged (branches is non-empty).
    """
    from fastanki.sync_guard import check_sync_state as _check  # type: ignore
    return _check()


@mcp.tool()
def create_backup(reason: str = "") -> dict:
    """Copy the live collection.anki2 to backups/collection-<utc-iso>.anki2.

    Returns {"path": "...", "bytes": int, "reason": "..."}.
    """
    from fastanki.sync_guard import create_backup as _bk  # type: ignore
    p = _bk(reason=reason)
    return {"path": str(p), "bytes": p.stat().st_size, "reason": reason}


def _safe_write(skip_backup: bool, reason: str, fn, *args, **kwargs):
    """Run a fastanki write under the sync-guard.

    - Backups the live DB first (unless skip_backup=True).
    - Refuses if the DB's scm drifted from our last snapshot, or if a
      new /sync/<user>#*/ branch appeared.
    - Re-snapshots the state after the write succeeds.
    """
    from fastanki.sync_guard import (  # type: ignore
        pre_write_check,
        remember_sync_state,
        SyncStateDrift,
    )

    try:
        pre_write_check(skip_backup=skip_backup, reason=reason)
    except SyncStateDrift as e:
        # re-raise with a hint — MCP clients will see the JSON-formatted message
        raise RuntimeError(
            f"refused write: {e}. Run check_sync_state to see the drift, "
            f"reconcile, and re-run."
        ) from e
    result = fn(*args, **kwargs)
    remember_sync_state()
    return result


@mcp.tool()
def create_deck(name: str, skip_backup: bool = False) -> dict:
    """Create a deck (and any missing parent decks) by name. Use "::" for nesting.

    Returns {"name": ..., "id": <deck_id>}. If the deck already exists, returns
    its id without re-creating.

    Refuses to write if the collection drifted since the last MCP call
    (run `check_sync_state` first) unless `skip_backup=True` is passed.
    """
    from fastanki.collection import Collection  # type: ignore
    from fastanki.sync_guard import (  # type: ignore
        pre_write_check,
        remember_sync_state,
        SyncStateDrift,
    )

    try:
        pre_write_check(skip_backup=skip_backup, reason="create_deck")
    except SyncStateDrift as e:
        raise RuntimeError(
            f"refused write: {e}. Run check_sync_state to see the drift, "
            f"reconcile, and re-run."
        ) from e
    with Collection.open() as col:
        did = col.deck_id(name, create=True)
    remember_sync_state()
    return {"name": name, "id": did}


@mcp.tool()
def delete_deck(name: str, skip_backup: bool = False) -> dict:
    """Delete a deck and all its subdecks (with their cards/notes). Idempotent.

    Refuses to write if the collection drifted since the last MCP call
    (run `check_sync_state` first) unless `skip_backup=True` is passed.
    """
    from fastanki.collection import Collection  # type: ignore
    from fastanki.sync_guard import (  # type: ignore
        pre_write_check,
        remember_sync_state,
        SyncStateDrift,
    )

    try:
        pre_write_check(skip_backup=skip_backup, reason=f"delete_deck:{name}")
    except SyncStateDrift as e:
        raise RuntimeError(
            f"refused write: {e}. Run check_sync_state to see the drift, "
            f"reconcile, and re-run."
        ) from e
    with Collection.open() as col:
        removed = col.remove_deck(name)
    remember_sync_state()
    return {"name": name, "removed": removed}


@mcp.tool()
def add_card(
    deck: str,
    fields: dict[str, str],
    model: str = "Basic",
    tags: str | None = None,
    skip_backup: bool = False,
) -> int:
    """Create a card. Returns the new note id.

    Args:
        deck: deck name (created if missing). Use "::" for nesting.
        fields: {field_name: value}, e.g. {"Front": "q", "Back": "a"}.
        model: notetype name (default "Basic", also "Cloze").
        tags: space-separated tags, optional.
        skip_backup: set to True to skip the pre-write backup (NOT
            recommended; the backup lets you recover from a wrong sync
            choice on the desktop).

    Refuses to write if the collection drifted since the last MCP call
    (run `check_sync_state` first) unless `skip_backup=True` is passed.
    """
    from fastanki.sync_guard import (  # type: ignore
        pre_write_check,
        remember_sync_state,
        SyncStateDrift,
    )
    try:
        pre_write_check(skip_backup=skip_backup, reason=f"add_card:{deck}")
    except SyncStateDrift as e:
        raise RuntimeError(
            f"refused write: {e}. Run check_sync_state to see the drift, "
            f"reconcile, and re-run."
        ) from e
    nid = fk.add_card(model=model, deck=deck, tags=tags, fields=fields)
    remember_sync_state()
    return nid


@mcp.tool()
def add_cloze(
    text: str,
    deck: str = "Default",
    back_extra: str = "",
    tags: str | None = None,
    skip_backup: bool = False,
) -> int:
    """Add a Cloze card. `text` uses {{c1::hidden}} syntax.

    Refuses to write if the collection drifted since the last MCP call.
    """
    from fastanki.sync_guard import (  # type: ignore
        pre_write_check,
        remember_sync_state,
        SyncStateDrift,
    )
    try:
        pre_write_check(skip_backup=skip_backup, reason=f"add_cloze:{deck}")
    except SyncStateDrift as e:
        raise RuntimeError(
            f"refused write: {e}. Run check_sync_state to see the drift, "
            f"reconcile, and re-run."
        ) from e
    nid = fk.add_cloze_card(text=text, back_extra=back_extra, deck=deck, tags=tags)
    remember_sync_state()
    return nid


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
    skip_backup: bool = False,
) -> dict:
    """Update a note's fields and/or tags. `tags` REPLACES, `add_tags` APPENDS.

    Refuses to write if the collection drifted since the last MCP call.
    """
    from fastanki.sync_guard import (  # type: ignore
        pre_write_check,
        remember_sync_state,
        SyncStateDrift,
    )
    try:
        pre_write_check(skip_backup=skip_backup, reason=f"update_note:{note_id}")
    except SyncStateDrift as e:
        raise RuntimeError(
            f"refused write: {e}. Run check_sync_state to see the drift, "
            f"reconcile, and re-run."
        ) from e
    fk.update_note(note_id, tags=tags, add_tags=add_tags, **(fields or {}))
    remember_sync_state()
    return {"ok": True, "id": note_id}


@mcp.tool()
def delete_note(note_id: int, skip_backup: bool = False) -> dict:
    """Delete a note (and all its cards) by id.

    Refuses to write if the collection drifted since the last MCP call.
    """
    from fastanki.sync_guard import (  # type: ignore
        pre_write_check,
        remember_sync_state,
        SyncStateDrift,
    )
    try:
        pre_write_check(skip_backup=skip_backup, reason=f"delete_note:{note_id}")
    except SyncStateDrift as e:
        raise RuntimeError(
            f"refused write: {e}. Run check_sync_state to see the drift, "
            f"reconcile, and re-run."
        ) from e
    fk.del_note(note_id)
    remember_sync_state()
    return {"ok": True, "id": note_id}


@mcp.tool()
def add_media(path: str, fname: str | None = None, skip_backup: bool = False) -> str:
    """Copy a local file into the collection's media folder. Returns the stored filename.

    Refuses to write if the collection drifted since the last MCP call.
    """
    from fastanki.sync_guard import (  # type: ignore
        pre_write_check,
        remember_sync_state,
        SyncStateDrift,
    )
    try:
        pre_write_check(skip_backup=skip_backup, reason="add_media")
    except SyncStateDrift as e:
        raise RuntimeError(
            f"refused write: {e}. Run check_sync_state to see the drift, "
            f"reconcile, and re-run."
        ) from e
    out = fk.add_media(path=path, fname=fname)
    remember_sync_state()
    return out


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("ANKI_MCP_PORT", "8765"))
    host = os.environ.get("ANKI_MCP_HOST", "0.0.0.0")
    # streamable-http transport — works with most modern MCP clients (incl. Claude Desktop, Cline, Hermes MCP)
    mcp.run(transport="streamable-http", host=host, port=port, log_level="info")
