"""Settings: where Steam and the game's data are, whether Play hides other
playsets' saves, the theme, and where Cold Steel keeps its own files."""

from pathlib import Path

from msgspec.structs import replace
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cold_steel.paradox.game import DEFAULT_STEAM_DIRS
from cold_steel.store import paths
from cold_steel.store.settings import Settings

THEMES = (("Follow the system", "system"), ("Light", "light"), ("Dark", "dark"))

HIDING_ON = (
    "Hiding other playsets' saves: on. Play shows only the playset's own saves, "
    "and saves bound to no playset, in the game's Load menu."
)
HIDING_OFF = {
    True: "Hiding other playsets' saves: off. Stellaris sends autosaves to Steam Cloud, "
    "which Cold Steel can't move.",
    None: "Hiding other playsets' saves: off. Cold Steel couldn't read the game's "
    "settings.txt, so it treats autosaves as going to Steam Cloud, which it can't move.",
}
HOW_TO_HIDE = (
    "To turn it on, turn off cloud autosaves in the game's settings, or set "
    "autosave_tocloud=no in its settings.txt while the game is closed. Autosaves "
    "already in Steam Cloud stay there and still show in the Load menu: delete them "
    "from the Load menu, or leave them."
)


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: Settings,
        found_data_dir: Path | None,
        parent: QWidget | None = None,
        *,
        game_found: bool = False,
        cloud_autosaves: bool | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(720)
        self._settings = settings

        steam_row, self.steam_dir = self._folder_box(
            settings.steam_dir,
            f"{DEFAULT_STEAM_DIRS[0]} (found on its own)",
            "Choose your Steam folder (the one holding steamapps)",
        )
        data_row, self.data_dir = self._folder_box(
            settings.game_data_dir,
            paths.shown(found_data_dir) if found_data_dir else "",
            "Choose Stellaris's data folder (the one holding mod and logs)",
        )
        self.theme = QComboBox()
        for text, value in THEMES:
            self.theme.addItem(text, value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.theme)))

        game = QFormLayout()
        game.addRow("Steam folder:", steam_row)
        game.addRow("", _hint("Only needed when Steam is a Flatpak or Snap, or somewhere unusual."))
        game.addRow("Stellaris data folder:", data_row)
        game.addRow("", _hint("Leave empty to use the folder the game names. Holds mod and logs."))
        self.hiding = QLabel(HIDING_OFF.get(cloud_autosaves, HIDING_ON), wordWrap=True)
        self.hiding_hint = _hint(HOW_TO_HIDE)
        self.hiding_hint.setWordWrap(True)
        if game_found:
            game.addRow("Saves:", self.hiding)
            if cloud_autosaves is not False:
                game.addRow("", self.hiding_hint)
        game_box = QGroupBox("Stellaris")
        game_box.setLayout(game)

        look = QFormLayout()
        look.addRow("Theme:", self.theme)
        look_box = QGroupBox("Appearance")
        look_box.setLayout(look)

        ours = QFormLayout()
        for label, folder in (
            ("Settings:", paths.config_dir()),
            ("Playsets, patches, builds:", paths.data_dir()),
            ("Cache (safe to delete):", paths.cache_dir()),
        ):
            ours.addRow(label, _open_row(folder))
        ours_box = QGroupBox("Cold Steel's own files")
        ours_box.setLayout(ours)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(game_box)
        layout.addWidget(look_box)
        layout.addWidget(ours_box)
        layout.addWidget(buttons)

    def settings(self) -> Settings:
        """The settings as the dialog shows them now."""
        return replace(
            self._settings,
            steam_dir=self.steam_dir.text().strip(),
            game_data_dir=self.data_dir.text().strip(),
            theme=self.theme.currentData(),
        )

    def _folder_box(self, value: str, placeholder: str, title: str) -> tuple[QWidget, QLineEdit]:
        """A path box with a Browse button: the row holding both, and the box."""
        box = QLineEdit(value, placeholderText=placeholder, clearButtonEnabled=True)
        browse = QPushButton("Browse…")

        def choose() -> None:
            start = box.text() or str(Path.home())
            folder = QFileDialog.getExistingDirectory(self, title, start)
            if folder:
                box.setText(folder)

        browse.clicked.connect(choose)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box, 1)
        layout.addWidget(browse)
        return row, box


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setEnabled(False)  # greyed, in the theme's own colour
    return label


def _open_row(folder: Path) -> QWidget:
    path = QLabel(paths.shown(folder))
    path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    open_ = QPushButton("Open")
    open_.setToolTip(f"Open {folder} in the file manager")
    open_.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))
    open_.setEnabled(folder.is_dir())
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(path, 1)
    layout.addWidget(open_)
    return row
