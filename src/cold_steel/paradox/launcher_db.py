"""Reads playsets from the Paradox launcher's `launcher-v2.sqlite`.

**Read-only.** The file is opened with SQLite's `mode=ro`, so nothing here can
change it (rule 1 in CLAUDE.md). Writing comes in Phase 2, behind a backup.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

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
    thumbnails = dict(
        conn.execute(
            "SELECT steamId, thumbnailPath FROM mods "
            "WHERE steamId IS NOT NULL AND steamId != '' AND thumbnailPath IS NOT NULL"
        ).fetchall()
    )
    return LauncherData(
        playsets=tuple(
            LauncherPlayset(id=pid, name=name, active=bool(active), mods=tuple(mods[pid]))
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
