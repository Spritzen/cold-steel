"""The settings, the theme and the first-run screen."""

import json
import shutil
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Scanner
from cold_steel.store.settings import Settings, load_settings
from cold_steel.ui.help import WelcomeDialog, welcome_text
from cold_steel.ui.settings_dialog import SettingsDialog
from cold_steel.ui.theme import DARK_TEXT, apply_theme, soft_text
from conftest import SampleInstall


def test_a_chosen_data_folder_replaces_the_games(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "flatpak/Stellaris"
    shutil.copytree(sample_install.data_dir, elsewhere)
    steam_dirs, cache = sample_install.scanner_args()

    library = Scanner(steam_dirs, cache, elsewhere)(JobContext())

    assert library.game.data_dir == elsewhere
    assert library.launcher_playsets  # read from the chosen folder's launcher database


def test_old_settings_files_still_load(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"steam_dir": "/games/Steam"}))
    assert load_settings(path) == Settings(steam_dir="/games/Steam")
    assert not load_settings(path).welcomed


def test_settings_dialog_gives_back_what_it_shows(qtbot: QtBot) -> None:
    dialog = SettingsDialog(Settings(steam_dir="/old", welcomed=True), Path("/found"))
    qtbot.addWidget(dialog)
    assert "/found" in dialog.data_dir.placeholderText()

    dialog.steam_dir.setText(" /games/Steam ")
    dialog.data_dir.setText("/flatpak/Stellaris")
    dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))

    assert dialog.settings() == Settings(
        steam_dir="/games/Steam",
        game_data_dir="/flatpak/Stellaris",
        theme="dark",
        welcomed=True,
    )


def test_theme_can_be_forced_and_given_back(qapp: QApplication) -> None:
    def window_lightness() -> int:
        return qapp.palette().color(QPalette.ColorRole.Window).lightness()

    system = qapp.palette()
    try:
        apply_theme(qapp, "dark")
        assert window_lightness() < 128
        assert qapp.palette().color(QPalette.ColorRole.Text) == QColor(DARK_TEXT)
        apply_theme(qapp, "light")
        assert window_lightness() > 128
        apply_theme(qapp, "system")
        assert qapp.palette() == system
    finally:
        apply_theme(qapp, "system")


def test_soft_text_leaves_the_desktops_other_colours(qapp: QApplication) -> None:
    over = soft_text()
    desktop = qapp.palette()
    merged = over.resolve(desktop)
    assert merged.color(QPalette.ColorRole.WindowText) == QColor(DARK_TEXT)
    assert merged.color(QPalette.ColorRole.Window) == desktop.color(QPalette.ColorRole.Window)
    # Disabled text keeps the desktop's greyed colour.
    disabled = QPalette.ColorGroup.Disabled
    assert merged.color(disabled, QPalette.ColorRole.Text) == desktop.color(
        disabled, QPalette.ColorRole.Text
    )


def test_welcome_says_what_is_changed(qtbot: QtBot) -> None:
    text = welcome_text()
    for promise in ("dlc_load.json", "backed up", "cold_steel_", "Export to launcher"):
        assert promise in text
    dialog = WelcomeDialog()
    qtbot.addWidget(dialog)
