"""The Help menu's windows: the first-run screen, the keyboard shortcuts, About."""

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QMenu,
    QMenuBar,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from cold_steel import __version__
from cold_steel.store import paths
from cold_steel.ui.full_text import show_full_text


def welcome_text() -> str:
    return f"""
<h2>Welcome to Cold Steel</h2>
<p>Cold Steel manages your Stellaris mods and playsets. Before you start, here is
exactly what it reads and what it changes.</p>

<h3>It only reads</h3>
<ul>
<li><b>Steam's folders:</b> the game and your Workshop mods. Cold Steel never
changes them, and never downloads or subscribes to mods.</li>
<li><b>Your launcher playsets.</b> The first time it opens, Cold Steel copies them
into its own list. After that the launcher only changes when you press
<b>Export to launcher</b>.</li>
</ul>

<h3>It changes Paradox's files only when you ask</h3>
<ul>
<li><b>Play</b> writes <code>dlc_load.json</code>, which tells the game what to load. For a
mod the launcher hasn't seen yet, it also adds the <code>.mod</code> file the game needs.</li>
<li><b>Export to launcher</b> and <b>Open in launcher</b> write the playset into the
launcher's database.</li>
<li><b>Generate patch mod</b>, <b>Build</b> and <b>Pins</b> add their own mods to the
game's <code>mod</code> folder. Their names start with <code>cold_steel_</code>.</li>
</ul>
<p>Every Paradox file is backed up before it changes, and nothing is written while
the launcher or the game is open.</p>

<h3>Its own files</h3>
<ul>
<li><code>{paths.shown(paths.config_dir())}</code>: settings</li>
<li><code>{paths.shown(paths.data_dir())}</code>: playsets, conflict choices, patch mods, pinned
copies, builds and backups</li>
<li><code>{paths.shown(paths.cache_dir())}</code>: what it remembers about your mods.
Safe to delete</li>
</ul>
<p>You can read this again from <b>Help &rsaquo; What Cold Steel changes</b>.</p>
"""


ABOUT = f"""
<h3>Cold Steel {__version__}</h3>
<p>A native Linux mod manager for Stellaris.</p>
<p><a href="https://github.com/Spritzen/cold-steel">github.com/Spritzen/cold-steel</a></p>
<p>MIT licence. Inspired by
<a href="https://github.com/bcssov/IronyModManager">IronyModManager</a>.</p>
"""


class WelcomeDialog(QDialog):
    """What Cold Steel reads and changes. Shown once, the first time it opens."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Cold Steel")
        self.resize(660, 520)
        # Scrolls, so the whole text can be read on a small screen.
        text = QTextBrowser()
        text.setHtml(welcome_text())
        text.setFrameShape(QTextBrowser.Shape.NoFrame)
        text.viewport().setAutoFillBackground(False)  # the dialog's own background
        buttons = QDialogButtonBox()
        buttons.addButton("Get started", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(buttons)


def shortcut_rows(menus: QMenuBar) -> list[tuple[str, str, str]]:
    """(menu, action, shortcut) for every menu action that has a shortcut, in menu order."""
    rows = []
    for top in menus.actions():
        menu = top.menu()
        if not isinstance(menu, QMenu):
            continue
        for act in menu.actions():
            if not act.shortcut().isEmpty():
                rows.append(
                    (
                        _plain(top.text()),
                        _plain(act.text()),
                        act.shortcut().toString(QKeySequence.SequenceFormat.NativeText),
                    )
                )
    return rows


class ShortcutsDialog(QDialog):
    def __init__(self, menus: QMenuBar, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        rows = shortcut_rows(menus)
        self.table = QTableWidget(len(rows), 3)
        show_full_text(self.table)
        self.table.setHorizontalHeaderLabels(["Menu", "Action", "Shortcut"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 8)
        self.table.resizeColumnsToContents()
        header = self.table.horizontalHeader()
        # Wide enough for every column, without a sideways scroll bar.
        self.table.setMinimumWidth(
            sum(self.table.columnWidth(c) for c in range(3))
            + 2 * self.table.frameWidth()
            + self.table.verticalScrollBar().sizeHint().width()
            + 24
        )
        header.setStretchLastSection(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(buttons)
        self.resize(0, 560)


def _plain(text: str) -> str:
    """A menu text without its & marker or trailing …"""
    return text.replace("&", "").rstrip("…")
