from pathlib import Path

from cold_steel.core.local_mods import local_files
from cold_steel.core.mods import Mod


def local(mod_dir: Path, stem: str, root: Path | str = "", archive: str = "") -> Mod:
    return Mod(
        key=f"local:{stem}",
        source="local",
        name=stem,
        root=str(root),
        archive=archive,
        descriptor_file=str(mod_dir / f"{stem}.mod"),
    )


def test_a_folder_in_the_mod_folder_goes_with_its_mod_file(tmp_path: Path) -> None:
    (tmp_path / "mine").mkdir()
    files = local_files(local(tmp_path, "mine", tmp_path / "mine"), tmp_path)
    assert files is not None
    assert files.paths == (tmp_path / "mine.mod", tmp_path / "mine")
    assert not files.link and files.kept is None


def test_a_link_goes_but_not_what_it_points_at(tmp_path: Path) -> None:
    mod_dir, project = tmp_path / "mod", tmp_path / "project"
    mod_dir.mkdir()
    project.mkdir()
    (mod_dir / "mine").symlink_to(project)
    files = local_files(local(mod_dir, "mine", mod_dir / "mine"), mod_dir)
    assert files is not None
    assert files.content == mod_dir / "mine" and files.link


def test_a_folder_outside_the_mod_folder_stays(tmp_path: Path) -> None:
    mod_dir = tmp_path / "mod"
    files = local_files(local(mod_dir, "mine", tmp_path / "project"), mod_dir)
    assert files is not None
    assert files.paths == (mod_dir / "mine.mod",)
    assert files.kept == tmp_path / "project"


def test_only_local_mods_that_arent_ours(tmp_path: Path) -> None:
    workshop = Mod(key="workshop:1", source="workshop", name="w", root=str(tmp_path / "1"))
    assert local_files(workshop, tmp_path) is None
    patch = local(tmp_path, "cold_steel_patch_x", tmp_path / "cold_steel_patch_x")
    assert local_files(patch, tmp_path) is None
    gone = Mod(key="local:gone", source="local", name="gone", installed=False)
    assert local_files(gone, tmp_path) is None
