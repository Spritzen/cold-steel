"""Is the Paradox launcher, the game or Steam running? Read from /proc.

The launcher keeps playsets in memory and writes them back, so writing its
database while it runs would lose our change. The game reads `dlc_load.json`
only as it starts.
"""

from pathlib import Path

# Names as /proc/<pid>/comm gives them: the program's file name, cut to 15 characters.
LAUNCHER = frozenset({"dowser", "Paradox Launche"})
GAME = frozenset({"stellaris"})
STEAM = frozenset({"steam", "steamwebhelper"})


def running(names: frozenset[str], proc: Path = Path("/proc")) -> bool:
    try:
        entries = list(proc.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text("utf-8", errors="replace").strip()
        except OSError:  # it ended while we looked, or isn't ours to read
            continue
        if comm in names:
            return True
    return False
