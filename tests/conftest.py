import hashlib
import os
import shutil
import sqlite3
import struct
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

import pytest

# GUI tests run headless unless asked otherwise (`COLD_STEEL_TEST_QPA=wayland make test`).
os.environ["QT_QPA_PLATFORM"] = os.environ.get("COLD_STEEL_TEST_QPA", "offscreen")

FIXTURES = Path(__file__).parent / "fixtures"
WORKSHOP = "games/steamapps/workshop/content/281990"
PARADOX = "home/.local/share/Paradox Interactive/Stellaris"


@dataclass(frozen=True)
class SampleInstall:
    """A small fake Steam + Paradox install, built fresh for each test."""

    root: Path
    home: Path
    steam_dir: Path  # the main Steam folder; Stellaris is in a second library
    data_dir: Path  # Paradox user data for Stellaris
    workshop_dir: Path
    cache_file: Path

    def scanner_args(self) -> tuple[tuple[Path, ...], Path]:
        return (self.steam_dir,), self.cache_file


@pytest.fixture
def sample_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SampleInstall:
    root = tmp_path / "install"
    build_sample_install(root)
    # The game finds its data folder through $LINUX_DATA_HOME, which is ~/.local/share.
    monkeypatch.setenv("HOME", str(root / "home"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return SampleInstall(
        root=root,
        home=root / "home",
        steam_dir=root / "home/.local/share/Steam",
        data_dir=root / PARADOX,
        workshop_dir=root / WORKSHOP,
        cache_file=tmp_path / "cache/cold-steel/mods.msgpack",
    )


def build_sample_install(root: Path) -> None:
    """Copy tests/fixtures/install to `root`, filling in the binary files."""
    shutil.copytree(FIXTURES / "install", root)
    for path in root.rglob("*"):
        if path.suffix in (".vdf", ".mod", ".json", ".acf"):
            path.write_text(path.read_text("utf-8").replace("@ROOT@", str(root)), "utf-8")

    png = make_png(4, 3, (200, 40, 40))
    (root / WORKSHOP / "2000000001/thumbnail.png").write_bytes(png)
    (root / PARADOX / "mod/my_local/thumbnail.png").write_bytes(make_png(2, 2, (40, 200, 40)))

    # A Workshop mod shipped as one zip, with its descriptor and picture inside.
    zipped = root / WORKSHOP / "2000000003"
    zipped.mkdir()
    with zipfile.ZipFile(zipped / "gamma.zip", "w") as zf:
        for file in sorted((FIXTURES / "zipped/2000000003").rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(FIXTURES / "zipped/2000000003").as_posix())
        zf.writestr("cover.png", make_png(3, 3, (40, 40, 200)))

    sql = (FIXTURES / "launcher-v2.sql").read_text("utf-8").replace("@ROOT@", str(root))
    with sqlite3.connect(root / PARADOX / "launcher-v2.sqlite") as conn:
        conn.executescript(sql)
    conn.close()


def make_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A tiny solid-colour PNG, so fixtures needn't hold binary files."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + bytes(rgb) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


type Snapshot = dict[str, tuple[int, int, str]]


def snapshot(root: Path, *, recursive: bool = True) -> Snapshot:
    """Size, timestamp and content hash of every file under `root`."""
    files = root.rglob("*") if recursive else root.iterdir()
    result: Snapshot = {}
    for path in files:
        if path.is_file() and not path.is_symlink():
            st = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns, digest)
    return result
