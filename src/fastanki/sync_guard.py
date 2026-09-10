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
  Raises ``SyncStateDrift`` if **any** of the following moved since we
  last wrote: ``col.usn`` (Anki's write sequence number), ``col.mod``
  (ms-precision mod-time), ``note_count``, ``card_count``, ``max_note_id``
  — OR if a new branch copy appeared under ``/sync/<user>#*/``.

  Note: ``col.scm`` is **not** used as a drift indicator. ``scm`` is
  Anki's schema-migration marker; it only moves on schema upgrades, so
  it would miss every normal card review / note add. The Anki sync
  protocol itself uses ``usn`` for divergence detection, so we follow
  the same convention.

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
import time
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
    """Read col.scm, col.usn, col.mod, mtime, table counts, and max note id.

    Opens the DB in **immutable read-only** mode (``mode=ro&immutable=1``)
    as the primary path. SQLite's ``immutable=1`` flag tells the driver
    to skip the OS-level read-lock acquisition (and the SHM ``read-lock``
    dance) entirely — the file is treated as a static snapshot. This is
    the **only** way to reliably read the collection while ``anki-sync``
    holds its long-lived writer connection (which is always, in
    production). The risk is reading mid-write (one torn page), but
    since this is read-only drift detection and a torn read raises
    immediately, the fallback path (and caller retry) handles it.

    Fallback: a locking-aware read that respects anki-sync's writer
    lock. This is what the original test suite exercised; it works
    when anki-sync is down (test mode).
    """
    db = _db_path()
    if not db.exists():
        raise SyncGuardError(f"Collection DB not found: {db}")

    # ---- Pass 1: immutable read (lock-free, always works alongside anki-sync) ----
    try:
        return _read_state_with_uri(f"file:{db}?mode=ro&immutable=1", timeout=5)
    except sqlite3.OperationalError as e:
        # Fall through to the locking-aware path.
        immutable_err: Exception | None = e

    # ---- Pass 2: locking-aware read (used by test suite; anki-sync must be down) ----
    uri = f"file:{db}?mode=ro"
    last_err: sqlite3.OperationalError | None = None
    for attempt in range(10):
        con = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            row = con.execute("select id, scm, mod, usn from col").fetchone()
            if not row:
                raise SyncGuardError(f"Collection row missing in {db}")
            cid, scm, mod, usn = row
            notes = con.execute("select count(*) from notes").fetchone()[0]
            cards = con.execute("select count(*) from cards").fetchone()[0]
            revlog = con.execute("select count(*) from revlog").fetchone()[0]
            max_note_id = con.execute("select max(id) from notes").fetchone()[0] or 0
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
                "max_note_id": int(max_note_id),
                "db_bytes": db.stat().st_size,
            }
        except sqlite3.OperationalError as e:
            last_err = e
            if "locked" not in str(e).lower():
                raise
            con.close()
            time.sleep(min(2 ** attempt * 0.05, 0.5))
            continue
        finally:
            try:
                con.close()
            except Exception:
                pass
    raise SyncGuardError(
        f"Collection DB remained locked after 10 retries: {last_err}. "
        f"anki-sync is holding a long write transaction."
    )


def _read_state_with_uri(uri: str, timeout: int) -> dict[str, Any]:
    """Read col.scm, col.usn, col.mod, mtime, table counts, and max note id
    from the given sqlite3 URI. Helper for ``_read_state_from_db``.

    Used both for the immutable (lock-free) pass and for callers that
    want a lock-aware read.
    """
    db = _db_path()
    con = sqlite3.connect(uri, uri=True, timeout=timeout)
    try:
        row = con.execute("select id, scm, mod, usn from col").fetchone()
        if not row:
            raise SyncGuardError(f"Collection row missing in {db}")
        cid, scm, mod, usn = row
        notes = con.execute("select count(*) from notes").fetchone()[0]
        cards = con.execute("select count(*) from cards").fetchone()[0]
        revlog = con.execute("select count(*) from revlog").fetchone()[0]
        max_note_id = con.execute("select max(id) from notes").fetchone()[0] or 0
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
            "max_note_id": int(max_note_id),
            "db_bytes": db.stat().st_size,
        }
    finally:
        try:
            con.close()
        except Exception:
            pass


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
        # ``usn`` is Anki's write-sequence number. Every Anki write
        # (card review, note add, config change, deck rename) bumps it.
        # This is the same indicator Anki's own sync protocol uses for
        # divergence detection.
        if remembered.get("usn") != current["usn"]:
            drift["usn"] = {
                "remembered": remembered.get("usn"),
                "current": current["usn"],
            }
        # ``mod_ms`` is the ms-precision modification timestamp on
        # ``col.mod``. Bumped on every write. Useful as a redundant
        # signal in case ``usn`` was not updated by some other writer.
        if remembered.get("mod_ms") != current["mod_ms"]:
            drift["mod_ms"] = {
                "remembered": remembered.get("mod_ms"),
                "current": current["mod_ms"],
            }
        # Row counts: another writer added/removed notes or cards since
        # we last looked.
        for key in ("note_count", "card_count"):
            if remembered.get(key) != current[key]:
                drift[key] = {
                    "remembered": remembered.get(key),
                    "current": current[key],
                }
        # Max note id: monotonic, advances on every note insert. Catches
        # inserts even if the writer used a different schema (defensive).
        if remembered.get("max_note_id") != current["max_note_id"]:
            drift["max_note_id"] = {
                "remembered": remembered.get("max_note_id"),
                "current": current["max_note_id"],
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
        2. Compare current state to remembered on multiple signals
           (``usn``, ``mod_ms``, ``note_count``, ``card_count``,
           ``max_note_id``). Raise ``SyncStateDrift`` if any moved.
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
        # Multi-signal drift check. Any single signal moving since we
        # last wrote indicates another writer (desktop, anki-sync, or
        # a stray manual DB edit) touched the collection. We do NOT
        # use ``col.scm`` here — it's Anki's schema-migration marker
        # and only changes on schema upgrades, not on regular writes,
        # so it would miss every card review / note add.
        signals = ("usn", "mod_ms", "note_count", "card_count", "max_note_id")
        moved: list[str] = []
        details: dict[str, Any] = {}
        for key in signals:
            if remembered.get(key) != current[key]:
                moved.append(key)
                details[key] = {
                    "remembered": remembered.get(key),
                    "current": current[key],
                }
        if moved:
            raise SyncStateDrift(
                f"Collection drift on signals {moved}: {details}. "
                f"The desktop or anki-sync container has written to "
                f"the collection between MCP calls. Run "
                f"check_sync_state to inspect, then re-run."
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
