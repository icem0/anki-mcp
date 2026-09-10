"""sync_guard — pre-write checks for the anki-mcp server.

Three writers can touch `/sync/<user>/collection.anki2`:

1. anki-sync container (foosel) — runs the AnkiWeb sync protocol
2. anki-mcp container (this server) — runs the MCP write tools
3. Anki Desktop (Windows) — runs the AnkiWeb sync client

WAL mode allows one writer at a time, but the conflict between them is
detected only at the sync-protocol level (server vs. desktop). The MCP
container is invisible to that protocol: it writes rows, bumps
``col.scm`` locally, and the next desktop-sync discovers the divergence
through the standard AnkiWeb mechanism. The server's response is
``FullSyncRequired`` → blind ``full_download`` / ``full_upload`` (see
``fastanki/syncer.py:489-510``) and a branch copy at
``/sync/<user>#<hash>/collection.anki2``.

This module gives the MCP container a memory and a safety net:

* ``remember_sync_state()`` — call after every write to record the
  collection's last-known ``scm``/``usn``/``mod``/``mtime``.
* ``check_sync_state()`` — read the current state, compare to the last
  remembered state, list any branch copies (``/sync/<user>#*/``).
* ``create_backup()`` — copy ``collection.anki2`` to a timestamped
  sibling under ``backups/``.
* ``pre_write_check()`` — combined gate: backup, then check for drift.
  Raises ``SyncStateDrift`` if the collection's ``scm`` changed since we
  last wrote, OR if a branch copy appeared under ``/sync/`` since then.

The state file is ``<db-stem>.sync-state.json`` next to the DB. Format::

    {
      "scm": 1789021482,
      "usn": 0,
      "mod_ms": 1789021482415,
      "mtime_iso": "2026-09-10T06:24:42Z",
      "card_count": 95,
      "note_count": 95,
      "updated_iso": "2026-09-10T06:24:42Z"
    }

Backups go to ``<db-dir>/backups/collection-YYYYMMDDTHHMMSSZ.anki2``.
Retention is left to the operator; rotate with a cron if it grows.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---- exceptions -----------------------------------------------------------


class SyncGuardError(RuntimeError):
    """Base for sync-guard errors."""


class SyncStateDrift(SyncGuardError):
    """The collection changed between two MCP writes we made.

    Either ``col.scm`` moved (the desktop or anki-sync wrote in between)
    or a new branch copy appeared under ``/sync/<user>#*/`` (a sync
    conflict was logged). The caller should run ``check_sync_state`` to
    see the new state, reconcile, and re-run.
    """


# ---- path helpers ---------------------------------------------------------


def _db_path() -> Path:
    """Path of the live collection.anki2 (from FASTANKI_DIR or default)."""
    from fastanki.collection import data_dir  # type: ignore

    return (data_dir() / "collection.anki2").resolve()


def _state_path() -> Path:
    return _db_path().with_suffix(".sync-state.json")


def _backup_dir() -> Path:
    d = _db_path().parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- core read -----------------------------------------------------------


def _read_state_from_db() -> dict[str, Any]:
    """Read col.scm, col.usn, col.mod, mtime, and table counts from the DB."""
    db = _db_path()
    if not db.exists():
        raise SyncGuardError(f"Collection DB not found: {db}")
    con = sqlite3.connect(str(db))
    try:
        row = con.execute("select id, scm, mod, usn from col").fetchone()
        if not row:
            raise SyncGuardError(f"Collection row missing in {db}")
        cid, scm, mod, usn = row
        notes = con.execute("select count(*) from notes").fetchone()[0]
        cards = con.execute("select count(*) from cards").fetchone()[0]
        revlog = con.execute("select count(*) from revlog").fetchone()[0]
        try:
            mtime = db.stat().st_mtime
        except OSError:
            mtime = 0.0
        return {
            "scm": int(scm),
            "usn": int(usn),
            "mod_ms": int(mod),
            "mtime_iso": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "card_count": int(cards),
            "note_count": int(notes),
            "revlog_count": int(revlog),
            "db_bytes": db.stat().st_size,
        }
    finally:
        con.close()


# ---- core write ----------------------------------------------------------


def remember_sync_state() -> dict[str, Any]:
    """Snapshot the current col state to the state file. Call after every write."""
    state = _read_state_from_db()
    state["updated_iso"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _state_path().write_text(json.dumps(state, indent=2, sort_keys=True))
    return state


def _load_remembered_state() -> dict[str, Any] | None:
    p = _state_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


# ---- backup --------------------------------------------------------------


def create_backup(reason: str = "") -> Path:
    """Copy the live collection.anki2 to a timestamped backup file.

    Returns the backup path. Existing backups with the same timestamp are
    overwritten (we only ever back up once per second per process).
    """
    db = _db_path()
    if not db.exists():
        raise SyncGuardError(f"Collection DB not found: {db}")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{reason}" if reason else ""
    dest = _backup_dir() / f"collection-{ts}{suffix}.anki2"
    # Use copy2 to keep mtime; SQLite WAL means the file might be in a
    # half-written state — sqlite3 doesn't snapshot by default, but
    # callers MUST stop anki-sync before invoking this, and a quiescent
    # MCP write that followed its own _dirty() leaves the DB in a clean
    # state on disk.
    shutil.copy2(db, dest)
    return dest


# ---- branch detection ---------------------------------------------------


_BRANCH_NAME_RE = __import__("re").compile(r"^([^#]+)#([A-Za-z0-9]+)$")


def list_branch_copies() -> list[dict[str, Any]]:
    """Find any ``/sync/<user>#<hash>/`` sibling directories.

    The anki-sync server creates these when ``FullSyncRequired`` fires
    (see ``fastanki/syncer.py:489-510``). Each branch is a snapshot of
    the collection at the time of the conflict — invaluable for
    forensic recovery.

    Returns a list of dicts: ``user``, ``suffix``, ``path``, ``bytes``,
    ``mtime_iso``.
    """
    db = _db_path()
    parent = db.parent
    user = parent.name
    out: list[dict[str, Any]] = []
    for entry in parent.iterdir():
        if not entry.is_dir() or entry.name == "backups":
            continue
        m = _BRANCH_NAME_RE.match(entry.name)
        if not m:
            continue
        if m.group(1) != user:
            continue
        col = entry / "collection.anki2"
        if not col.exists():
            continue
        try:
            st = col.stat()
        except OSError:
            continue
        out.append(
            {
                "user": m.group(1),
                "suffix": m.group(2),
                "path": str(col),
                "bytes": st.st_size,
                "mtime_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )
    return sorted(out, key=lambda x: x["mtime_iso"])


# ---- public API ---------------------------------------------------------


def check_sync_state() -> dict[str, Any]:
    """Return the current state, the remembered state, and any branches.

    The diff section is informational; ``pre_write_check`` is the one
    that raises. Operators can call this any time to inspect the state.
    """
    current = _read_state_from_db()
    remembered = _load_remembered_state()
    branches = list_branch_copies()
    drift: dict[str, Any] = {}
    if remembered:
        if remembered.get("scm") != current["scm"]:
            drift["scm"] = {
                "remembered": remembered.get("scm"),
                "current": current["scm"],
            }
        if remembered.get("mod_ms") != current["mod_ms"]:
            drift["mod_ms"] = {
                "remembered": remembered.get("mod_ms"),
                "current": current["mod_ms"],
            }
    return {
        "current": current,
        "remembered": remembered,
        "drift": drift,
        "branches": branches,
        "branch_count": len(branches),
        "db_path": str(_db_path()),
    }


def pre_write_check(skip_backup: bool = False, reason: str = "") -> dict[str, Any]:
    """Combined gate for any MCP write.

    Steps:
        1. (optional) Back up the live collection to ``backups/``.
        2. Compare current ``scm`` to the remembered one. Raise
           ``SyncStateDrift`` if it moved.
        3. Compare current branch list to the remembered one. Raise
           ``SyncStateDrift`` if new branches appeared.
        4. Re-snapshot the state.

    Returns the new remembered state.
    """
    backup_path: str | None = None
    if not skip_backup:
        backup_path = str(create_backup(reason=reason))
    current = _read_state_from_db()
    remembered = _load_remembered_state()
    if remembered:
        if remembered.get("scm") != current["scm"]:
            raise SyncStateDrift(
                f"col.scm drift: remembered={remembered.get('scm')} "
                f"current={current['scm']}. The desktop or anki-sync "
                f"container has written to the collection between MCP "
                f"calls. Run check_sync_state to inspect, then re-run."
            )
    branches_before = (
        set(b["path"] for b in (remembered or {}).get("branches", []))
        if remembered
        else set()
    )
    branches_now = list_branch_copies()
    new_branches = [b for b in branches_now if b["path"] not in branches_before]
    if new_branches:
        raise SyncStateDrift(
            f"New branch copy(s) appeared under /sync/: {[b['path'] for b in new_branches]}. "
            f"A sync conflict was logged. Run check_sync_state to inspect."
        )
    # persist new state with branch list baked in
    new_state = dict(current)
    new_state["branches"] = branches_now
    new_state["updated_iso"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _state_path().write_text(json.dumps(new_state, indent=2, sort_keys=True))
    return {"backup_path": backup_path, "state": new_state, "branches": branches_now}
