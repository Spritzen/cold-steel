"""Check the winner rules in merge_rules.json against the real game.

Two small local mods, "Cold Steel Live A" and "Cold Steel Live B", define the
same things in ways where the rules and load order predict different winners.
Each definition writes a line to the game's log saying which one ran.

    python3 tools/live_rules.py install   # writes the two mods into your mod folder
    (make a playset: Live A, then Live B; Play; start a new game; quit)
    python3 tools/live_rules.py check     # reads logs/game.log and reports each rule
    python3 tools/live_rules.py remove    # deletes the two mods again

Run it on the host, where the game runs. It only ever creates or deletes its
own two mods: it refuses to touch a folder it didn't make.
"""

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cold_steel.paradox.game import DEFAULT_STEAM_DIRS, Game, find_game

MARKER = ".cold-steel-live"  # proves a folder is ours to replace or delete
TAG = "COLDSTEEL"
BOM = "﻿"


@dataclass(frozen=True)
class Check:
    rule: str  # the merge_rules.json row it confirms
    expect: str  # the log line, after "COLDSTEEL <name>: ", that the rule predicts
    other: str  # what load order alone would predict


CHECKS = {
    "last": Check(
        "default (last wins)",
        "zz file in A",
        "aa file in B",
    ),
    "first": Check("events (first wins)", "aa file in B", "zz file in A"),
    "first-variables": Check(
        "common/scripted_variables (first wins)", "aa file in B", "zz file in A"
    ),
    "same-path": Check("whole files (load order)", "B", "A"),
    "merged-a": Check("common/on_actions (merged)", "A ran", "missing"),
    "merged-b": Check("common/on_actions (merged)", "B ran", "missing"),
}
# Localisation can't be logged, so it's shown in a window at the start of the game.
POPUP = """\
The window "Cold Steel live check" should read:
  title: "replace/ in A wins"   (localisation: replace/ beats the rest)
  text:  "zz file in A wins"    (localisation: last file name wins)
If it says "B" anywhere, the localisation rule is wrong. Load order alone would
give "B outside replace/ wins" and "aa file in B wins".
"""


def log(name: str, text: str) -> str:
    return f'log = "{TAG} {name}: {text}"'


def files() -> dict[str, dict[str, str]]:
    """Each mod's files, by path inside the mod."""
    event_check = (
        "country_event = {{ id = cs_live.10 hide_window = yes is_triggered_only = yes "
        "immediate = {{ {} }} }}\n"
    )
    variables = (
        "set_variable = { which = cs_live_var value = @cs_live_var }\n"
        "if = { limit = { check_variable = { which = cs_live_var value = 1 } } "
        + log("first-variables", "aa file in B")
        + " }\n"
        "if = { limit = { check_variable = { which = cs_live_var value = 2 } } "
        + log("first-variables", "zz file in A")
        + " }\n"
    )
    return {
        "a": {
            "common/scripted_effects/zz_cs_live_a.txt": (
                f"cs_live_last = {{ {log('last', 'zz file in A')} }}\n"
            ),
            "common/scripted_effects/cs_live_same.txt": (
                f"cs_live_same = {{ {log('same-path', 'A')} }}\n"
            ),
            "common/scripted_variables/zz_cs_live_a.txt": "@cs_live_var = 2\n",
            "common/on_actions/cs_live_a.txt": (
                "on_game_start_country = { events = { cs_live_a.1 } }\n"
            ),
            "events/zz_cs_live_a.txt": (
                "namespace = cs_live\n" + event_check.format(log("first", "zz file in A"))
            ),
            "events/cs_live_a_run.txt": (
                "namespace = cs_live_a\n"
                "country_event = {\n"
                "\tid = cs_live_a.1\n\thide_window = yes\n\tis_triggered_only = yes\n"
                "\ttrigger = { is_ai = no }\n"
                "\timmediate = {\n"
                f"\t\t{log('merged-a', 'A ran')}\n"
                "\t\tcs_live_last = yes\n\t\tcs_live_same = yes\n"
                "\t\tcountry_event = { id = cs_live.10 }\n"
                f"\t\t{variables}"
                "\t\tcountry_event = { id = cs_live_a.2 }\n"
                "\t}\n}\n"
                "country_event = {\n"
                "\tid = cs_live_a.2\n\ttitle = cs_live_loc_replace\n\tdesc = cs_live_loc_name\n"
                "\tis_triggered_only = yes\n\toption = { name = OK }\n}\n"
            ),
            "localisation/english/replace/cs_live_a_l_english.yml": (
                f'{BOM}l_english:\n cs_live_loc_replace:0 "replace/ in A wins"\n'
            ),
            "localisation/english/zz_cs_live_a_l_english.yml": (
                f'{BOM}l_english:\n cs_live_loc_name:0 "zz file in A wins"\n'
            ),
        },
        "b": {
            "common/scripted_effects/aa_cs_live_b.txt": (
                f"cs_live_last = {{ {log('last', 'aa file in B')} }}\n"
            ),
            "common/scripted_effects/cs_live_same.txt": (
                f"cs_live_same = {{ {log('same-path', 'B')} }}\n"
            ),
            "common/scripted_variables/aa_cs_live_b.txt": "@cs_live_var = 1\n",
            "common/on_actions/cs_live_b.txt": (
                "on_game_start_country = { events = { cs_live_b.1 } }\n"
            ),
            "events/aa_cs_live_b.txt": (
                "namespace = cs_live\n" + event_check.format(log("first", "aa file in B"))
            ),
            "events/cs_live_b_run.txt": (
                "namespace = cs_live_b\n"
                "country_event = {\n"
                "\tid = cs_live_b.1\n\thide_window = yes\n\tis_triggered_only = yes\n"
                "\ttrigger = { is_ai = no }\n"
                f"\timmediate = {{ {log('merged-b', 'B ran')} }}\n}}\n"
            ),
            "localisation/english/cs_live_b_l_english.yml": (
                f'{BOM}l_english:\n cs_live_loc_replace:0 "B outside replace/ wins"\n'
            ),
            "localisation/english/aa_cs_live_b_l_english.yml": (
                f'{BOM}l_english:\n cs_live_loc_name:0 "aa file in B wins"\n'
            ),
        },
    }


