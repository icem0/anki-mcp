---
name: Sync-Conflict report
about: Report a sync conflict between anki-mcp, anki-sync and Anki-Desktop
title: "[SYNC-CONFLICT] "
labels: ["sync-conflict", "data-safety"]
---

## Symptom

- Anki-Desktop and anki-sync server disagree on `collection.anki2`
- Sync server creates a "branch" copy at `/sync/<user>#<hash>/collection.anki2`
  (the hash is the host-key suffix)
- One side (desktop or server) silently overwrites the other on the next
  `full_download` / `full_upload` (fastanki `sync()` lines 489–510)
- No backup of the pre-overwrite state. No user prompt. No MCP-level warning.

## Repro

1. anki-mcp `add_card` runs against `/sync/matthias/collection.anki2`
2. Anki-Desktop on the user's PC modifies the same DB locally
3. Anki-Desktop clicks "Sync" → fastanki `sync()` enters
   `FullSyncRequired` branch → calls `full_download` or `full_upload`
4. Server saves the diverged state as a "branch" copy under
   `/sync/matthias#<hkey-suffix>/` and overwrites the live DB
5. The user (and any in-flight MCP tools) see one side of the data disappear
   with no audit trail

## Real-world evidence

| DB | Path | Bytes | Notes | Cards |
|---|---|---|---|---|
| Live | `/sync/matthias/collection.anki2` | 204,800 | Currently being written | 95 |
| Branch | `/sync/matthias#IGAnBrUfIZtMXPM0FndyAiRIDt09/collection.anki2` | 126,976 | Pre-conflict snapshot, only "Default" | 0 |

Live DB has 3 decks (`Default`, `Rechnungswesen`, `MPA`) and 95 cards / 222
revlog entries (last review 2026-09-10 05:50 UTC). Branch DB has only the
empty `Default` deck — it represents the server state BEFORE the desktop
pushed the new decks. The two sides diverged, the server protected itself by
forking, and the user has no idea.

## Three writers, no lock, no backup

```
Windows Desktop Anki  ──┐
                         ├──►  /sync/matthias/collection.anki2
anki-mcp container  ─────┤
                         │
anki-sync container ─────┘
```

All three writers can read/write the same SQLite file concurrently. SQLite
WAL mode allows concurrent readers and one writer, but only IF writers
take the WAL lock; the desktop and MCP both close cleanly. The problem is
not the lock — the problem is **the conflict is detected only at the sync
protocol level (server vs. desktop), and the MCP container is not part of
that protocol**: MCP just writes rows, and the next desktop-sync discovers
the divergence.

## Why the MCP makes it worse

The MCP container (`add_card`, `update_note`, `delete_note`, etc.) writes
directly to the SQLite DB. The Anki-Desktop client and the anki-sync server
both run their own merge logic via the AnkiWeb sync protocol. The MCP is
invisible to that protocol — it doesn't bump `col.scm`, doesn't notify
either side, and doesn't take a backup. So:

- Desktop: scm = N
- Server: scm = N
- MCP writes a new note, server: scm = N, usn unchanged
- Desktop syncs → server says "no changes" (col.mod is what it cares about,
  and MCP didn't update col.mod) → desktop pull gets the new note

That works UNTIL the desktop and server both write in the gap between
desktop's "meta" query and desktop's "start" query. Then the server's
fastanki `sync()` raises `FullSyncRequired`, the server picks a side, and
the `#hash` branch is born.

## Expected behaviour

1. The MCP should refuse to write if `col.scm` differs from the value the
   MCP has cached. The error message should tell the operator to run
   `check_sync_state` first.
2. Every MCP write that touches the live DB should first copy
   `/sync/<user>/collection.anki2` to a timestamped backup
   (`/sync/<user>/backups/collection-<utc-iso>.anki2`).
3. A new tool `check_sync_state` should report the live DB's
   `scm` / `mod` / `usn` / card-count vs. the last-known-good snapshot, so
   the operator can detect drift BEFORE the user clicks "Sync" on the
   desktop.
4. The fastanki `sync()` `FullSyncRequired` branch should refuse to do a
   blind `full_download` / `full_upload`; it should at least print a
   warning. (Upstream — we can patch the vendored copy until upstream
   fixes it.)
5. Anki-Desktop's "Änderungen in eine Richtung erzwingen" must be enabled
   to break the conflict dialog loop, but the MCP needs to make sure both
   sides are in sync first.

## Proposed fix scope

- [ ] `src/fastanki/syncer.py`: in `sync_collection`, before
      `full_download` / `full_upload`, write a backup copy of the
      current `self.path` to a sibling `backups/` directory with a
      timestamped name. Print a one-line warning to stderr.
- [ ] `src/fastanki/syncer.py`: in `sync()`, when `FullSyncRequired`
      fires, raise a typed exception that lets the caller (e.g. the MCP
      server or a CLI wrapper) decide which side to keep, instead of
      doing it automatically.
- [ ] `server.py`: add `check_sync_state` tool. Returns
      `{scm, mod, usn, cards, notes, revlog, mtime_iso, has_branch,
      branch_paths}`. Reads `/sync/<user>/` for sibling directories
      matching the `#hash` pattern.
- [ ] `server.py`: add `create_backup` tool. Copies the live
      collection.anki2 to a timestamped backup file.
- [ ] `server.py`: every write tool (`add_card`, `update_note`,
      `delete_note`, `create_deck`, `delete_deck`, `add_media`) MUST
      call `create_backup` first, unless the caller passes
      `skip_backup=True` (off by default).
- [ ] `src/fastanki/core.py` (or a new `src/fastanki/sync_guard.py`):
      cache `col.scm` after each write. On the next write, if
      `current_scm != cached_scm`, refuse the write with a
      `SyncStateDrift` error pointing the user at `check_sync_state`.
- [ ] `README.md`: document the three-writer problem and the new
      `check_sync_state` / `create_backup` workflow. Document that
      Anki-Desktop's "Änderungen in eine Richtung erzwingen" should be
      enabled once both sides are reconciled.
- [ ] `flashcard` skill: add a "PRE-SYNC" gate that calls
      `check_sync_state` before any `add_card` run.

## Priority

**P1 — data loss risk.** A user (or agent) running MCP-side writes while
the desktop is open can lose local-only decks/cards with no warning and
no recovery path beyond reading the `#hash` branch manually.

## Workaround until the fix lands

1. Stop anki-sync container before MCP writes
   (`docker stop anki-sync`).
2. Before re-enabling sync, run
   `sqlite3 /sync/matthias/collection.anki2 "select mod, scm from col"`
   to see the last write time.
3. If Anki-Desktop has un-synced changes, click "Sync" on the desktop
   FIRST, accept the conflict dialog (Upload: Desktop wins), THEN start
   MCP writes.
4. Never run MCP writes while the desktop is open.
