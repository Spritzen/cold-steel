"""The scan job, run against the sample install in tests/fixtures/."""

import os
from pathlib import Path

import pytest

from cold_steel.core import mods as mods_module
from cold_steel.core.jobs import Cancelled, JobContext
from cold_steel.core.library import Library, Scanner
from cold_steel.core.mods import read_picture
from cold_steel.paradox.descriptor import Descriptor, decode_descriptor
from conftest import SampleInstall, snapshot


def scan(install: SampleInstall) -> Library:
    return Scanner(*install.scanner_args())(JobContext())


def by_key(library: Library) -> dict[str, object]:
    return {m.key: m for m in library.mods}


def test_lists_workshop_and_local_mods(sample_install: SampleInstall) -> None:
    library = scan(sample_install)

    assert [m.key for m in library.mods] == [
        "workshop:2000000001",  # Alpha Interface
        "workshop:2000000002",  # Beta Ships
        "local:broken",  # Broken Descriptor's file is named broken.mod
        "workshop:2000000003",  # Gamma Soundtrack
        "local:my_local",  # My Local Tweaks
    ]
    alpha = library.mods[0]
    assert alpha.name == "Alpha Interface"
    assert alpha.version == "1.2"
    assert alpha.supported_version == "v4.5.*"
    assert alpha.tags == ("Graphics", "Interface")
    assert alpha.source == "workshop"
    assert alpha.descriptor_file.endswith("mod/ugc_2000000001.mod")


def test_reads_a_mod_from_inside_its_zip(sample_install: SampleInstall) -> None:
    gamma = next(m for m in scan(sample_install).mods if m.name == "Gamma Soundtrack")

    assert gamma.version == "2.0"  # only in the zip's descriptor.mod
    assert gamma.archive.endswith("2000000003/gamma.zip")
    assert gamma.picture_member == "cover.png"
    data = read_picture(gamma)
    assert data is not None and data.startswith(b"\x89PNG")


def test_a_broken_descriptor_is_listed_with_its_problem(sample_install: SampleInstall) -> None:
    broken = next(m for m in scan(sample_install).mods if m.key == "local:broken")
    assert broken.name == "broken"
    assert "never closed" in broken.problem


def test_thumbnails_are_found(sample_install: SampleInstall) -> None:
    library = scan(sample_install)
    pictures = {m.key: Path(m.picture).name for m in library.mods if m.picture}
    assert pictures == {
        "workshop:2000000001": "thumbnail.png",
        "workshop:2000000003": "gamma.zip",
        "local:my_local": "thumbnail.png",
    }


def test_outdated_mods_are_marked(sample_install: SampleInstall) -> None:
    library = scan(sample_install)
    outdated = {m.name for m in library.mods if library.is_outdated(m)}
    assert outdated == {"Beta Ships", "My Local Tweaks"}


def test_launcher_playsets_link_to_mods_in_load_order(sample_install: SampleInstall) -> None:
    library = scan(sample_install)

    main, second = library.launcher_playsets
    assert main.name == "Main Playset"
    assert library.launcher_active == main.id
    assert [(e.key, e.enabled) for e in main.entries] == [
        ("workshop:2000000001", True),
        ("workshop:2000000003", False),
        ("local:my_local", True),
        ("workshop:2000000099", True),
    ]
    # The unsubscribed mod keeps its key and name, so it can be shown as missing.
    assert main.entries[3].name == "Unsubscribed Mod"
    assert [e.key for e in second.entries] == ["workshop:2000000003"]
    assert library.problems == ()


def test_launcher_dlc_ids_become_dlc_folders(sample_install: SampleInstall) -> None:
    library = scan(sample_install)
    assert [d.folder for d in library.dlcs] == ["dlc002_arachnoid", "dlc032_machine_age"]
    assert library.dlcs[0].name == "Arachnoid Portrait Pack"
    assert library.dlcs[0].file == "dlc/dlc002_arachnoid/dlc002.dlc"
    main, second = library.launcher_playsets
    # "arachnoid" has no number; "dlc032_cybernetics" is the launcher's old name.
    assert main.disabled_dlcs == ("dlc002_arachnoid", "dlc032_machine_age")
    assert second.disabled_dlcs == ()


def test_a_bad_launcher_database_still_lists_mods(sample_install: SampleInstall) -> None:
    (sample_install.data_dir / "launcher-v2.sqlite").unlink()
    library = scan(sample_install)
    assert len(library.mods) == 5
    assert library.launcher_playsets == ()
    assert "isn't there" in library.problems[0]


def test_nothing_in_steam_or_paradox_folders_changes(sample_install: SampleInstall) -> None:
    before = snapshot(sample_install.root)
    scan(sample_install)
    scan(sample_install)  # the second run uses the cache
    assert snapshot(sample_install.root) == before
    assert sample_install.cache_file.is_file()


def test_unchanged_mods_come_from_the_cache(
    sample_install: SampleInstall, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = scan(sample_install)

    def no_parsing(data: bytes) -> Descriptor:
        raise AssertionError("an unchanged descriptor was parsed again")

    monkeypatch.setattr(mods_module, "decode_descriptor", no_parsing)
    assert scan(sample_install) == first


def test_a_changed_mod_is_read_again(
    sample_install: SampleInstall, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan(sample_install)
    descriptor = sample_install.workshop_dir / "2000000002/descriptor.mod"
    descriptor.write_text(descriptor.read_text().replace("Beta Ships", "Beta Ships II"))
    os.utime(descriptor, ns=(1, 1))  # a different timestamp, whatever the clock says

    parsed: list[str] = []
    real = decode_descriptor

    def counting(data: bytes) -> Descriptor:
        desc = real(data)
        parsed.append(desc.name)
        return desc

    monkeypatch.setattr(mods_module, "decode_descriptor", counting)
    library = scan(sample_install)
    assert "Beta Ships II" in {m.name for m in library.mods}
    assert parsed == ["Beta Ships II"]


def test_a_touched_but_unchanged_mod_is_not_parsed_again(
    sample_install: SampleInstall, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan(sample_install)
    # Steam often rewrites files with the same content. The hash catches that.
    os.utime(sample_install.workshop_dir / "2000000002/descriptor.mod", ns=(1, 1))

    def no_parsing(data: bytes) -> Descriptor:
        raise AssertionError("a descriptor with the same content was parsed again")

    monkeypatch.setattr(mods_module, "decode_descriptor", no_parsing)
    scan(sample_install)


def test_cancelling_stops_the_scan(sample_install: SampleInstall) -> None:
    ctx = JobContext()
    ctx.cancel()
    with pytest.raises(Cancelled):
        Scanner(*sample_install.scanner_args())(ctx)