def install(game: Game) -> None:
    version = game.version.removeprefix("v").split(".")
    supported = f"v{version[0]}.{version[1]}.*" if len(version) >= 2 else "v4.5.*"
    for letter, contents in files().items():
        name = f"cold_steel_live_{letter}"
        folder = game.mod_dir / name
        descriptor = game.mod_dir / f"{name}.mod"
        if folder.exists() and not (folder / MARKER).exists():
            sys.exit(f"{folder} exists and isn't ours. Nothing was written.")
        if descriptor.exists() and not folder.exists():
            sys.exit(f"{descriptor} exists and isn't ours. Nothing was written.")
        shutil.rmtree(folder, ignore_errors=True)
        for path, text in contents.items():
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_text(text, "utf-8")
        (folder / MARKER).write_text("Made by Cold Steel's tools/live_rules.py\n", "utf-8")
        body = (
            f'name="Cold Steel Live {letter.upper()}"\n'
            'tags={\n\t"Utilities"\n}\n'
            f'supported_version="{supported}"\n'
        )
        (folder / "descriptor.mod").write_text(body, "utf-8")
        descriptor.write_text(body + f'path="{folder}"\n', "utf-8")
        print(f"Wrote {folder}")
    print(
        "\nNext, on this machine:\n"
        "1. Rescan in Cold Steel, then make a playset with Cold Steel Live A first, "
        "then Cold Steel Live B. Nothing else.\n"
        "2. Play, and start a new game (any empire, the smallest galaxy).\n"
        "3. Note what the 'Cold Steel live check' window says, then quit.\n"
        "4. Run: python3 tools/live_rules.py check\n"
    )


def check(game: Game) -> int:
    log_file = game.data_dir / "logs/game.log"
    try:
        lines = log_file.read_text("utf-8", "replace").splitlines()
    except OSError:
        print(f"Can't read {log_file}. Has the game run since installing?")
        return 1
    seen: dict[str, list[str]] = {}
    for line in lines:
        if f"{TAG} " in line:
            name, _, said = line.split(f"{TAG} ", 1)[1].partition(": ")
            seen.setdefault(name, []).append(said.strip())
    if not seen:
        print(f"No {TAG} lines in {log_file}. Did a new game start with both mods?")
        return 1
    failed = 0
    for name, want in CHECKS.items():
        got = seen.get(name, [])
        if got == [want.expect]:
            verdict = "confirmed"
        else:
            failed += 1
            verdict = f"WRONG: got {got or 'nothing'}, rule says {want.expect!r}"
            if got == [want.other]:
                verdict += " (that's what load order alone gives)"
        print(f"{want.rule:45} {verdict}")
    print("\n" + POPUP)
    return 1 if failed else 0


def remove(game: Game) -> None:
    for letter in "ab":
        name = f"cold_steel_live_{letter}"
        folder = game.mod_dir / name
        if (folder / MARKER).exists():
            shutil.rmtree(folder)
            (game.mod_dir / f"{name}.mod").unlink(missing_ok=True)
            print(f"Removed {folder}")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("install", "check", "remove"):
        print(__doc__)
        return 2
    game = find_game(DEFAULT_STEAM_DIRS)
    if argv[1] == "install":
        install(game)
    elif argv[1] == "remove":
        remove(game)
    else:
        return check(game)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
