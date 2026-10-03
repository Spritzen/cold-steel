"""Reading error.log, and matching each error to the mod that caused it."""

import os
from pathlib import Path

import pytest

from cold_steel.core.build import BuildRecord, BuiltFile, save_record
from cold_steel.core.errors import (
    GAME,
    ErrorReader,
    ErrorReport,
    GameError,
    is_missing_resource,
    is_override,
)
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
[10:42:19][eventmanager.cpp:489]: Corrupt Event Table Entry - } in events/first_contact_dlc_events.\
txtline: 6838
This usually means the syntax is wrong earlier in the file
[10:42:12][persistent.cpp:41]: Error: "Unexpected token: voidlure_tiyanki, near line: 1
" in file: "common/starbase_buildings/00_starbase_buildings.txt:2792(inline_script) \
common/inline_scripts/grand_archive/voidlure.txt" near line: 156
[10:42:26][parser_deferred_database_objects.cpp:84]: Failed to deferred read key reference \
dystopian_specialist from database  common/scripted_triggers/00_scripted_triggers.txt:5414 @ in \
scripted trigger is_specialist_category at file: script value count_specialists at file: \
common/buildings/00_capital_buildings.txt:1687(inline_script) \
common/inline_scripts/buildings/on_all_capital_buildings.txt line: 160 line: 7
[10:42:08][inlinescripts.cpp:40]: Unknown inline_script "paragon/global_faction_demands" in file: \
" file: common/pop_faction_types/00_imperialist.txt line: 703
[10:42:30][trigger_impl.cpp:900]: Wrong scope for trigger 'pop_group_crime' at  file: script value \
experiment_engineer_crime_research_output at file: common/pop_jobs/16_shroud_jobs.txt \
line: 568 line: 4
"""


def test_each_kind_of_entry_names_its_file() -> None:
    entries = parse_error_log("A line before the first entry is skipped\n" + REAL_LINES)
    assert [(e.source, e.file, e.line) for e in entries] == [
        ("dlc.cpp:1847", "/home/u/workshop/content/281990/688086068", 0),
        ("dlc.cpp:346", "mod/ugc_1506079770.mod", 9),
        ("persistent.cpp:41", "sound/ASB_soundeffects.asset", 16),
        ("pdx_audio.cpp:1111", "", 0),
        ("projectile_graphics_data.cpp:460", "gfx/projectiles/apocalypse_weapons.txt", 44),
        # In a chain, the first file is where the problem is.
        ("trigger_impl.cpp:1217", "common/scripted_triggers/02_x.txt", 64),
        ("pdxassetutil.cpp:103", "gfx/models/ships/a/Airlock_TMP.dds", 0),
        ("portraitobject.cpp:2108", "gfx/models/portraits/x/clothes_01.dds", 0),
        # No space before "line".
        ("eventmanager.cpp:489", "events/first_contact_dlc_events.txt", 6838),
        # An inline script: the file that uses it, at the line that does.
        ("persistent.cpp:41", "common/starbase_buildings/00_starbase_buildings.txt", 2792),
        (
            "parser_deferred_database_objects.cpp:84",
            "common/scripted_triggers/00_scripted_triggers.txt",
            5414,
        ),
        ("inlinescripts.cpp:40", "common/pop_faction_types/00_imperialist.txt", 703),
        # A script value has no file of its own; the one using it does.
        ("trigger_impl.cpp:900", "common/pop_jobs/16_shroud_jobs.txt", 568),
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


def test_missing_top_bar_resources_are_told_apart_from_problems(library: Library) -> None:
    # Real lines: Universal Resource Patch listing resources from mods that aren't installed.
    source = "strategic_resources_gui_group.cpp:438"
    text = "Failed to read key reference sr_latinum from database  file: x.txt line: 14"
    assert is_missing_resource(source, text)
    assert not is_missing_resource("other.cpp:1", text)
    assert not is_missing_resource(source, "Something else")

    write_log(
        library,
        f"[10:42:21][{source}]: Failed to read key reference sr_latinum from database  "
        "file: interface/resource_groups/tb_extras_group.txt line: 14\n"
        f"[10:42:21][{source}]: Failed to read key reference sr_crew from database  "
        "file: interface/resource_groups/tb_extras_group.txt line: 15\n"
        "[10:42:22][a.cpp:1]: A real problem\n",
    )
    report = ErrorReader(library)(JobContext())
    assert (report.total, report.resources, report.problems) == (3, 2, 1)


def test_a_parse_error_names_what_the_file_defines_after_it(
    library: Library, sample_install: SampleInstall
) -> None:
    local = sample_install.data_dir / "mod/my_local"
    triggers = local / "common/scripted_triggers/x_triggers.txt"
    triggers.parent.mkdir(parents=True)
    triggers.write_text(
        "first = { always = yes }\n"
        "second = { any_system_colony = { } }\n"
        "third = { always = yes }\n"
        "fourth = { always = yes }\n",
        "utf-8",
    )
    library = Scanner(*sample_install.scanner_args())(JobContext())
    write_log(
        library,
        '[10:42:07][persistent.cpp:41]: Error: "Unexpected token: any_system_colony, near line: 2\n'
        '" in file: "common/scripted_triggers/x_triggers.txt" near line: 2\n'
        "[10:42:08][a.cpp:1]: Bad value  file: common/alpha.txt line: 2\n",
    )
    found = groups(ErrorReader(library)(JobContext()))
    broken, other = found["local:my_local"]
    assert broken.lost == ("third", "fourth")
    assert other.lost == ()  # not a parse error


def test_errors_in_a_build_are_traced_to_its_mods(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    data = sample_install.data_dir
    folder = data / "mod/cold_steel_build_p"
    (folder / "common").mkdir(parents=True)
    (folder / "common/alpha.txt").write_text("alpha_value = 2\nbroken = yes\n", "utf-8")
    (folder / "common/new.txt").write_text("x = 1\n", "utf-8")
    (data / "mod/cold_steel_build_p.mod").write_text(
        f'name="Cold Steel build: Test"\nsupported_version="v4.5.*"\npath="{folder}"\n', "utf-8"
    )
    (data / "dlc_load.json").write_text(
        '{"enabled_mods":["mod/cold_steel_build_p.mod"],"disabled_dlcs":[]}', "utf-8"
    )
    builds = tmp_path / "builds"
    save_record(
        builds,
        BuildRecord(
            playset="p",
            order=("workshop:2000000001", "local:my_local"),
            names={"workshop:2000000001": "Alpha", "local:my_local": "My Local Tweaks"},
            files={"common/Alpha.txt": BuiltFile("local:my_local", "common/alpha.txt", (0, 0))},
        ),
    )
    library = Scanner(*sample_install.scanner_args())(JobContext())
    write_log(
        library,
        "[10:00:00][a.cpp:1]: Bad value  file: common/alpha.txt line: 2\n"
        "[10:00:01][a.cpp:1]: Bad value  file: common/new.txt line: 1\n",
    )
    report = ErrorReader(library, builds)(JobContext())

    found = groups(report)
    [traced] = found["local:my_local"]
    # Named as in the mod it came from; the line is read from the build's copy.
    assert (traced.file, traced.code) == ("common/alpha.txt", "broken = yes")
    # A file the record doesn't list stays with the build.
    [untraced] = found["local:cold_steel_build_p"]
    assert untraced.file == "common/new.txt"
    assert report.build == "Cold Steel build: Test"
    assert all(g.loaded for g in report.groups)
    assert not report.stale

    # Rebuilt after the run: the record may not match what the game read.
    record = builds / "p.json"
    later = record.stat().st_mtime_ns + 5_000_000_000
    os.utime(record, ns=(later, later))
    assert ErrorReader(library, builds)(JobContext()).stale


def test_no_log_yet(library: Library) -> None:
    report = ErrorReader(library)(JobContext())
    assert report.written == 0
    assert report.groups == ()


def test_a_mod_list_changed_after_the_run_is_flagged(library: Library) -> None:
    log = write_log(library, "[10:00:00][a.cpp:1]: Something\n")
    later = log.stat().st_mtime_ns + 5_000_000_000
    os.utime(library.game.data_dir / "dlc_load.json", ns=(later, later))
    assert ErrorReader(library)(JobContext()).stale
