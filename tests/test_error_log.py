"""Reading error.log, and matching each error to the mod that caused it."""

import os
from pathlib import Path

import pytest

from cold_steel.core.errors import GAME, ErrorReader, ErrorReport, GameError, is_override
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library, Scanner
from cold_steel.paradox.error_log import parse_error_log
from conftest import SampleInstall

# Real lines from the game's error.log, one of each shape.
REAL_LINES = """\
[19:27:06][dlc.cpp:1847]: Could not find files for mod: /home/u/workshop/content/281990/688086068
[19:27:06][dlc.cpp:346]: Invalid supported_version in  file: mod/ugc_1506079770.mod line: 9
[19:27:07][persistent.cpp:41]: Error: "expected { after "delay_offset", got: 0.0, near line: 16
" in file: "sound/ASB_soundeffects.asset" near line: 16
[19:27:07][pdx_audio.cpp:1111]: Sound effect with name 'auto_cannon_fire_large' already added
[19:27:33][projectile_graphics_data.cpp:460]: An item with name "ion_cannon" already exists!  \
file: gfx/projectiles/apocalypse_weapons.txt line: 44
[19:27:57][trigger_impl.cpp:1217]: Script Error: Invalid context switch [founder_species] from \
Elven Divine Order [country], common/scripted_triggers/02_x.txt:64 @ in scripted trigger \
is_individual_machine at file: common/governments/civics/00_origins.txt line: 2383, Scope:
type=country
id=419430400
[12:21:42][pdxassetutil.cpp:103]: Duplicate texture 'Airlock_TMP.dds' found (current path \
'gfx/models/ships/a/Airlock_TMP.dds', previous path 'gfx/models/ships/b/Airlock_TMP.dds')
[12:23:42][portraitobject.cpp:2108]: Could not find texture "gfx/models/portraits/x/clothes_01.dds"
"""


def test_each_kind_of_entry_names_its_file() -> None:
    entries = parse_error_log("A line before the first entry is skipped\n" + REAL_LINES)
    assert [(e.source, e.file, e.line) for e in entries] == [
        ("dlc.cpp:1847", "/home/u/workshop/content/281990/688086068", 0),
        ("dlc.cpp:346", "mod/ugc_1506079770.mod", 9),
        ("persistent.cpp:41", "sound/ASB_soundeffects.asset", 16),
        ("pdx_audio.cpp:1111", "", 0),
        ("projectile_graphics_data.cpp:460", "gfx/projectiles/apocalypse_weapons.txt", 44),
        ("trigger_impl.cpp:1217", "common/governments/civics/00_origins.txt", 2383),
        ("pdxassetutil.cpp:103", "gfx/models/ships/a/Airlock_TMP.dds", 0),
        ("portraitobject.cpp:2108", "gfx/models/portraits/x/clothes_01.dds", 0),
    ]
    # Lines that don't start with a time belong to the entry above them.
    assert entries[2].text.endswith('in file: "sound/ASB_soundeffects.asset" near line: 16')
    assert entries[5].text.endswith("id=419430400")
    assert entries[0].time == "19:27:06"


# Matching errors to mods, on the sample install


@pytest.fixture
def library(sample_install: SampleInstall) -> Library:
    # My Local Tweaks loads last and has its own common/alpha.txt, so it wins that file.
    local = sample_install.data_dir / "mod/my_local"
    (local / "common/alpha.txt").write_text("alpha_value = 2\nbroken = yes\n", "utf-8")
    (sample_install.data_dir / "dlc_load.json").write_text(
        '{"enabled_mods":["mod/ugc_2000000001.mod","mod/ugc_2000000003.mod","mod/my_local.mod"],'
        '"disabled_dlcs":[]}',
        "utf-8",
    )
    return Scanner(*sample_install.scanner_args())(JobContext())


def write_log(library: Library, text: str) -> Path:
    log = library.game.data_dir / "logs/error.log"
    log.parent.mkdir(exist_ok=True)
    log.write_text(text, "utf-8")
    # Written after dlc_load.json, as it is when the game runs.
    dlc_load = (library.game.data_dir / "dlc_load.json").stat()
    os.utime(log, ns=(dlc_load.st_atime_ns, dlc_load.st_mtime_ns + 1_000_000_000))
    return log


def groups(report: ErrorReport) -> dict[str, list[GameError]]:
    return {g.key: list(g.errors) for g in report.groups}


