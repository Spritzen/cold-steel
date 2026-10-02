"""The Arch package files agree with the code."""

import re
from configparser import ConfigParser
from pathlib import Path

from cold_steel import __version__

ROOT = Path(__file__).parent.parent
PACKAGING = ROOT / "packaging"


def test_pkgbuild_version_matches_the_app() -> None:
    pkgbuild = (PACKAGING / "PKGBUILD").read_text("utf-8")
    found = re.search(r"^pkgver=(\S+)$", pkgbuild, re.MULTILINE)
    assert found is not None
    assert found.group(1) == __version__


def test_desktop_entry_starts_the_installed_program() -> None:
    entry = ConfigParser(interpolation=None)
    entry.optionxform = str  # type: ignore[assignment,method-assign]  # keys are case-sensitive
    entry.read(PACKAGING / "cold-steel.desktop", "utf-8")
    desktop = entry["Desktop Entry"]
    assert desktop["Exec"] == "cold-steel"
    assert desktop["Icon"] == "cold-steel"
    assert (ROOT / "src/cold_steel/data/cold-steel.svg").is_file()
