"""Health checks, each run against a small broken sample mod."""

import os
import zipfile
from pathlib import Path

import pytest

from cold_steel.core import health as health_module
from cold_steel.core.health import (
    BOM,
    MAX_LINES_PER_FILE,
    HealthChecker,
    Issue,
    check_descriptor,
    check_files,
    status,
)
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Scanner
from cold_steel.core.mods import Mod, _walk
from cold_steel.paradox.game import DEFAULT_STEAM_DIRS, GameNotFound, find_game
from conftest import SampleInstall

GAME = "v4.5.1"

# A mod with one of each problem, plus files that must not be flagged.
BROKEN_MOD = {
    # No BOM: the game ignores the whole file.
    "localisation/english/no_bom_l_english.yml": b'l_english:\n KEY:0 "text"\n',
    "localisation/english/lines_l_english.yml": BOM
    + b"l_english:\n"
    + b' GOOD:0 "has a version"\n'
    + b' ALSO_GOOD: "no version, like the game\'s own files" # and a comment\n'
    + b"\n # a comment line\n"
    + b' NO_COLON "text"\n'  # line 6
    + b" NO_QUOTES:0 text\n",  # line 7
    "localisation/english/no_language_l_english.yml": BOM + b' KEY:0 "text"\n',
    # A stray "}" on line 4. Braces in strings and comments don't count.
    "common/traits/stray.txt": b'a = {\n\tname = "}"  # }\n}\n}\nb = { }\n',
    # A "{" opened on line 2 is never closed.
    "events/unclosed.txt": b"namespace = x\nevent = {\n\tid = x.1\n",
    "gfx/fine.gfx": b'objectTypes = { pdxmesh = { name = "{" } } # {\n',
    # Not script: a readme, and a .txt outside the game's folders.
    "readme.txt": b"}}} not script\n",
    "docs/notes.txt": b"{{{ not script either\n",
    "descriptor.mod": b'name="Broken"\n',
}


def write_mod(folder: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)


def folder_mod(tmp_path: Path, files: dict[str, bytes] = BROKEN_MOD, **fields: str) -> Mod:
    root = tmp_path / "broken"
    write_mod(root, files)
    args = {"supported_version": "v4.5.*", **fields}
    return Mod(key="local:broken", source="local", name="Broken", root=str(root), **args)  # type: ignore[arg-type]


def file_issues(mod: Mod) -> dict[str, list[Issue]]:
    found: dict[str, list[Issue]] = {}
    for issue in check_files(mod, _walk(Path(mod.root))):
        found.setdefault(issue.file, []).append(issue)
    return found


def test_localisation_without_a_bom_is_an_error(tmp_path: Path) -> None:
    [issue] = file_issues(folder_mod(tmp_path))["localisation/english/no_bom_l_english.yml"]
    assert issue.severity == "error"
    assert "ignores this whole file" in issue.text


def test_localisation_lines_that_arent_key_text(tmp_path: Path) -> None:
    issues = file_issues(folder_mod(tmp_path))["localisation/english/lines_l_english.yml"]
    # A missing :0 and a trailing comment are fine: the game's own files do both.
    assert [(i.severity, i.line, i.detail) for i in issues] == [
        ("error", 6, 'NO_COLON "text"'),
        ("error", 7, "NO_QUOTES:0 text"),
    ]
    assert "skips it" in issues[0].text


def test_localisation_needs_a_language_line(tmp_path: Path) -> None:
    [issue] = file_issues(folder_mod(tmp_path))["localisation/english/no_language_l_english.yml"]
    assert issue.line == 1
    assert '"l_english:"' in issue.text


def test_many_bad_lines_are_summed_up(tmp_path: Path) -> None:
    lines = b"".join(b"BAD LINE\n" for _ in range(MAX_LINES_PER_FILE + 5))
    mod = folder_mod(tmp_path, {"localisation/x_l_english.yml": BOM + b"l_english:\n" + lines})
    issues = file_issues(mod)["localisation/x_l_english.yml"]
    assert len(issues) == MAX_LINES_PER_FILE + 1
    assert issues[-1].text == "And 5 more lines like that."


def test_a_stray_closing_brace_is_an_error(tmp_path: Path) -> None:
    [issue] = file_issues(folder_mod(tmp_path))["common/traits/stray.txt"]
    assert (issue.severity, issue.line) == ("error", 4)
    assert "ignores the rest" in issue.text


def test_an_unclosed_brace_is_a_warning(tmp_path: Path) -> None:
    [issue] = file_issues(folder_mod(tmp_path))["events/unclosed.txt"]
    assert (issue.severity, issue.line) == ("warning", 2)


def test_only_game_files_are_checked(tmp_path: Path) -> None:
    found = file_issues(folder_mod(tmp_path))
    assert set(found) == {
        "localisation/english/no_bom_l_english.yml",
        "localisation/english/lines_l_english.yml",
        "localisation/english/no_language_l_english.yml",
        "common/traits/stray.txt",
        "events/unclosed.txt",
    }


