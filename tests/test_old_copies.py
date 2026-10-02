"""Old copies: a mod made for an older game replacing a newer mod's files.

Alpha is made for v4.5.*, Beta for 3.*, so Beta is the older mod.
"""

from cold_steel.core.conflicts import Found
from cold_steel.core.old_copies import OldCopy, describe, find_old_copies
from conftest import SampleInstall
from test_conflicts import ALPHA, BETA, find, scan, write

NEW = b"alpha_a = { always = yes }\nalpha_b = { always = yes }\nalpha_c = { always = yes }\n"
OLD = b"alpha_a = { always = no }\n"  # alpha_b and alpha_c aren't in it yet
FILE = "common/scripted_triggers/alpha_triggers.txt"


def old_copies(install: SampleInstall, *order: str) -> tuple[OldCopy, ...]:
    found: Found = find(install, *order)
    versions = {m.key: m.supported_version for m in scan(install).every_mod}
    return find_old_copies(found, versions)


def test_an_older_mod_replacing_a_file_and_losing_objects_is_found(
    sample_install: SampleInstall,
) -> None:
    write(sample_install, {ALPHA: {FILE: NEW}, BETA: {FILE: OLD}})

    [copy] = old_copies(sample_install, ALPHA, BETA)

    assert (copy.mod, copy.replaces) == (BETA, ALPHA)
    assert (copy.mod_version, copy.replaces_version) == ("3.*", "v4.5.*")
    assert copy.files == (FILE,)
    assert copy.missing == (
        ("common/scripted_triggers", "alpha_b"),
        ("common/scripted_triggers", "alpha_c"),
    )
    names = {ALPHA: "Alpha", BETA: "Beta"}
    assert describe(copy, names) == (
        "Beta, made for 3.*, replaces 1 file(s) of Alpha (v4.5.*) with older copies. "
        "2 thing(s) Alpha adds are then missing from the game: alpha_b, alpha_c."
    )


def test_an_object_that_only_moved_to_another_file_isnt_missing(
    sample_install: SampleInstall,
) -> None:
    write(
        sample_install,
        {
            ALPHA: {FILE: NEW},
            BETA: {
                FILE: OLD,
                "common/scripted_triggers/beta_triggers.txt": (
                    b"alpha_b = { always = yes }\nalpha_c = { always = yes }\n"
                ),
            },
        },
    )
    assert old_copies(sample_install, ALPHA, BETA) == ()


def test_a_newer_mod_replacing_an_older_ones_file_isnt_flagged(
    sample_install: SampleInstall,
) -> None:
    # Alpha is the newer mod, and loads last: replacing is up to date.
    write(sample_install, {ALPHA: {FILE: OLD}, BETA: {FILE: NEW}})
    assert old_copies(sample_install, BETA, ALPHA) == ()


def test_an_older_mod_that_loses_nothing_isnt_flagged(sample_install: SampleInstall) -> None:
    # Authors often don't update supported_version. If nothing goes missing, it's fine.
    write(sample_install, {ALPHA: {FILE: NEW}, BETA: {FILE: NEW.replace(b"yes", b"no")}})
    assert old_copies(sample_install, ALPHA, BETA) == ()
