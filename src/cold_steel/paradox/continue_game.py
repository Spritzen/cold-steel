"""`continue_game.json`: the save file the game opens when it's started with
`--continuelastsave`, skipping its main menu. The game writes it as it saves:

    {
        "title": "save games/commonwealthofman_1251622081/2203.01.08",
        "desc": "Commonwealth of Man",
        "date": "2203.01.08"
    }

(The game indents with tabs, and puts a tab after each colon. So do we.)

`title` is the save file's path inside the game's data folder, without `.sav`.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from cold_steel.paradox.backup import backup_file
from cold_steel.paradox.save import SAVE_FOLDER

FILE_NAME = "continue_game.json"
# Two dashes, as the Paradox launcher passes it. With one, the game stopped at
# its main menu (seen on the host, 2026-10-09).
CONTINUE_ARG = "--continuelastsave"


@dataclass(frozen=True)
class ContinueGame:
    folder: str  # the save's folder, "commonwealthofman_1251622081"
    name: str  # the save file's name without .sav, "2203.01.08"
    empire: str  # "Commonwealth of Man"
    date: str  # the in-game date, "2203.01.08"

    @property
    def title(self) -> str:
        return f"{SAVE_FOLDER}/{self.folder}/{self.name}"


def format_continue_game(target: ContinueGame) -> str:
    """The file's text, laid out as the game writes it."""
    fields = {"title": target.title, "desc": target.empire, "date": target.date}
    lines = [f"\t{json.dumps(k)}:\t{json.dumps(v, ensure_ascii=False)}" for k, v in fields.items()]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def write_continue_game(data_dir: Path, target: ContinueGame, backup_dir: Path) -> Path | None:
    """Write the file after backing it up. Returns the backup, if there was a
    file to back up. Raises OSError, and writes nothing, if the backup fails."""
    path = data_dir / FILE_NAME
    backup = backup_file(path, backup_dir)
    tmp = path.with_name(f".{FILE_NAME}.tmp")
    try:
        tmp.write_text(format_continue_game(target), "utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return backup