def test_a_zipped_mod_is_checked_inside_its_zip(tmp_path: Path) -> None:
    archive = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in BROKEN_MOD.items():
            zf.writestr(name, data)
    mod = Mod(key="local:zipped", source="local", name="Zipped", archive=str(archive))
    st = archive.stat()
    issues = check_files(mod, {"broken.zip": (st.st_size, st.st_mtime_ns)})
    assert {i.file for i in issues} == set(file_issues(folder_mod(tmp_path)))


# Descriptors


def descriptor_texts(mod: Mod) -> list[tuple[str, str]]:
    return [(i.severity, i.text) for i in check_descriptor(mod, GAME)]


@pytest.mark.parametrize("version", ["v4.5.*", "v4.5.1", "4.5.*"])
def test_a_good_descriptor_has_no_problems(tmp_path: Path, version: str) -> None:
    assert descriptor_texts(folder_mod(tmp_path, supported_version=version)) == []


@pytest.mark.parametrize("version", ["3.*", "v4.*.*", "v4.**.*"])
def test_supported_versions_the_game_calls_invalid(tmp_path: Path, version: str) -> None:
    texts = descriptor_texts(folder_mod(tmp_path, supported_version=version))
    assert ("warning", f'The game can\'t read supported_version "{version}" and logs it as '
            'invalid. It should look like "v4.5.*".') in texts  # fmt: skip


def test_an_old_supported_version_is_outdated(tmp_path: Path) -> None:
    [(severity, text)] = descriptor_texts(folder_mod(tmp_path, supported_version="v4.4.*"))
    assert severity == "warning"
    assert "marks it outdated" in text


def test_a_missing_supported_version(tmp_path: Path) -> None:
    [(severity, text)] = descriptor_texts(folder_mod(tmp_path, supported_version=""))
    assert severity == "warning"
    assert text.startswith("No supported_version")


def test_descriptor_errors(tmp_path: Path) -> None:
    unnamed = Mod(key="local:x", source="local", name="x", named=False, root=str(tmp_path))
    gone = Mod(key="local:y", source="local", name="y", root=str(tmp_path / "gone"))
    nowhere = Mod(key="local:z", source="local", name="z")
    broken = Mod(key="local:b", source="local", name="b", problem="broken.mod is broken (x)")

    assert "has no name" in check_descriptor(unnamed, GAME)[0].text
    assert "which doesn't exist" in check_descriptor(gone, GAME)[0].text
    assert "no path or archive" in check_descriptor(nowhere, GAME)[0].text
    # A descriptor that can't be read gives one error, not one per missing field.
    [issue] = check_descriptor(broken, GAME)
    assert issue.severity == "error" and issue.text.startswith("broken.mod is broken")


def test_status() -> None:
    warning = Issue("warning", "w")
    error = Issue("error", "e")
    assert status(()) == "ok"
    assert status((warning,)) == "warning"
    assert status((warning, error)) == "error"


# The job, and its cache


def test_unchanged_mods_are_not_checked_again(
    sample_install: SampleInstall, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scanner = Scanner(*sample_install.scanner_args())
    cache = tmp_path / "health.msgpack"
    checked: list[str] = []
    real_check = health_module.check_files

    def counting(mod: Mod, *args: object) -> tuple[Issue, ...]:
        checked.append(mod.key)
        return real_check(mod, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(health_module, "check_files", counting)

    first = HealthChecker(scanner(JobContext()), cache)(JobContext())
    assert len(checked) == 5
    assert status(first["local:broken"]) == "error"  # its descriptor can't be read
    assert status(first["workshop:2000000001"]) == "ok"

    checked.clear()
    assert HealthChecker(scanner(JobContext()), cache)(JobContext()) == first
    assert checked == []

    # Break one file in Alpha Interface: only Alpha is read again.
    alpha = sample_install.workshop_dir / "2000000001/common/alpha.txt"
    alpha.write_text("alpha_value = 1\n}\n", "utf-8")
    os.utime(alpha, ns=(1, 1))
    third = HealthChecker(scanner(JobContext()), cache)(JobContext())
    assert checked == ["workshop:2000000001"]
    assert [(i.file, i.line) for i in third["workshop:2000000001"]] == [("common/alpha.txt", 2)]


@pytest.mark.real_install
def test_the_games_own_files_pass(tmp_path: Path) -> None:
    """The checks must not flag vanilla. Only the two files Paradox ships with a
    missing last "}" are warned about, and the game logs nothing for those."""
    steam_dirs = (
        tuple([Path(os.environ["STEAM_DIR"])] if "STEAM_DIR" in os.environ else [])
        + DEFAULT_STEAM_DIRS
    )
    try:
        game = find_game(steam_dirs)
    except GameNotFound:
        pytest.skip("Stellaris isn't installed here")
    vanilla = Mod(key="local:vanilla", source="local", name="Vanilla", root=str(game.install_dir))
    issues = check_files(vanilla, _walk(game.install_dir))
    assert all(i.severity == "warning" for i in issues)
    assert len(issues) <= 2
