"""tools/live_rules.py: its two test mods, read by our own conflict finder.

The live run checks the game against merge_rules.json. This checks that the
test mods ask the questions we think they do: our finder must predict exactly
what the tool expects the game's log to say.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from cold_steel.core.conflicts import FILE, ConflictFinder
from cold_steel.core.index import Indexer
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Scanner
from cold_steel.paradox.game import find_game
from cold_steel.store.playsets import Playset, PlaysetEntry
from conftest import SampleInstall

TOOL = Path(__file__).resolve().parent.parent / "tools/live_rules.py"
A, B = "local:cold_steel_live_a", "local:cold_steel_live_b"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("live_rules", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_our_finder_predicts_what_the_live_run_expects(sample_install: SampleInstall) -> None:
    tool = load_tool()
    game = find_game((sample_install.steam_dir,))
    tool.install(game)

    library = Scanner(*sample_install.scanner_args())(JobContext())
    index = Indexer(library, sample_install.cache_file.parent / "index", workers=1)(JobContext())
    playset = Playset(id="live", name="Live", entries=(PlaysetEntry(A), PlaysetEntry(B)))
    found = ConflictFinder(index, playset, library)(JobContext())
    winners = {(c.kind, c.key): c.winning.layer for c in found.conflicts}

    letter = {A: "A", B: "B"}
    checks = tool.CHECKS
    expect = {
        ("common/scripted_effects", "cs_live_last"): checks["last"].expect,
        ("events", "cs_live.10"): checks["first"].expect,
        ("common/scripted_variables", "@cs_live_var"): checks["first-variables"].expect,
        (FILE, "common/scripted_effects/cs_live_same.txt"): checks["same-path"].expect,
    }
    for key, said in expect.items():
        assert key in winners, key
        assert said.endswith(letter[winners[key]]) or f"in {letter[winners[key]]}" in said, key

    # Localisation: what the window at the start of the game should show.
    assert winners["localisation", "cs_live_loc_replace"] == A
    assert winners["localisation", "cs_live_loc_name"] == A
    # on_actions are merged, so both mods' events run.
    assert ("common/on_actions", "on_game_start_country") not in winners

    tool.remove(game)
    assert not (game.mod_dir / "cold_steel_live_a").exists()
