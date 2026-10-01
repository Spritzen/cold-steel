from pathlib import Path

import pytest

from cold_steel.store import paths


def test_defaults_follow_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)

    assert paths.config_dir() == tmp_path / ".config/cold-steel"
    assert paths.data_dir() == tmp_path / ".local/share/cold-steel"
    assert paths.cache_dir() == tmp_path / ".cache/cold-steel"


def test_env_overrides_and_relative_values_are_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", "/etc/xdg-test")
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/path")

    assert paths.config_dir() == Path("/etc/xdg-test/cold-steel")
    assert paths.cache_dir() == tmp_path / ".cache/cold-steel"
