"""`dlc_load.json`: what the game loads when it starts. The mods in
`enabled_mods` load in that order; the DLC in `disabled_dlcs` are skipped.

    {"enabled_mods": ["mod/ugc_1623423360.mod"],
     "disabled_dlcs": ["dlc/dlc033_cosmic_storms/dlc033.dlc"]}
"""

from pathlib import Path

import msgspec

from cold_steel.paradox.backup import backup_file

FILE_NAME = "dlc_load.json"


class DlcLoad(msgspec.Struct, frozen=True):
    enabled_mods: tuple[str, ...] = ()  # "mod/<name>.mod", relative to the data folder
    disabled_dlcs: tuple[str, ...] = ()  # "dlc/<folder>/<name>.dlc"


def read_dlc_load(data_dir: Path) -> DlcLoad | None:
    """None if the file is missing or can't be read."""
    try:
        return msgspec.json.decode((data_dir / FILE_NAME).read_bytes(), type=DlcLoad)
    except OSError, msgspec.DecodeError:
        return None


def write_dlc_load(data_dir: Path, load: DlcLoad, backup_dir: Path) -> Path | None:
    """Write the file after backing it up. Returns the backup, if there was a
    file to back up. Raises OSError, and writes nothing, if the backup fails."""
    path = data_dir / FILE_NAME
    backup = backup_file(path, backup_dir)
    tmp = path.with_name(f".{FILE_NAME}.tmp")
    try:
        # Compact, as the launcher writes it.
        tmp.write_bytes(msgspec.json.encode(load))
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return backup
