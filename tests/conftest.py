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
# Steam Cloud's save folders: the last Steam user's (account 1000), and another's.
CLOUD_SAVES = "home/.local/share/Steam/userdata/1000/281990/remote/save games"
OTHER_USER_SAVES = "home/.local/share/Steam/userdata/2000/281990/remote/save games"
# When the oldest fixture save was written; each later one is an hour after.
SAVED_FROM = 1_791_000_000


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

    build_saves(root)

    sql = (FIXTURES / "launcher-v2.sql").read_text("utf-8").replace("@ROOT@", str(root))
    with sqlite3.connect(root / PARADOX / "launcher-v2.sqlite") as conn:
        conn.executescript(sql)
    conn.close()


def build_saves(root: Path) -> None:
    """Zip each tests/fixtures/saves/**/*.meta into a .sav in its save folder.
    They're dated by name, an hour apart, so a later in-game date is a later save."""
    places = {
        "local": root / PARADOX / "save games",
        "cloud": root / CLOUD_SAVES,
        "other-user": root / OTHER_USER_SAVES,
    }
    metas = sorted((FIXTURES / "saves").rglob("*.meta"), key=lambda p: p.stem.split("_")[-1])
    for hour, meta in enumerate(metas):
        place = meta.relative_to(FIXTURES / "saves").parts[0]
        target = places[place] / meta.parent.name / f"{meta.stem}.sav"
        make_save(target, meta.read_text("utf-8"))
        os.utime(target, ns=((SAVED_FROM + hour * 3600) * 10**9,) * 2)


def make_save(path: Path, meta: str) -> None:
    """A .sav as the game writes it: a zip of `gamestate`, then `meta`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("gamestate", 'date="2200.01.01"\n')
        zf.writestr("meta", meta)


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


def empire_block(name: str, planet: str = "pc_desert", extra: str = "") -> bytes:
    """One empire as the game writes it: tabs and CRLF line breaks."""
    text = (
        f'"{name}"=\n{{\n\tkey="{name}"\n'
        '\tspecies=\n\t{\n\t\tclass="HUM"\n\t\tspecies_name=\n\t\t{\n\t\t\tkey="Elf"\n'
        '\t\t\tliteral=yes\n\t\t}\n\t\tname_list="HUMAN3"\n\t\ttrait="trait_venerable"\n\t}\n'
        '\tauthority="auth_imperial"\n\tgovernment="gov_theocratic_monarchy"\n'
        f'\tplanet_class="{planet}"\n\tcity_graphical_culture="humanoid_01"\n'
        '\truler=\n\t{\n\t\ttrait="leader_trait_spark_of_genius"\n\t}\n'
        '\tethic="ethic_militarist"\n\tethic="ethic_fanatic_spiritualist"\n'
        '\tcivics=\n\t{\n\t\t"civic_ascensionists"\n\t\t"civic_chosen"\n\t}\n'
        f'\torigin="origin_life_seeded"\n{extra}}}\n'
    )
    return text.replace("\n", "\r\n").encode()


def write_empires(data_dir: Path, *blocks: bytes) -> Path:
    """The game's empire file, holding these empires."""
    path = data_dir / "user_empire_designs_v3.4.txt"
    path.write_bytes(b"".join(blocks))
    return path


def add_empire_definitions(game_dir: Path) -> None:
    """The game's own definitions of what empire_block uses, but for
    pc_continental, which test mods add."""
    files = {
        "common/species_classes/00_species_classes.txt": b"HUM = { }\n",
        "common/name_lists/HUMAN3.txt": b"HUMAN3 = { }\n",
        "common/traits/00_traits.txt": (
            b"trait_venerable = { }\nleader_trait_spark_of_genius = { }\n"
        ),
        "common/governments/authorities/00_authorities.txt": b"auth_imperial = { }\n",
        "common/governments/00_governments.txt": b"gov_theocratic_monarchy = { }\n",
        "common/governments/civics/00_civics.txt": (
            b"civic_ascensionists = { }\ncivic_chosen = { }\norigin_life_seeded = { }\n"
        ),
        "common/ethics/00_ethics.txt": (
            b"ethic_militarist = { }\nethic_fanatic_spiritualist = { }\n"
        ),
        "common/planet_classes/00_planet_classes.txt": b"pc_desert = { }\n",
        "common/graphical_culture/00_graphical_culture.txt": b"humanoid_01 = { }\n",
    }
    for name, data in files.items():
        (game_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (game_dir / name).write_bytes(data)
