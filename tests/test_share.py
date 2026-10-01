"""Playset files: ours (the launcher's backup shape) and Irony's exports."""

import json
import zipfile
from pathlib import Path

import pytest

from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library, Scanner
from cold_steel.core.share import ShareError, load_share_file, save_share_file
from cold_steel.store.playsets import Playset, PlaysetEntry
from conftest import SampleInstall


@pytest.fixture
def library(sample_install: SampleInstall) -> Library:
    return Scanner(*sample_install.scanner_args())(JobContext())


def test_save_and_load_round_trip(tmp_path: Path, library: Library) -> None:
    playset = Playset(
        id="p",
        name="Shared",
        entries=(
            PlaysetEntry("workshop:2000000003", False, "Gamma Soundtrack"),
            PlaysetEntry("local:my_local", True, "My Local Tweaks"),
            PlaysetEntry("workshop:2000000001", True, "Alpha Interface"),
        ),
    )
    path = tmp_path / "shared.json"
    assert save_share_file(playset, library, path) == ("My Local Tweaks",)

    data = json.loads(path.read_text())
    assert data["game"] == "stellaris"
    assert data["mods"][1] == {
        "displayName": "Alpha Interface",
        "enabled": True,
        "position": 1,
        "steamId": "2000000001",
    }
    loaded = load_share_file(path).playset
    assert loaded.name == "Shared" and loaded.id != "p"
    assert [(e.key, e.enabled) for e in loaded.entries] == [
        ("workshop:2000000003", False),
        ("workshop:2000000001", True),
    ]


def test_reads_the_launchers_backup_files(tmp_path: Path) -> None:
    # The shape of Paradox's playsets_backup/*.json, positions out of order.
    path = tmp_path / "launcher.json"
    path.write_text(
        json.dumps(
            {
                "game": "stellaris",
                "name": "From the launcher",
                "mods": [
                    {"displayName": "B", "enabled": True, "position": 1, "steamId": "222"},
                    {"displayName": "A", "enabled": False, "position": 0, "steamId": "111"},
                    {"displayName": "Paradox Mods only", "enabled": True, "position": 2},
                ],
            }
        )
    )
    shared = load_share_file(path)
    assert [(e.key, e.name, e.enabled) for e in shared.playset.entries] == [
        ("workshop:111", "A", False),
        ("workshop:222", "B", True),
    ]
    assert shared.left_out == ("Paradox Mods only",)


def test_reads_an_irony_export(tmp_path: Path) -> None:
    path = tmp_path / "irony.zip"
    collection = {
        "Name": "Irony collection",
        "Mods": ["mod/ugc_333.mod", "mod/my_local.mod"],
        "ModNames": ["Three", "Mine"],
        "ModIds": [{"SteamId": 333}, {}],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("exported.json", json.dumps(collection))
    playset = load_share_file(path).playset
    assert playset.name == "Irony collection"
    assert [(e.key, e.name) for e in playset.entries] == [
        ("workshop:333", "Three"),
        ("local:my_local", "Mine"),
    ]


@pytest.mark.parametrize(
    ("content", "message"),
    [("not json", "Couldn't read"), ("[1, 2]", "isn't a playset"), ('{"name": "x"}', "no list")],
)
def test_bad_files_are_clear_errors(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(ShareError, match=message):
        load_share_file(path)


def test_a_zip_without_irony_data_is_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "other.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "hello")
    with pytest.raises(ShareError, match="not one Irony exported"):
        load_share_file(path)
