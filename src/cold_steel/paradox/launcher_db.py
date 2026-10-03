"""Reads and writes playsets in the Paradox launcher's `launcher-v2.sqlite`.

Reading opens the file with SQLite's `mode=ro`, so it can't change anything.
Writing backs the file up first (decision 6). `write_playset` changes only
the one playset it was given; `replace_playsets` makes the launcher's list
exactly the one it was given. Don't call it while the launcher
is running: the launcher keeps its own copy of the data in memory.
"""

import os
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from cold_steel.paradox.backup import backup_file, keep_original

# The columns we read. The launcher's layout changes between versions, so we
# check for these on open and stop with a clear message if any are missing.
REQUIRED_COLUMNS = {
    "playsets": {"id", "name", "isActive", "createdOn"},
    "playsets_mods": {"playsetId", "modId", "enabled", "position"},
    "mods": {"id", "steamId", "name", "displayName", "dirPath", "archivePath", "thumbnailPath"},
}


class LauncherDbError(Exception):
    pass


@dataclass(frozen=True)
class LauncherMod:
    """One mod in a playset, as the launcher records it."""

    steam_id: str
    name: str
    dir_path: str
    archive_path: str
    enabled: bool


@dataclass(frozen=True)
class LauncherPlayset:
    id: str
    name: str
    active: bool
    mods: tuple[LauncherMod, ...]  # in load order
    disabled_dlcs: tuple[str, ...] = ()  # the launcher's DLC ids, e.g. "arachnoid"


@dataclass(frozen=True)
class LauncherData:
    playsets: tuple[LauncherPlayset, ...]  # oldest first, which is how the launcher lists them
    thumbnails: dict[str, str]  # Workshop ID -> the launcher's copy of its Steam thumbnail


def read_launcher(db_path: Path) -> LauncherData:
    if not db_path.is_file():
        raise LauncherDbError(f"The launcher database isn't there: {db_path}")
    try:
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise LauncherDbError(f"Couldn't open {db_path}: {error}") from error
    try:
        with closing(conn):
            return _read(conn)
    except sqlite3.Error as error:
        raise LauncherDbError(f"Couldn't read {db_path}: {error}") from error


def _read(conn: sqlite3.Connection) -> LauncherData:
    columns = _check_columns(conn)
    not_removed = "WHERE NOT isRemoved" if "isRemoved" in columns["playsets"] else ""
    playsets = conn.execute(
        f"SELECT id, name, isActive FROM playsets {not_removed} ORDER BY createdOn, rowid"
    ).fetchall()

    mods: dict[str, list[LauncherMod]] = {pid: [] for pid, _, _ in playsets}
    rows = conn.execute(
        """
        SELECT pm.playsetId, pm.enabled, m.steamId, COALESCE(m.displayName, m.name),
               m.dirPath, m.archivePath
        FROM playsets_mods pm JOIN mods m ON m.id = pm.modId
        ORDER BY pm.playsetId, pm.position IS NULL, pm.position, pm.rowid
        """
    )
    for pid, enabled, steam_id, name, dir_path, archive in rows:
        if pid in mods:
            mods[pid].append(
                LauncherMod(
                    steam_id=steam_id or "",
                    name=name or "",
                    dir_path=dir_path or "",
                    archive_path=archive or "",
                    enabled=bool(enabled) if enabled is not None else True,
                )
            )
    disabled: dict[str, list[str]] = {pid: [] for pid, _, _ in playsets}
    if _has_table(conn, "playsets_dlcs"):
        for pid, dlc_id in conn.execute(
            "SELECT playsetId, dlcId FROM playsets_dlcs WHERE NOT enabled ORDER BY dlcId"
        ):
            if pid in disabled:
                disabled[pid].append(dlc_id)
    thumbnails = dict(
        conn.execute(
            "SELECT steamId, thumbnailPath FROM mods "
            "WHERE steamId IS NOT NULL AND steamId != '' AND thumbnailPath IS NOT NULL"
        ).fetchall()
    )
    return LauncherData(
        playsets=tuple(
            LauncherPlayset(
                id=pid,
                name=name,
                active=bool(active),
                mods=tuple(mods[pid]),
                disabled_dlcs=tuple(disabled[pid]),
            )
            for pid, name, active in playsets
        ),
        thumbnails=thumbnails,
    )