def test_errors_are_grouped_by_the_mod_that_caused_them(
    library: Library, sample_install: SampleInstall
) -> None:
    workshop = sample_install.workshop_dir
    write_log(
        library,
        "[10:00:00][a.cpp:1]: Bad value  file: common/alpha.txt line: 2\n"
        "[10:00:01][a.cpp:2]: Unknown music  file: music/gamma.txt line: 1\n"
        "[10:00:02][dlc.cpp:346]: Invalid supported_version in  file: mod/ugc_2000000001.mod "
        "line: 8\n"
        f"[10:00:03][dlc.cpp:1847]: Could not find files for mod: {workshop}/2000000002\n"
        "[10:00:04][b.cpp:3]: Bad event  file: events/beta.txt line: 1\n"
        "[10:00:05][c.cpp:4]: Duplicate of x added to entity system\n"
        "[10:00:06][c.cpp:4]: Duplicate of x added to entity system\n",
    )
    report = ErrorReader(library)(JobContext())

    found = groups(report)
    # The last mod in load order that has the file is the one the game read.
    [local] = found["local:my_local"]
    assert (local.file, local.line, local.code) == ("common/alpha.txt", 2, "broken = yes")
    [gamma] = found["workshop:2000000003"]  # read from inside its zip
    assert (gamma.file, gamma.code) == ("music/gamma.txt", "music = { name = gamma_theme }")
    [alpha] = found["workshop:2000000001"]
    assert alpha.file == "ugc_2000000001.mod"
    assert alpha.code == 'supported_version="v4.5.*"'  # line 8 of its mod/*.mod file
    [beta] = found["workshop:2000000002"]  # named by its folder
    assert beta.source == "dlc.cpp:1847"
    # The game checks every mod's files as it starts, but Beta Ships wasn't
    # loaded: its group is marked so, after the loaded mods.
    assert [(g.key, g.loaded) for g in report.groups] == [
        ("workshop:2000000001", True),  # one error each, so by name
        ("workshop:2000000003", True),
        ("local:my_local", True),
        ("workshop:2000000002", False),
        (GAME, True),
    ]

    # Beta Ships wasn't loaded, so events/beta.txt can't be its. Errors that
    # name no file are the game's, or unknown. Repeats are counted, not listed.
    unknown = found[GAME]
    assert [(e.file, e.count) for e in unknown] == [("events/beta.txt", 1), ("", 2)]
    assert report.groups[-1].key == GAME
    assert report.total == 7
    # The repeated "Duplicate of x" only says one definition replaced another.
    assert [e.override for e in unknown] == [False, True]
    assert (report.overrides, report.problems) == (2, 5)
    assert not report.stale


def test_overrides_are_told_apart_from_problems() -> None:
    # Real lines from a 24-mod playset's log.
    overrides = [
        "Object with key: artisan already exists, using the one at  file: common/pop_jobs/x.txt",
        "an event with id [toxoids.1] already exists!  file: events/toxoids_events.txt line: 10",
        'An item with name "ion_cannon" already exists!  file: gfx/projectiles/x.txt line: 44',
        "Duplicate of toxoid_01_starbases_entity added to entity system",
        "duplicate section template found. Multiple sections are named [ION_CANNON_CORE]. ",
        "Variable name planet_standard_scale is already taken.  file: common/x.txt line: 178",
    ]
    problems = [
        "Duplicate trigger at ' file: common/starbase_modules/x.txt line: 3'",
        "Duplicate texture 'nospec.dds' found (current path 'a/nospec.dds', previous path 'b')",
        'OnAction "on_colony_transfer" is referencing an invalid event: "mem_planetary_shields.14"',
        "Failed to find texture 'infernal_ring_world_tech_diffuse.dds'",
    ]
    assert all(is_override(text) for text in overrides)
    assert not any(is_override(text) for text in problems)


def test_no_log_yet(library: Library) -> None:
    report = ErrorReader(library)(JobContext())
    assert report.written == 0
    assert report.groups == ()


def test_a_mod_list_changed_after_the_run_is_flagged(library: Library) -> None:
    log = write_log(library, "[10:00:00][a.cpp:1]: Something\n")
    later = log.stat().st_mtime_ns + 5_000_000_000
    os.utime(library.game.data_dir / "dlc_load.json", ns=(later, later))
    assert ErrorReader(library)(JobContext()).stale
