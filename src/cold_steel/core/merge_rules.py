"""How the game picks between two definitions of one object, per game folder.

The rules are data, in `cold_steel/data/merge_rules.json`, so they're easy to
check and fix: one row per folder. They start from Irony's rules and are
confirmed by live runs, which fill in each row's `checked` date.
"""

from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Literal

import msgspec

type Winner = Literal["last", "first", "merged", "localisation"]
type Unit = Literal["key", "field", "define", "name", "localisation", "file"]

# Files read for the objects inside them. Every other file only clashes as a
# whole file, with another mod's file at the same path.
SCRIPT_FOLDERS = ("common/", "events/", "prescripted_countries/", "map/")
SCRIPT_SUFFIX = ".txt"
GRAPHICS_FOLDERS = ("gfx/", "interface/")
GRAPHICS_SUFFIXES = (".txt", ".gfx", ".gui", ".asset")
LOCALISATION_FOLDERS = ("localisation/", "localisation_synced/")


class Rule(msgspec.Struct, frozen=True):
    folder: str = ""
    winner: Winner = "last"
    unit: Unit = "key"
    field: str = ""  # for unit "field": the field that names each object
    source: str = ""  # where the rule came from
    checked: str = ""  # the date a live run confirmed it

    @property
    def first_wins(self) -> bool:
        return self.winner == "first"


class _Table(msgspec.Struct):
    default: Rule
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class Rules:
    default: Rule
    by_folder: dict[str, Rule]

    def for_folder(self, folder: str) -> Rule:
        """The row for this folder, or for the nearest folder above it that has one."""
        while folder:
            rule = self.by_folder.get(folder)
            if rule is not None:
                return rule
            folder = folder.rpartition("/")[0]
        return self.default

    def for_file(self, path: str) -> Rule:
        """The rule for a file, by its path inside a mod. "file" if it isn't read inside."""
        folder, _, name = path.rpartition("/")
        lowered = path.lower()
        rule = self.for_folder(folder.lower())
        if rule.unit == "file":
            return rule
        if lowered.startswith(LOCALISATION_FOLDERS):
            readable = lowered.endswith(".yml") and rule.unit == "localisation"
        elif lowered.startswith(GRAPHICS_FOLDERS):
            readable = lowered.endswith(GRAPHICS_SUFFIXES)
        else:
            readable = lowered.startswith(SCRIPT_FOLDERS) and lowered.endswith(SCRIPT_SUFFIX)
        if not readable or not name or name.lower().startswith("readme"):
            return msgspec.structs.replace(rule, unit="file")
        return rule


def parse_rules(data: bytes) -> Rules:
    table = msgspec.json.decode(data, type=_Table)
    return Rules(table.default, {r.folder.lower(): r for r in table.rules})


@cache
def load_rules() -> Rules:
    data = resources.files("cold_steel").joinpath("data/merge_rules.json").read_bytes()
    return parse_rules(data)