def _check_columns(conn: sqlite3.Connection) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    missing: list[str] = []
    for table, needed in REQUIRED_COLUMNS.items():
        # Table names come from our own constant, not from the file.
        found[table] = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing += [f"{table}.{c}" for c in sorted(needed - found[table])]
    if missing:
        raise LauncherDbError(
            "This launcher database has a layout Cold Steel doesn't know. Missing: "
            + ", ".join(missing)
        )
    return found


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return row.fetchone() is not None


@dataclass(frozen=True)
class ExportMod:
    """One mod to write into a launcher playset. Matched to the launcher's own
    mod list by Workshop ID, then by folder or zip."""

    name: str
    steam_id: str
    dir_path: str
    archive_path: str
    enabled: bool


@dataclass(frozen=True)
class WriteResult:
    launcher_id: str  # the launcher playset written: the one given, or a new one
    backup: Path | None
    skipped: tuple[str, ...]  # names of mods the launcher doesn't know yet


def write_playset(
    db_path: Path,
    *,
    name: str,
    mods: Sequence[ExportMod],
    backup_dir: Path,
    launcher_id: str = "",
    dlc_enabled: Callable[[str], bool | None] = lambda dlc_id: None,
    active: bool = False,
) -> WriteResult:
    """Write one playset into the launcher database, after backing it up.

    Replaces the playset `launcher_id` if the launcher still has it, otherwise
    adds a new one. `dlc_enabled` maps each of the launcher's DLC ids to on,
    off, or None to leave it as it is. `active` makes it the playset the
    launcher shows when it opens.
    """
    with _writing(db_path, backup_dir) as (conn, columns, backup):
        pid, skipped = _write(conn, columns, name, mods, launcher_id, dlc_enabled)
        if active:
            conn.execute("UPDATE playsets SET isActive = (id = ?)", (pid,))
    return WriteResult(launcher_id=pid, backup=backup, skipped=skipped)


@dataclass(frozen=True)
class PlaysetWrite:
    """One playset for `replace_playsets`. The fields mean what they do in
    `write_playset`."""

    name: str
    mods: Sequence[ExportMod]
    launcher_id: str = ""
    dlc_enabled: Callable[[str], bool | None] = lambda dlc_id: None


@dataclass(frozen=True)
class ReplaceResult:
    launcher_ids: tuple[str, ...]  # one per playset given, in the same order
    backup: Path | None
    skipped: tuple[tuple[str, ...], ...]  # per playset: mods the launcher doesn't know yet
    removed: int  # how many of the launcher's own playsets were removed


def replace_playsets(
    db_path: Path,
    playsets: Sequence[PlaysetWrite],
    *,
    backup_dir: Path,
    active: int | None = None,
) -> ReplaceResult:
    """Make the launcher's playsets exactly these, in this order, after one
    backup. Each replaces its `launcher_id` if the launcher has it and no
    earlier one took it, otherwise it's added. Every other playset is removed,
    the way the launcher removes one. `active` is the index of the playset the
    launcher shows when it opens; None keeps the launcher's choice if it
    survives, else picks the first.
    """
    with _writing(db_path, backup_dir) as (conn, columns, backup):
        now = int(time.time() * 1000)
        ids: list[str] = []
        skipped: list[tuple[str, ...]] = []
        for n, playset in enumerate(playsets):
            # Two of ours can come from the same launcher playset (imported twice).
            launcher_id = playset.launcher_id if playset.launcher_id not in ids else ""
            pid, missing = _write(
                conn, columns, playset.name, playset.mods, launcher_id, playset.dlc_enabled
            )
            # The launcher lists playsets oldest first, so this keeps our order.
            conn.execute("UPDATE playsets SET createdOn = ? WHERE id = ?", (now + n, pid))
            ids.append(pid)
            skipped.append(missing)

        keep = ", ".join("?" * len(ids))
        if "isRemoved" in columns["playsets"]:
            removed = conn.execute(
                f"UPDATE playsets SET isRemoved = 1, isActive = 0 "
                f"WHERE NOT isRemoved AND id NOT IN ({keep})",
                ids,
            ).rowcount
        else:
            gone = [
                r[0] for r in conn.execute(f"SELECT id FROM playsets WHERE id NOT IN ({keep})", ids)
            ]
            for pid in gone:
                conn.execute("DELETE FROM playsets_mods WHERE playsetId = ?", (pid,))
                if _has_table(conn, "playsets_dlcs"):
                    conn.execute("DELETE FROM playsets_dlcs WHERE playsetId = ?", (pid,))
                conn.execute("DELETE FROM playsets WHERE id = ?", (pid,))
            removed = len(gone)

        if active is not None:
            conn.execute("UPDATE playsets SET isActive = (id = ?)", (ids[active],))
        elif (
            ids
            and not conn.execute(
                f"SELECT 1 FROM playsets WHERE isActive AND id IN ({keep})", ids
            ).fetchone()
        ):
            conn.execute("UPDATE playsets SET isActive = (id = ?)", (ids[0],))
    return ReplaceResult(
        launcher_ids=tuple(ids), backup=backup, skipped=tuple(skipped), removed=removed
    )


