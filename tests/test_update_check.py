"""tools/update_check.py: what it notices after the game, the launcher, Steam
or a mod changes, on a copy of the sample install."""

import importlib.util
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import msgspec
import pytest

from cold_steel.paradox.game import Game
from conftest import SampleInstall, make_save

TOOL = Path(__file__).resolve().parent.parent / "tools/update_check.py"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("update_check", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load_tool()


@pytest.fixture
def game(sample_install: SampleInstall) -> Game:
    """The sample install, with the programs and game folders a real one has."""
    found: Game = tool.find_this_game()
    for name in ("stellaris", "dowser"):
        (found.install_dir / name).write_bytes(b"")
    folders = ("common/traits", "common/component_templates", "events", "gfx/models/ships")
    for folder in (*folders, "gfx/portraits/portraits"):
        (found.install_dir / folder).mkdir(parents=True)
        (found.install_dir / folder / "00_game.txt").write_text("a = { }\n", "utf-8")
    return found


def texts(problems: list[object]) -> str:
    return "\n".join(f"{p.area}: {p.text}" for p in problems)  # type: ignore[attr-defined]


def test_a_tidy_install_has_no_problems_and_no_changes(game: Game) -> None:
    assert tool.problems(game) == []
    facts = tool.collect(game)
    assert tool.compare(facts, tool.collect(game)) == []
    assert facts["game version"] == "v4.5.1"
    assert facts["how the game starts"] == "stellaris -gdpr-compliant"
    assert "playsetId char(36) NOT NULL" in facts["launcher table playsets_mods"]
    # Graphics folders go two deep, and deeper only where a rule row names them.
    assert "gfx/models" in facts["game folders"]
    assert "gfx/models/ships" not in facts["game folders"]
    assert "gfx/portraits/portraits" in facts["game folders"]


def test_the_baseline_reads_back_as_it_was_saved(game: Game, tmp_path: Path) -> None:
    base = tool.Baseline("2026-10-10T00:00:00+00:00", "1.0.0", tool.collect(game), {"1": 5})
    path = tmp_path / "baseline.json"
    path.write_bytes(msgspec.json.encode(base))
    assert msgspec.json.decode(path.read_bytes(), type=tool.Baseline) == base


def test_a_launcher_column_cold_steel_reads_going_is_a_problem(game: Game) -> None:
    with sqlite3.connect(game.launcher_db) as conn:
        conn.execute("ALTER TABLE playsets_mods DROP COLUMN position")
    conn.close()
    assert "`playsets_mods` lacks position" in texts(tool.problems(game))


def test_a_required_column_cold_steel_doesnt_write_is_a_problem(game: Game) -> None:
    with sqlite3.connect(game.launcher_db) as conn:
        conn.execute("CREATE TABLE p2 (id char(36), stamp datetime NOT NULL)")
        conn.execute("DROP TABLE playsets_mods")
        conn.execute("CREATE TABLE playsets_mods (playsetId, modId, enabled, position, x NOT NULL)")
    conn.close()
    found = texts(tool.problems(game))
    assert "`playsets_mods` now requires x" in found
    # Required columns in tables Cold Steel never inserts into don't matter.
    assert "`mods` now requires" not in found
    assert "p2" not in found


def test_launcher_table_changes_show_against_the_baseline(game: Game) -> None:
    before = tool.collect(game)
    with sqlite3.connect(game.launcher_db) as conn:
        conn.execute("ALTER TABLE playsets ADD COLUMN lastUsedAt datetime")
    conn.close()
    [change] = tool.compare(before, tool.collect(game))
    assert change.fact == "launcher table playsets"
    assert change.lines() == ["+ lastUsedAt datetime"]
    assert "Sync launcher" in change.reader


def test_keys_play_would_drop_are_problems(game: Game) -> None:
    (game.data_dir / "dlc_load.json").write_text(
        json.dumps({"enabled_mods": [], "disabled_dlcs": [], "load_order_v2": []}), "utf-8"
    )
    (game.data_dir / "continue_game.json").write_text(
        json.dumps({"title": "saves/x/y", "desc": "E", "date": "2200.01.01", "ironman": 1}),
        "utf-8",
    )
    found = texts(tool.problems(game))
    assert "dlc_load.json: It has load_order_v2" in found
    assert "continue_game.json: It has ironman" in found
    assert 'Its title is "saves/x/y"' in found


def test_a_new_empire_format_is_a_problem_and_a_change(game: Game) -> None:
    old = game.data_dir / "user_empire_designs_v3.4.txt"
    old.write_bytes(b'"A"={\r\n\tkey="A"\r\n\tauthority="auth_democratic"\r\n}\r\n')
    before = tool.collect(game)
    new = game.data_dir / "user_empire_designs_v4.6.txt"
    new.write_bytes(b'"A"={\n\tkey="A"\n\tauthority="auth_democratic"\n\tcrest="x"\n}\n')

    assert "The game now uses user_empire_designs_v4.6.txt" in texts(tool.problems(game))
    changes = {c.fact: c.lines() for c in tool.compare(before, tool.collect(game))}
    assert changes["empire file"] == [
        "was: user_empire_designs_v3.4.txt",
        "now: user_empire_designs_v4.6.txt",
    ]
    assert changes["empire fields"] == ["+ crest"]


def test_a_new_game_folder_says_which_winner_rule_it_gets(game: Game) -> None:
    before = tool.collect(game)
    for folder in ("common/brand_new", "gfx/models/ships/deeper", "gfx/brand_new"):
        (game.install_dir / folder).mkdir(parents=True)
        (game.install_dir / folder / "x.txt").write_text("", "utf-8")
    [change] = tool.compare(before, tool.collect(game))
    # A deep graphics folder shares its parent's rule, so it isn't listed.
    assert change.lines() == [
        "+ common/brand_new  (the default row: last wins, by key)",
        "+ gfx/brand_new  (the row for gfx: last wins, by name)",
    ]


def test_mods_steam_updated_are_listed_by_name(game: Game) -> None:
    acf = game.library_dir / "steamapps/workshop/appworkshop_281990.acf"
    acf.parent.mkdir(parents=True, exist_ok=True)
    item = '"{id}" {{ "size" "1" "timeupdated" "{time}" }}'
    acf.write_text(
        '"AppWorkshop" { "WorkshopItemsInstalled" { '
        + item.format(id="2000000001", time=200)
        + item.format(id="2000000003", time=100)
        + " } }",
        "utf-8",
    )
    workshop = tool.workshop_times(game)
    assert workshop == {"2000000001": 200, "2000000003": 100}
    facts = tool.collect(game)
    base = tool.Baseline("2026-10-10T00:00:00+00:00", "1.0.0", facts, {"2000000001": 150})
    report = tool.render(game, base, facts, workshop, [], None)
    assert "**Workshop mods updated or added**: 2" in report
    assert "Alpha Interface (2000000001)\n" in report
    assert "(2000000003), new" in report


def test_patch_notes_lines_are_sorted_into_topics() -> None:
    text = tool.plain_text(
        "[h2]Bugfixes[/h2][list][*]Fixed a crash when loading a save from the cloud"
        "[*]Fixed a typo in a tooltip[/list]"
        "[h2]Modding[/h2][list][*]Duplicate keys in a database now always log an error"
        "[*]The empire designer now saves crests[/list]"
    )
    assert tool.note_topics(text) == {
        "Saves and Continue": ["Fixed a crash when loading a save from the cloud (Bugfixes)"],
        "Empires": ["The empire designer now saves crests (Modding)"],
        "Error log": ["Duplicate keys in a database now always log an error (Modding)"],
    }


def test_notes_are_kept_once_and_read_back_oldest_first(tmp_path: Path) -> None:
    newer = tool.Note(datetime(2026, 10, 2, tzinfo=UTC), "4.5.2 Released!", "u2", "b\n")
    older = tool.Note(datetime(2026, 9, 1, tzinfo=UTC), "4.5.1 Released", "u1", "a\n")
    tool.keep_notes([newer, older], tmp_path)
    tool.keep_notes([newer], tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "2026-09-01-4-5-1-released.txt",
        "2026-10-02-4-5-2-released.txt",
    ]
    assert tool.kept_notes(tmp_path) == [older, newer]


def test_plain_section_names_count_as_headings() -> None:
    # Steam's release notes: section names are plain lines, items a lone "-".
    text = tool.plain_text(
        "Modding\n\n[list][*]\nRenamed `a_effects.txt` to `b_effects.txt`\n"
        "[*]\nSpecies can now use the Wet Climate Mods preference[/list]"
    )
    assert text.splitlines()[2] == "- Renamed `a_effects.txt` to `b_effects.txt`"
    assert tool.note_topics(text) == {
        "Mods, load order and overrides": ["Renamed `a_effects.txt` to `b_effects.txt` (Modding)"]
    }


def test_a_save_meta_missing_what_saves_needs_is_a_problem(game: Game) -> None:
    assert "Saves" not in texts(tool.problems(game))
    newest = game.data_dir / "save games/newest_1/2300.01.01.sav"
    make_save(newest, 'version="Corona v4.6.0"\nempire_name="E"\ndate="2300.01.01"\n')
    os.utime(newest, (2_000_000_000, 2_000_000_000))
    assert "newest_1/2300.01.01.sav, has no name in its meta" in texts(tool.problems(game))


def test_builds_made_before_an_update_are_named(game: Game, tmp_path: Path) -> None:
    built = datetime(2026, 10, 2, 14, 3)
    (tmp_path / "p1.json").write_text(
        json.dumps(
            {
                "name": "Mix",
                "built": f"{built:%Y-%m-%d %H:%M}",
                "order": ["workshop:2000000001", "workshop:2000000003@abc", "local:x.mod"],
                "files": {},
            }
        ),
        "utf-8",
    )
    stamp = int(built.timestamp())
    assert tool.stale_builds(game, {"2000000001": stamp - 60}, tmp_path) == []
    # A pinned copy doesn't change when Steam updates the mod.
    assert tool.stale_builds(game, {"2000000003": stamp + 60}, tmp_path) == []
    assert tool.stale_builds(game, {"2000000001": stamp + 60}, tmp_path) == [
        "- **Mix**, built 2026-10-02 14:03, is older than: Alpha Interface (2000000001)"
    ]
