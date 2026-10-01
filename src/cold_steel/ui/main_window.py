"""The main Cold Steel window: playsets on the left, the mod list on the right."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from msgspec.structs import replace as msgspec_replace
from PySide6.QtCore import QModelIndex, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QFont, QImage, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core import playsets as ops
from cold_steel.core.conflicts import ConflictFinder, Found
from cold_steel.core.errors import ErrorReader, ErrorReport
from cold_steel.core.health import Health, HealthChecker, status
from cold_steel.core.index import Index, Indexer
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.load_order import sort_playset
from cold_steel.core.mods import Mod
from cold_steel.core.patch import (
    PatchError,
    PatchPlan,
    patch_key,
    patches_dir,
    plan_patch,
    remove_patch,
    with_patch_last,
    write_patch,
)
from cold_steel.core.play import PlayError, PlayPlan, plan_play, play
from cold_steel.core.playsets import PlaysetBook, missing_mods
from cold_steel.core.resolve import ResolutionBook, copy_resolutions
from cold_steel.core.share import ShareError, load_share_file, save_share_file
from cold_steel.core.sync import SyncError, export_playset, import_playset
from cold_steel.paradox.game import Game, GameNotFound
from cold_steel.paradox.launcher_db import LauncherDbError
from cold_steel.store import paths
from cold_steel.store.playsets import Playset, playsets_file
from cold_steel.store.resolutions import resolutions_dir, resolutions_file
from cold_steel.ui.conflicts_window import ConflictsWindow
from cold_steel.ui.dlc_dialog import DlcDialog
from cold_steel.ui.errors_dialog import ErrorsDialog
from cold_steel.ui.health_dialog import HealthDialog
from cold_steel.ui.mod_table import MOD_ROLE, Column, Membership, ModFilter, ModTableModel
from cold_steel.ui.tasks import Task, TaskRunner
from cold_steel.ui.thumbnails import SHOWN_SIZE, blank_thumbnail, thumbnail_job

type Scan = Callable[[JobContext], Library]
type Launch = Callable[[PlayPlan, Game, Path], Any]

ROW_HEIGHT = SHOWN_SIZE.height() + 4


class MainWindow(QMainWindow):
    # The user picked a Steam folder by hand. The app saves it and rescans.
    steam_dir_chosen = Signal(Path)
    # A scan finished and its result is on screen. For tests and timing.
    library_shown = Signal(object)
    # Every mod's health is checked and shown. For tests.
    health_shown = Signal(object)
    # error.log was read and grouped by mod. For tests.
    errors_read = Signal(object)
    # A playset's conflicts are on screen in the Conflicts window. For tests.
    conflicts_shown = Signal(object)
    # The patch mod was written: its PatchPlan. For tests.
    patch_generated = Signal(object)

    def __init__(
        self,
        scan: Scan | None = None,
        thumbnail_cache: Path | None = None,
        parent: QWidget | None = None,
        *,
        playsets_path: Path | None = None,
        backup_dir: Path | None = None,
        health_cache: Path | None = None,
        index_cache: Path | None = None,
        choices_dir: Path | None = None,
        patch_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cold Steel")
        self.resize(1200, 760)

        self.tasks = TaskRunner(self)
        self.library: Library | None = None
        self._scan = scan
        self._scan_task: Task | None = None
        self._thumbnail_task: Task | None = None
        self._health_task: Task | None = None
        self._health_cache = health_cache or paths.cache_dir() / "health.msgpack"
        self._errors_dialog: ErrorsDialog | None = None
        self._index_cache = index_cache or paths.cache_dir() / "index"
        # Kept between conflict scans, so unchanged mods aren't even loaded from the cache again.
        self._index: Index | None = None
        self._conflicts_window: ConflictsWindow | None = None
        self._conflicts_task: Task | None = None
        self._choices_dir = choices_dir or resolutions_dir()
        self._patch_dir = patch_dir or patches_dir()
        self._choices: ResolutionBook | None = None  # the selected playset's
        self._patch_task: Task | None = None
        # The game we started, watched so its errors can be read when it closes.
        self._game: Any = None
        self._game_timer = QTimer(self, interval=3000)
        self._game_timer.timeout.connect(self._check_game)
        self._thumbnail_cache = thumbnail_cache or paths.cache_dir() / "thumbnails"
        self._playsets_path = playsets_path or playsets_file()
        self.backup_dir = backup_dir or paths.data_dir() / "backups"
        self.book: PlaysetBook | None = None
        # Writes dlc_load.json and starts the game. Tests swap in a stand-in.
        self.launch: Launch = play

        self.model = ModTableModel()
        self.filter = ModFilter(self.model)
        self.model.enabled_toggled.connect(
            lambda key, on: self._edit(lambda p: ops.set_enabled(p, [key], on))
        )
        self.filter.move_requested.connect(
            lambda keys, before: self._edit(lambda p: ops.move_mods(p, keys, before or None))
        )

        self._build_menu()
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_library_page())
        self.pages.addWidget(self._build_message_page())
        self.setCentralWidget(self.pages)
        self._build_status_bar()

        if scan is not None:
            QTimer.singleShot(0, self.rescan)

    # Building the window

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&File")
        rescan = QAction(
            "&Rescan mods", self, shortcut=QKeySequence(QKeySequence.StandardKey.Refresh)
        )
        rescan.triggered.connect(self.rescan)
        steam = QAction("Set &Steam folder…", self)
        steam.triggered.connect(self.choose_steam_dir)
        quit_ = QAction("&Quit", self, shortcut=QKeySequence(QKeySequence.StandardKey.Quit))
        quit_.triggered.connect(self.close)
        menu.addActions([rescan, steam])
        menu.addSeparator()
        menu.addAction(quit_)

        def action(text: str, slot: Callable[[], object], shortcut: str = "") -> QAction:
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            return act

        self.new_action = action("&New playset…", self.new_playset, "Ctrl+N")
        self.copy_action = action("&Copy playset…", self.copy_playset)
        self.rename_action = action("&Rename playset…", self.rename_playset, "F2")
        self.delete_action = action("&Delete playset", self.delete_playset)
        self.sort_action = action("&Sort load order", self.sort_mods)
        self.dlc_action = action("&DLC…", self.choose_dlc)
        self.play_action = action("&Play", self.play, "Ctrl+Return")
        self.errors_action = action("&Errors from the last game…", self.show_errors, "Ctrl+E")
        self.errors_action.setEnabled(False)
        self.conflicts_action = action("&Conflicts…", self.show_conflicts, "Ctrl+K")
        self.export_action = action("&Export to launcher", self.export_to_launcher)
        self.save_file_action = action("&Save to file…", self.save_to_file)
        self.load_file_action = action("&Load from file…", self.load_from_file)
        self.import_menu = QMenu("&Import from launcher", self)
        self.import_menu.aboutToShow.connect(self._fill_import_menu)

        menu = self.menuBar().addMenu("&Playset")
        menu.addActions([self.new_action, self.copy_action, self.rename_action])
        menu.addAction(self.delete_action)
        menu.addSeparator()
        menu.addActions([self.play_action, self.errors_action, self.conflicts_action])
        menu.addActions([self.sort_action, self.dlc_action])
        menu.addSeparator()
        menu.addMenu(self.import_menu)
        menu.addAction(self.export_action)
        menu.addSeparator()
        menu.addActions([self.load_file_action, self.save_file_action])

    def _build_library_page(self) -> QWidget:
        self.playset_list = QListWidget()
        self.playset_list.currentRowChanged.connect(self._playset_selected)
        self.playset_list.itemDoubleClicked.connect(lambda _: self.rename_playset())

        sidebar_buttons = QHBoxLayout()
        for text, act in (("New", self.new_action), ("Copy", self.copy_action)):
            sidebar_buttons.addWidget(self._button(text, act))
        sidebar_buttons.addWidget(self._button("Rename", self.rename_action))
        sidebar_buttons.addWidget(self._button("Delete", self.delete_action))
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.addWidget(self.playset_list)
        sidebar_layout.addLayout(sidebar_buttons)

        self.search = QLineEdit(placeholderText="Search mods", clearButtonEnabled=True)
        self.search.textChanged.connect(self.filter.set_text)
        self.tag_box = QComboBox()
        self.tag_box.currentIndexChanged.connect(
            lambda: self.filter.set_tag(self.tag_box.currentData() or "")
        )
        self.membership_box = QComboBox()
        self.membership_box.addItem("In any playset or none", Membership.ANY)
        self.membership_box.addItem("In a playset", Membership.IN_A_PLAYSET)
        self.membership_box.addItem("In no playset", Membership.IN_NO_PLAYSET)
        self.membership_box.currentIndexChanged.connect(
            lambda: self.filter.set_membership(self.membership_box.currentData())
        )
        self.outdated_box = QCheckBox("Outdated only")
        self.outdated_box.toggled.connect(self.filter.set_outdated_only)
        self.problems_box = QCheckBox("Problems only")
        self.problems_box.setToolTip("Mods whose health check found errors or warnings")
        self.problems_box.toggled.connect(self.filter.set_problems_only)

        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.tag_box)
        filters.addWidget(self.membership_box)
        filters.addWidget(self.outdated_box)
        filters.addWidget(self.problems_box)

        self.play_button = self._button("▶  Play", self.play_action)
        font = self.play_button.font()
        font.setBold(True)
        self.play_button.setFont(font)
        self.playset_bar = QWidget()
        playset_bar = QHBoxLayout(self.playset_bar)
        playset_bar.setContentsMargins(0, 0, 0, 0)
        playset_bar.addWidget(self.play_button)
        self.errors_button = self._button("Errors", self.errors_action)
        playset_bar.addWidget(self.errors_button)
        playset_bar.addWidget(self._button("Conflicts", self.conflicts_action))
        playset_bar.addWidget(self._button("Sort", self.sort_action))
        playset_bar.addWidget(self._button("DLC…", self.dlc_action))
        playset_bar.addStretch()
        playset_bar.addWidget(self._button("Export to launcher", self.export_action))

        self.problems_label = QLabel(wordWrap=True)
        self.problems_label.setStyleSheet("color: #d9534f;")
        self.problems_label.hide()
        self.missing_label = QLabel(wordWrap=True)
        self.missing_label.setStyleSheet("color: #d9534f;")
        self.missing_label.hide()

        self.table = QTableView()
        self.table.setModel(self.filter)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setIconSize(SHOWN_SIZE)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.table.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.table.setDropIndicatorShown(True)
        self.table.setDragDropOverwriteMode(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.clicked.connect(self._cell_clicked)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(Column.NAME, QHeaderView.ResizeMode.Stretch)
        for col in (
            Column.POSITION,
            Column.HEALTH,
            Column.VERSION,
            Column.SUPPORTED,
            Column.SOURCE,
        ):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.resizeSection(Column.TAGS, 240)

        right = QWidget()
        layout = QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.playset_bar)
        layout.addLayout(filters)
        layout.addWidget(self.problems_label)
        layout.addWidget(self.missing_label)
        layout.addWidget(self.table)

        splitter = QSplitter()
        splitter.addWidget(sidebar)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 940])
        return splitter

    @staticmethod
    def _button(text: str, action: QAction) -> QPushButton:
        """A button that runs `action` and is enabled whenever it is."""
        button = QPushButton(text)
        button.setToolTip(action.text().replace("&", ""))
        button.clicked.connect(action.trigger)
        action.enabledChanged.connect(button.setEnabled)
        button.setEnabled(action.isEnabled())
        return button

    def _build_message_page(self) -> QWidget:
        self.message = QLabel(wordWrap=True, alignment=Qt.AlignmentFlag.AlignCenter)
        choose = QPushButton("Choose Steam folder…")
        choose.clicked.connect(self.choose_steam_dir)
        retry = QPushButton("Try again")
        retry.clicked.connect(self.rescan)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(choose)
        buttons.addWidget(retry)
        buttons.addStretch()

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()
        layout.addWidget(self.message)
        layout.addLayout(buttons)
        layout.addStretch()
        return page

    def _build_status_bar(self) -> None:
        self.progress = QProgressBar(textVisible=False)
        self.progress.setMaximumWidth(220)
        self.progress.hide()
        self.game_label = QLabel()
        bar = self.statusBar()
        bar.addPermanentWidget(self.progress)
        bar.addPermanentWidget(self.game_label)
        bar.showMessage("Ready")

    # Scanning

    def rescan(self) -> None:
        if self._scan is None:
            return
        if self._scan_task is not None:
            self._scan_task.cancel()
        task = self.tasks.start(self._scan)
        self._scan_task = task
        task.progress.connect(self._show_progress)
        task.succeeded.connect(self.show_library)
        task.failed.connect(self._scan_failed)
        task.finished.connect(lambda: self._task_done(task))

    def set_scan(self, scan: Scan) -> None:
        self._scan = scan
        self.rescan()

    def choose_steam_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose your Steam folder (the one holding steamapps)", str(Path.home())
        )
        if folder:
            self.steam_dir_chosen.emit(Path(folder))

    def show_library(self, library: Library) -> None:
        self.library = library
        problems = list(library.problems)
        if self.book is None:
            try:
                self.book = PlaysetBook.open(self._playsets_path, library)
            except OSError as error:
                problems.append(f"Your playsets couldn't be loaded: {error}")
            else:
                problems += self.book.problems
        playsets = self.book.playsets if self.book else ()
        self.model.set_library(library, missing_mods(playsets, library))
        self.filter.set_playsets(playsets)
        self._fill_playsets()
        self._fill_tags(library)

        self.problems_label.setText("\n".join(problems))
        self.problems_label.setVisible(bool(problems))
        self.game_label.setText(f"Stellaris {library.game.version_name}")
        self.game_label.setToolTip(str(library.game.install_dir))
        outdated = sum(library.is_outdated(m) for m in library.mods)
        self.statusBar().showMessage(f"{len(library.mods)} mods, {outdated} outdated")
        self.pages.setCurrentIndex(0)
        self.errors_action.setEnabled(True)
        self._load_thumbnails(library)
        self._check_health(library)
        self.library_shown.emit(library)

    def _load_thumbnails(self, library: Library) -> None:
        if self._thumbnail_task is not None:
            self._thumbnail_task.cancel()
        task = self.tasks.start(thumbnail_job(library.mods, self._thumbnail_cache))
        self._thumbnail_task = task
        task.succeeded.connect(self._thumbnails_loaded)
        task.failed.connect(
            lambda error: self.statusBar().showMessage(f"Thumbnails failed to load: {error}")
        )

    def _thumbnails_loaded(self, images: dict[str, QImage]) -> None:
        self.model.set_thumbnails(images, blank_thumbnail())

    def _check_health(self, library: Library) -> None:
        if self._health_task is not None:
            self._health_task.cancel()
        self.model.set_health(None)
        task = self.tasks.start(HealthChecker(library, self._health_cache))
        self._health_task = task
        # A rescan replaces this task; a result from the old one is ignored.
        task.succeeded.connect(lambda health: self._health_checked(health, task))
        task.failed.connect(
            lambda error: self.statusBar().showMessage(f"Checking mods failed: {error}")
        )

    def _health_checked(self, health: dict[str, Health], task: Task) -> None:
        if task is not self._health_task:
            return
        self.model.set_health(health)
        if self.library is not None:
            mods = self.library.mods
            outdated = sum(self.library.is_outdated(m) for m in mods)
            broken = sum(status(health.get(m.key, ())) == "error" for m in mods)
            self.statusBar().showMessage(
                f"{len(mods)} mods, {outdated} outdated, {broken} with broken files"
            )
        self.health_shown.emit(health)

    def _cell_clicked(self, index: QModelIndex) -> None:
        if index.column() != Column.HEALTH:
            return
        mod: Mod = index.data(MOD_ROLE)
        issues = self.model.health(mod)
        if issues:
            self.show_health(mod, issues)

    def _scan_failed(self, error: Exception) -> None:
        if isinstance(error, GameNotFound):
            text = f"{error}\n\nIf Steam is somewhere else, choose its folder."
        else:
            text = f"Reading your mods failed:\n{type(error).__name__}: {error}"
        self.message.setText(text)
        self.pages.setCurrentIndex(1)
        self.statusBar().showMessage("Scan failed")

    def _show_progress(self, done: int, total: int, message: str) -> None:
        self.progress.setRange(0, total)  # 0..0 shows a busy bar
        self.progress.setValue(done)
        self.progress.show()
        self.statusBar().showMessage(message)

    def _task_done(self, task: Task) -> None:
        if task is self._scan_task:
            self._scan_task = None
            self.progress.hide()

    # The sidebar and filters

    def _fill_playsets(self, select: str | None = None) -> None:
        """Rebuild the sidebar. Keeps the current playset unless `select` names another."""
        if select is None:
            current = self.playset_list.currentItem()
            select = current.data(Qt.ItemDataRole.UserRole) if current else None
        library, book = self.library, self.book
        self.playset_list.blockSignals(True)
        self.playset_list.clear()
        everything = QListWidgetItem(f"All mods ({len(library.mods) if library else 0})")
        everything.setData(Qt.ItemDataRole.UserRole, None)
        self.playset_list.addItem(everything)
        selected = 0
        for row, playset in enumerate(book.playsets if book else (), 1):
            item = QListWidgetItem(self._playset_label(playset))
            item.setData(Qt.ItemDataRole.UserRole, playset.id)
            if book and playset.id == book.active:
                font = QFont()
                font.setBold(True)
                item.setFont(font)
                item.setToolTip("The playset you last played")
            if playset.id == select:
                selected = row
            self.playset_list.addItem(item)
        self.playset_list.blockSignals(False)
        self.playset_list.setCurrentRow(selected)
        self._playset_selected(selected)

    @staticmethod
    def _playset_label(playset: Playset) -> str:
        return f"{playset.name} ({len(playset.entries)})"

    def _fill_tags(self, library: Library) -> None:
        current = self.tag_box.currentData()
        tags = sorted({t for m in library.mods for t in m.tags}, key=str.casefold)
        self.tag_box.blockSignals(True)
        self.tag_box.clear()
        self.tag_box.addItem("All tags", "")
        for tag in tags:
            self.tag_box.addItem(tag, tag)
        index = max(0, self.tag_box.findData(current)) if current else 0
        self.tag_box.setCurrentIndex(index)
        self.tag_box.blockSignals(False)
        self.filter.set_tag(self.tag_box.currentData() or "")

    def selected_playset(self) -> Playset | None:
        item = self.playset_list.currentItem()
        if item is None or self.book is None:
            return None
        pid = item.data(Qt.ItemDataRole.UserRole)
        return self.book.get(pid) if pid else None

    def _playset_selected(self, row: int) -> None:
        playset = self.selected_playset()
        self._show_playset(playset)
        self.table.setColumnHidden(Column.POSITION, playset is None)
        if playset is None:
            self.table.sortByColumn(Column.NAME, Qt.SortOrder.AscendingOrder)
        else:
            self.table.sortByColumn(Column.POSITION, Qt.SortOrder.AscendingOrder)
        chosen = playset is not None
        for act in (
            self.copy_action,
            self.rename_action,
            self.delete_action,
            self.sort_action,
            self.dlc_action,
            self.play_action,
            self.conflicts_action,
            self.export_action,
            self.save_file_action,
        ):
            act.setEnabled(chosen)
        self.new_action.setEnabled(self.book is not None)
        self.load_file_action.setEnabled(self.book is not None)
        self.import_menu.setEnabled(self.book is not None)
        self.playset_bar.setVisible(chosen)
        self._refresh_conflicts()

    def _show_playset(self, playset: Playset | None) -> None:
        """Show this playset's order and missing mods. None shows all mods."""
        self.model.set_playset(playset)
        self.filter.set_playset(playset)
        installed = {m.key for m in self.library.mods} if self.library else set()
        missing = (
            [e.name or e.key for e in playset.entries if e.key not in installed]
            if (playset)
            else []
        )
        if missing:
            self.missing_label.setText(
                f"{len(missing)} mod(s) in this playset aren't installed (unsubscribed or "
                "deleted), so Play will leave them out: " + ", ".join(missing)
            )
        self.missing_label.setVisible(bool(missing))

    # Changing playsets

    def _edit(self, change: Callable[[Playset], Playset]) -> None:
        """Apply `change` to the selected playset, save it, and show the result."""
        playset = self.selected_playset()
        if playset is None or self.book is None:
            return
        changed = change(playset)
        if changed == playset:
            return
        self.book.update(changed)
        self._playset_changed(changed)

    def _playset_changed(self, playset: Playset) -> None:
        self._show_playset(playset)
        self._refresh_conflicts()
        item = self.playset_list.currentItem()
        if item is not None:
            item.setText(self._playset_label(playset))
        if self.book:
            self.filter.set_playsets(self.book.playsets)

    def _reload_playsets(self, select: str | None = None) -> None:
        """After adding or removing playsets: rebuild the list and the missing mods."""
        if self.library is None or self.book is None:
            return
        self.model.set_missing(missing_mods(self.book.playsets, self.library))
        self.filter.set_playsets(self.book.playsets)
        self._fill_playsets(select)

    def new_playset(self) -> None:
        if self.book is None:
            return
        name = self.ask_text("New playset", "Name:", self.book.unused_name("New playset"))
        if name:
            self._reload_playsets(self.book.create(name).id)

    def copy_playset(self) -> None:
        playset = self.selected_playset()
        if playset is None or self.book is None:
            return
        wanted = self.book.unused_name(f"{playset.name} (copy)")
        name = self.ask_text("Copy playset", "Name of the copy:", wanted)
        if name:
            copy = self.book.copy(playset.id, name)
            copy_resolutions(playset.id, copy.id, self._choices_dir)
            self._reload_playsets(copy.id)

    def rename_playset(self) -> None:
        playset = self.selected_playset()
        if playset is None or self.book is None:
            return
        name = self.ask_text("Rename playset", "New name:", playset.name)
        if name and name != playset.name:
            self._playset_changed(self.book.rename(playset.id, name))

    def delete_playset(self) -> None:
        playset = self.selected_playset()
        if playset is None or self.book is None:
            return
        if self.confirm(
            "Delete playset",
            f"Delete \u201c{playset.name}\u201d? Your mods stay installed. "
            "The launcher's copy, if it has one, isn't touched.",
        ):
            self.book.delete(playset.id)
            resolutions_file(playset.id, self._choices_dir).unlink(missing_ok=True)
            if self.library is not None:
                remove_patch(playset.id, self.library.game, self._patch_dir)
            self._reload_playsets("")

    def sort_mods(self) -> None:
        if self.library is None:
            return
        mods = {m.key: m for m in self.library.mods}
        self._edit(lambda p: sort_playset(p, mods))

    def choose_dlc(self) -> None:
        playset = self.selected_playset()
        if playset is None or self.library is None:
            return
        disabled = self.ask_dlc(playset)
        if disabled is not None:
            self._edit(lambda p: msgspec_replace(p, disabled_dlcs=disabled))

    def selected_keys(self) -> list[str]:
        """The selected mods, top to bottom as shown."""
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        return [self.filter.index(r, Column.NAME).data(MOD_ROLE).key for r in rows]

    def add_to_playset(self, playset_id: str, keys: Sequence[str]) -> None:
        if self.book is None or self.library is None:
            return
        playset = self.book.get(playset_id)
        if playset is None:
            return
        changed = ops.add_mods(playset, keys, self.library)
        self.book.update(changed)
        self.filter.set_playsets(self.book.playsets)
        for row in range(1, self.playset_list.count()):
            item = self.playset_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == playset_id:
                item.setText(self._playset_label(changed))
        added = len(changed.entries) - len(playset.entries)
        self.statusBar().showMessage(f"Added {added} mod(s) to {playset.name}")

    def _table_menu(self, pos: QPoint) -> None:
        keys = self.selected_keys()
        if not keys or self.book is None:
            return
        menu = QMenu(self)
        playset = self.selected_playset()
        if playset is None:
            add = menu.addMenu("Add to playset")
            for target in self.book.playsets:
                act = add.addAction(target.name)
                act.triggered.connect(lambda _=False, pid=target.id: self.add_to_playset(pid, keys))
            add.setEnabled(bool(self.book.playsets))
        else:
            menu.addAction("Turn on").triggered.connect(
                lambda: self._edit(lambda p: ops.set_enabled(p, keys, True))
            )
            menu.addAction("Turn off").triggered.connect(
                lambda: self._edit(lambda p: ops.set_enabled(p, keys, False))
            )
            menu.addSeparator()
            first = playset.entries[0].key if playset.entries else None
            menu.addAction("Move to top").triggered.connect(
                lambda: self._edit(lambda p: ops.move_mods(p, keys, first))
            )
            menu.addAction("Move to bottom").triggered.connect(
                lambda: self._edit(lambda p: ops.move_mods(p, keys, None))
            )
            menu.addSeparator()
            menu.addAction("Show conflicts").triggered.connect(lambda: self.show_conflicts(keys[0]))
            menu.addSeparator()
            menu.addAction("Remove from playset").triggered.connect(
                lambda: self._edit(lambda p: ops.remove_mods(p, keys))
            )
        menu.popup(self.table.viewport().mapToGlobal(pos))

    # The launcher, files and Play

    def _fill_import_menu(self) -> None:
        self.import_menu.clear()
        launcher = self.library.launcher_playsets if self.library else ()
        for playset in launcher:
            act = self.import_menu.addAction(self._playset_label(playset))
            act.triggered.connect(lambda _=False, p=playset: self.import_from_launcher(p))
        if not launcher:
            self.import_menu.addAction("The launcher has no playsets").setEnabled(False)

    def import_from_launcher(self, launcher_playset: Playset) -> None:
        if self.book is None:
            return
        playset = import_playset(self.book, launcher_playset)
        self._reload_playsets(playset.id)
        self.statusBar().showMessage(f"Imported {playset.name} from the launcher")

    def export_to_launcher(self) -> None:
        playset = self.selected_playset()
        if playset is None or self.book is None or self.library is None:
            return
        try:
            result = export_playset(self.book, playset, self.library, self.backup_dir)
        except (SyncError, LauncherDbError) as error:
            self.tell("Export to launcher", str(error))
            return
        text = f"The launcher now has \u201c{playset.name}\u201d."
        if result.skipped:
            text += (
                "\n\nThe launcher doesn't know these mods yet, so they were left out. "
                "Open the launcher once so it finds them, then export again:\n"
                + "\n".join(result.skipped)
            )
        if result.backup:
            text += f"\n\nThe launcher database was backed up first, to {result.backup}"
        self.tell("Export to launcher", text)

    def save_to_file(self) -> None:
        playset = self.selected_playset()
        if playset is None or self.library is None:
            return
        path = self.ask_save_path(f"{playset.name}.json")
        if path is None:
            return
        try:
            left_out = save_share_file(playset, self.library, path)
        except OSError as error:
            self.tell("Save to file", f"Couldn't save {path}: {error}")
            return
        if left_out:
            self.tell(
                "Save to file",
                "Local mods can't be shared, so these were left out:\n" + "\n".join(left_out),
            )
        self.statusBar().showMessage(f"Saved {playset.name} to {path}")

    def load_from_file(self) -> None:
        if self.book is None:
            return
        path = self.ask_open_path()
        if path is None:
            return
        try:
            shared = load_share_file(path)
        except ShareError as error:
            self.tell("Load from file", str(error))
            return
        playset = self.book.add(
            msgspec_replace(shared.playset, name=self.book.unused_name(shared.playset.name))
        )
        self._reload_playsets(playset.id)
        if shared.left_out:
            self.tell(
                "Load from file",
                "These mods have no Workshop ID, so they couldn't be added:\n"
                + "\n".join(shared.left_out),
            )
        self.statusBar().showMessage(f"Loaded {playset.name}")

    def play(self) -> None:
        playset = self.selected_playset()
        if playset is None or self.book is None or self.library is None:
            return
        plan = plan_play(playset, self.library)
        if plan.skipped and not self.confirm(
            "Some mods aren't installed",
            "These mods aren't installed, so the game won't load them:\n"
            + "\n".join(plan.skipped)
            + "\n\nPlay anyway?",
        ):
            return
        try:
            process = self.launch(plan, self.library.game, self.backup_dir)
        except PlayError as error:
            self.tell("Play", str(error))
            return
        self.book.set_active(playset.id)
        self._fill_playsets(playset.id)
        self.statusBar().showMessage(f"Stellaris is starting with {playset.name}")
        if hasattr(process, "poll"):
            self._game = process
            self._game_timer.start()

    # Errors from the game

    def show_errors(self) -> None:
        """Read error.log and show it, grouped by mod."""
        self._read_errors(show=True)

    def _read_errors(self, *, show: bool) -> None:
        if self.library is None:
            return
        task = self.tasks.start(ErrorReader(self.library))
        task.succeeded.connect(lambda report: self._errors_ready(report, show=show))
        task.failed.connect(
            lambda error: self.statusBar().showMessage(f"Reading error.log failed: {error}")
        )

    def _errors_ready(self, report: ErrorReport, *, show: bool) -> None:
        self.errors_button.setText(f"Errors ({report.total})" if report.total else "Errors")
        dialog = self._errors_dialog
        if show and dialog is None:
            dialog = self._errors_dialog = ErrorsDialog(self)
            dialog.refresh_requested.connect(self.show_errors)
        if dialog is not None and (show or dialog.isVisible()):
            dialog.set_report(report)
            if show:
                self.show_dialog(dialog)
        if not show:  # read because the game closed
            self.statusBar().showMessage(
                f"Stellaris closed with {report.total} errors in its log. "
                "Press Errors to see which mods caused them."
                if report.total
                else "Stellaris closed. Its error log is empty."
            )
        self.errors_read.emit(report)

    def _check_game(self) -> None:
        """Runs every few seconds while the game we started is open."""
        if self._game is None or self._game.poll() is None:
            return
        self._game_timer.stop()
        self._game = None
        self.statusBar().showMessage("Stellaris closed. Reading its error log…")
        self._read_errors(show=False)

    # Conflicts

    def show_conflicts(self, mod: str | None = None) -> None:
        """Open the Conflicts window for the selected playset. `mod` filters it to one mod."""
        if self.selected_playset() is None or self.library is None:
            return
        window = self._conflicts_window
        if window is None:
            window = self._conflicts_window = ConflictsWindow(self.tasks, self)
            window.refresh_requested.connect(self._find_conflicts)
            window.generate_requested.connect(self.generate_patch)
        self.show_dialog(window)
        self._find_conflicts(mod)

    def _refresh_conflicts(self) -> None:
        """The playset or its order changed: update the Conflicts window if it's open."""
        window = self._conflicts_window
        if window is None or not window.isVisible():
            return
        if self.selected_playset() is None:
            window.close()
        else:
            self._find_conflicts()

    def _find_conflicts(self, mod: str | None = None) -> None:
        playset, library, window = self.selected_playset(), self.library, self._conflicts_window
        if playset is None or library is None or window is None:
            return
        if self._conflicts_task is not None:
            self._conflicts_task.cancel()
        window.setWindowTitle(f"Conflicts in {playset.name}")
        cache, previous = self._index_cache, self._index

        leave_out = frozenset({patch_key(playset.id)})

        def job(ctx: JobContext) -> tuple[Index, Found]:
            index = Indexer(library, cache, previous)(ctx)
            return index, ConflictFinder(index, playset, library, leave_out=leave_out)(ctx)

        task = self.tasks.start(job)
        self._conflicts_task = task
        task.progress.connect(window.show_progress)
        task.succeeded.connect(lambda result: self._conflicts_found(result, task, mod))
        task.failed.connect(lambda error: window.show_failure(f"Finding conflicts failed: {error}"))

    def _conflicts_found(self, result: tuple[Index, Found], task: Task, mod: str | None) -> None:
        if task is not self._conflicts_task or self._conflicts_window is None:
            return  # a newer search replaced this one
        self._conflicts_task = None
        index, found = result
        self._index = index
        names = {m.key: m.name for m in self.library.mods} if self.library else {}
        playset = self.selected_playset()
        choices = self.choices_for(playset) if playset else None
        self._conflicts_window.set_found(found, index, names, mod, choices)
        self.conflicts_shown.emit(found)

    def choices_for(self, playset: Playset) -> ResolutionBook:
        """The playset's conflict choices, opened once and kept while it's selected."""
        path = resolutions_file(playset.id, self._choices_dir)
        if self._choices is None or self._choices.path != path:
            self._choices = ResolutionBook.open(path)
            if self._choices.problems:
                self.tell("Conflict choices", "\n".join(self._choices.problems))
        return self._choices

    # The patch mod

    def generate_patch(self) -> None:
        """Rebuild the playset's patch mod from its choices, then put it last."""
        playset, library = self.selected_playset(), self.library
        if playset is None or library is None or self._patch_task is not None:
            return
        choices = self.choices_for(playset)
        resolutions = choices.resolutions  # a snapshot; the job runs on another thread
        cache, previous, root = self._index_cache, self._index, self._patch_dir
        leave_out = frozenset({patch_key(playset.id)})

        def job(ctx: JobContext) -> PatchPlan:
            index = Indexer(library, cache, previous)(ctx)
            found = ConflictFinder(index, playset, library, leave_out=leave_out)(ctx)
            ctx.progress(0, 0, "Writing the patch mod")
            plan = plan_patch(found, resolutions, index)
            write_patch(plan, playset, library.game, root)
            return plan

        task = self.tasks.start(job)
        self._patch_task = task
        task.progress.connect(self._show_progress)
        task.succeeded.connect(lambda plan: self._patch_written(plan, playset, choices))
        task.failed.connect(self._patch_failed)
        task.finished.connect(self._patch_done)

    def _patch_written(self, plan: PatchPlan, playset: Playset, choices: ResolutionBook) -> None:
        choices.mark_built(plan.digest)
        if self.book is not None and self.book.get(playset.id) is not None:
            current = self.book.get(playset.id) or playset
            self.book.update(with_patch_last(current))
        text = (
            f"The patch mod holds {len(plan.written)} choice(s). It's at the end of "
            f"\u201c{playset.name}\u201d, so Play loads it last."
        )
        if plan.left_out:
            text += f"\n\nLeft out, {len(plan.left_out)}:\n" + "\n".join(
                f"{r.resolution.key}: {r.why}" for r in plan.left_out
            )
        self.statusBar().showMessage(f"Patch mod written for {playset.name}")
        self.patch_generated.emit(plan)
        # The scan finds the new mod, and refreshes the conflicts.
        self.rescan()
        self.tell("Patch mod", text)

    def _patch_failed(self, error: Exception) -> None:
        if isinstance(error, PatchError | OSError):
            self.tell("Patch mod", f"The patch mod wasn't written: {error}")
        else:
            self.tell("Patch mod", f"Writing the patch mod failed: {type(error).__name__}: {error}")

    def _patch_done(self) -> None:
        self._patch_task = None
        self.progress.hide()

    # Questions for the user. Tests replace these.

    def ask_text(self, title: str, label: str, text: str) -> str | None:
        value, ok = QInputDialog.getText(self, title, label, QLineEdit.EchoMode.Normal, text)
        return value.strip() if ok and value.strip() else None

    def confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(self, title, text)
        return answer == QMessageBox.StandardButton.Yes

    def tell(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def ask_dlc(self, playset: Playset) -> tuple[str, ...] | None:
        """The DLC folders to turn off, or None if cancelled."""
        if self.library is None:
            return None
        dialog = DlcDialog(self.library.dlcs, playset.disabled_dlcs, self)
        return dialog.disabled() if dialog.exec() else None

    def show_health(self, mod: Mod, issues: Health) -> None:
        HealthDialog(mod, issues, self).exec()

    def show_dialog(self, dialog: QWidget) -> None:
        """Show a window that stays open beside this one."""
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def ask_save_path(self, name: str) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save playset", str(Path.home() / name), "Playset files (*.json)"
        )
        return Path(path) if path else None

    def ask_open_path(self) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load playset",
            str(Path.home()),
            "Playset files (*.json *.zip);;All files (*)",
        )
        return Path(path) if path else None

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        self.tasks.cancel_all()
        self.tasks.wait(5000)
        super().closeEvent(event)