@contextmanager
def _writing(
    db_path: Path, backup_dir: Path
) -> Iterator[tuple[sqlite3.Connection, dict[str, set[str]], Path | None]]:
    """Open the database for writing, lock it, back it up, and commit at the
    end. Anything that goes wrong rolls back, so nothing is half written."""
    if not db_path.is_file():
        raise LauncherDbError(f"The launcher database isn't there: {db_path}")
    try:
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=rw", uri=True, isolation_level=None)
    except sqlite3.Error as error:
        raise LauncherDbError(f"Couldn't open {db_path}: {error}") from error
    try:
        with closing(conn):
            columns = _check_columns(conn)
            # Lock first, so nothing can change the file between backup and write.
            conn.execute("BEGIN IMMEDIATE")
            try:
                keep_original(db_path)
                backup = backup_file(db_path, backup_dir)
                yield conn, columns, backup
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    except OSError as error:
        message = f"Couldn't back up {db_path}, so nothing was written: {error}"
        raise LauncherDbError(message) from error
    except sqlite3.Error as error:
        raise LauncherDbError(f"Couldn't write {db_path}: {error}") from error


def _write(
    conn: sqlite3.Connection,
    columns: dict[str, set[str]],
    name: str,
    mods: Sequence[ExportMod],
    launcher_id: str,
    dlc_enabled: Callable[[str], bool | None],
) -> tuple[str, tuple[str, ...]]:
    now = int(time.time() * 1000)  # the launcher stores milliseconds
    removed = "AND NOT isRemoved" if "isRemoved" in columns["playsets"] else ""
    exists = (
        launcher_id
        and conn.execute(
            f"SELECT 1 FROM playsets WHERE id = ? {removed}", (launcher_id,)
        ).fetchone()
    )
    if exists:
        conn.execute("UPDATE playsets SET name = ? WHERE id = ?", (name, launcher_id))
        if "updatedOn" in columns["playsets"]:
            conn.execute("UPDATE playsets SET updatedOn = ? WHERE id = ?", (now, launcher_id))
        pid = launcher_id
    else:
        pid = str(uuid.uuid4())
        row = {"id": pid, "name": name, "isActive": 0, "createdOn": now}
        row |= {"loadOrder": "custom", "updatedOn": now, "isRemoved": 0}
        row = {k: v for k, v in row.items() if k in columns["playsets"]}
        conn.execute(
            f"INSERT INTO playsets ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
            tuple(row.values()),
        )

    by_steam: dict[str, str] = {}
    by_path: dict[str, str] = {}
    for mod_id, steam_id, dir_path, archive in conn.execute(
        "SELECT id, steamId, dirPath, archivePath FROM mods"
    ):
        if steam_id:
            by_steam.setdefault(steam_id, mod_id)
        for path in (dir_path, archive):
            if path:
                by_path.setdefault(os.path.normpath(path), mod_id)

    conn.execute("DELETE FROM playsets_mods WHERE playsetId = ?", (pid,))
    skipped: list[str] = []
    position = 0
    for mod in mods:
        mod_id = by_steam.get(mod.steam_id) if mod.steam_id else None
        for path in (mod.dir_path, mod.archive_path):
            if mod_id is None and path:
                mod_id = by_path.get(os.path.normpath(path))
        if mod_id is None:
            skipped.append(mod.name)
            continue
        conn.execute(
            "INSERT INTO playsets_mods (playsetId, modId, enabled, position) VALUES (?, ?, ?, ?)",
            (pid, mod_id, int(mod.enabled), position),
        )
        position += 1

    if _has_table(conn, "playsets_dlcs"):
        # The launcher's DLC ids, from every playset: it writes a row for each DLC.
        known = [r[0] for r in conn.execute("SELECT DISTINCT dlcId FROM playsets_dlcs")]
        for dlc_id in known:
            enabled = dlc_enabled(dlc_id)
            if enabled is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO playsets_dlcs (playsetId, dlcId, enabled) "
                    "VALUES (?, ?, ?)",
                    (pid, dlc_id, int(enabled)),
                )
    return pid, tuple(skipped)
