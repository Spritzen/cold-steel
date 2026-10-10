"""The main Cold Steel window: playsets on the left, the mod list on the right."""

import contextlib
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from msgspec.structs import replace as msgspec_replace
from PySide6.QtCore import (
    QEvent,
    QFile,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QKeySequence,
)
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
    QStyle,
    QStyleOptionViewItem,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core import playsets as ops
from cold_steel.core.build import (
    Builder,
    BuildError,
    BuildRecord,
    build_key,
    build_name,
    builds_dir,
    built_from,
    load_record,
    load_stamp,
    not_ours,
    remove_build,
    save_record,
)
from cold_steel.core.conflicts import ConflictFinder, Found
from cold_steel.core.errors import ErrorReader, ErrorReport
from cold_steel.core.health import Health, HealthChecker, status
from cold_steel.core.hide import hidden_file, hidden_now, restore
from cold_steel.core.index import GAME, Index, Indexer
from cold_steel.core.jobs import Job, JobContext
from cold_steel.core.library import Library
from cold_steel.core.load_order import sort_playset
from cold_steel.core.local_mods import LocalFiles, local_files
from cold_steel.core.mods import Mod, base_key
from cold_steel.core.old_copies import OldCopy, describe, find_old_copies
from cold_steel.core.patch import (
    PatchError,
    PatchPlan,
    not_our_patch,
    patch_key,
    patch_keys,
    patched_from,
    patches_dir,
    plan_patch,
    remove_patch,
    with_patch_last,
    write_patch,
)
from cold_steel.core.play import HidePlan, PlayError, PlayPlan, plan_play, play
from cold_steel.core.playsets import PlaysetBook, as_played, missing_mods
from cold_steel.core.resolve import ResolutionBook, choices_digest, copy_resolutions
from cold_steel.core.saves import (
    BindingBook,
    Save,
    SaveCheck,
    SaveScanner,
    bound_saves,
    check_save,
    compare_mods,
    played_mods,
    played_since,
    suggest,
    unbound_saves,
)
from cold_steel.core.share import ShareError, load_share_file, save_share_file
from cold_steel.core.snapshots import (
    Drift,
    PinChecker,
    Pinner,
    Snapshot,
    SnapshotError,
    SnapshotStore,
    snapshots_dir,
)
from cold_steel.core.sync import (
    SyncError,
    check_launcher_can_open,
    describe_difference,
    export_playset,
    import_playset,
    launcher_difference,
    start_launcher,
    sync_launcher,
)
from cold_steel.paradox import processes
from cold_steel.paradox.continue_game import ContinueGame
from cold_steel.paradox.game import Game, GameNotFound
from cold_steel.paradox.launcher_db import LauncherDbError
from cold_steel.paradox.save import autosaves_to_cloud, local_save_dir
from cold_steel.store import paths
from cold_steel.store.playsets import Pin, Playset, PlaysetEntry, playsets_file
from cold_steel.store.resolutions import resolutions_dir, resolutions_file
from cold_steel.store.saves import bindings_file
from cold_steel.store.settings import Settings
from cold_steel.ui import icons
from cold_steel.ui.build_dialog import BuildDialog
from cold_steel.ui.conflicts_window import ConflictsWindow
from cold_steel.ui.dlc_dialog import DlcDialog
from cold_steel.ui.errors_dialog import ErrorsDialog
from cold_steel.ui.full_text import line_count, show_full_text
from cold_steel.ui.health_dialog import HealthDialog
from cold_steel.ui.help import ABOUT, ShortcutsDialog, WelcomeDialog
from cold_steel.ui.mod_table import ALL, MOD_ROLE, NO_PLAYSET, Column, ModFilter, ModTableModel
from cold_steel.ui.pins_dialog import PinsDialog
from cold_steel.ui.rebuild_dialog import RebuildChoice, RebuildDialog, describe_changes
from cold_steel.ui.saves_dialog import SavesDialog
from cold_steel.ui.settings_dialog import SettingsDialog
from cold_steel.ui.tasks import Task, TaskRunner
from cold_steel.ui.thumbnails import SHOWN_SIZE, blank_thumbnail, thumbnail_job

type Scan = Callable[[JobContext], Library]
type Launch = Callable[[PlayPlan, Game, Path], Any]
type StartLauncher = Callable[[Game], Any]
# What the conflict job finds in a playset: the index, the conflicts, the old copies.
type PlaysetFindings = tuple[Index, Found, tuple[OldCopy, ...]]

ROW_HEIGHT = SHOWN_SIZE.height() + 4
# Name and Tags wrap onto this many lines; ROW_HEIGHT has room for two.
TEXT_LINES = 2
WORKSHOP_PAGE = "https://steamcommunity.com/sharedfiles/filedetails/?id="
# Opens the same page inside the Steam client.
STEAM_WORKSHOP_PAGE = "steam://url/CommunityFilePage/"
# Tags is this wide, compared with Name.
TAGS_SHARE = 0.75
# The widest the Health column gets.
WIDEST_HEALTH = "99 warnings"
# Old copies named above the mod list; Conflicts lists the rest.
OLD_COPIES_SHOWN = 3


class MainWindow(QMainWindow):
    # The user changed the settings: the new Settings. The app saves and applies them.
    settings_chosen = Signal(object)
    # A scan finished and its result is on screen. For tests and timing.
    library_shown = Signal(object)
    # Every mod's health is checked and shown. For tests.
    health_shown = Signal(object)
    # error.log was read and grouped by mod. For tests.
    errors_read = Signal(object)
    # A playset's conflicts are on screen in the Conflicts window. For tests.
    conflicts_shown = Signal(object)
    # The selected playset was checked for old copies: the OldCopy tuple. For tests.
    old_copies_shown = Signal(object)
    # The patch mod was written: its PatchPlan. For tests.
    patch_generated = Signal(object)
    # Mods were pinned or unpinned: the changed Playset. For tests.
    pins_changed = Signal(object)
    # Pinned mods were compared with Steam's folders: {snapshot id: (Snapshot, Drift)}.
    pins_checked = Signal(object)
    # A playset was built into one mod: its BuildRecord. For tests.
    build_finished = Signal(object)
    # The save folders were read: every Save, newest first. For tests.
    saves_found = Signal(object)

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
        snapshot_dir: Path | None = None,
        build_dir: Path | None = None,
        saves_path: Path | None = None,
        hidden_path: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cold Steel")
        self.resize(1200, 760)
        self.settings = settings or Settings()

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
        # Mods replacing a newer mod's files with old copies, in the selected playset.
        self._old_task: Task | None = None
        self.old_copies: tuple[OldCopy, ...] = ()
        self._choices_dir = choices_dir or resolutions_dir()
        self._patch_dir = patch_dir or patches_dir()
        self._choices: ResolutionBook | None = None  # the selected playset's
        self._patch_task: Task | None = None
        self.snapshots = SnapshotStore(snapshot_dir or snapshots_dir())
        self._pin_task: Task | None = None
        self._check_task: Task | None = None
        # What Steam changed in each pinned mod, by snapshot id.
        self._checked: dict[str, tuple[Snapshot, Drift]] = {}
        self._pins_dialog: PinsDialog | None = None
        self._build_dir = build_dir or builds_dir()
        self._build_task: Task | None = None
        self._build_dialog: BuildDialog | None = None
        # A finished build, waiting for the scan that finds its mod: the record,
        # and whether its source playset was still selected (decision 95).
        self._shown_after_scan: tuple[BuildRecord, bool] | None = None
        self._saves_path = saves_path or bindings_file()
        # Saves, Continue, binding and the rebuild question are on only while the
        # game keeps its autosaves local (decision 91). Read from settings.txt.
        self.saves_on = False
        self.bindings: BindingBook | None = None
        # Every save in the save folders, newest first. None until they're read.
        self.saves: tuple[Save, ...] | None = None
        self._saves_task: Task | None = None
        self._saves_dialog: SavesDialog | None = None
        # The playset Play last started, and when (nanoseconds). New saves written
        # since are bound to it once the game closes.
        self._played: tuple[str, int] | None = None
        self._bind_played = False  # the next save scan does that binding
        # hidden.json: the saves of other playsets that Play hid from the game.
        self._hidden_path = hidden_path or hidden_file()
        # Saves were left hidden by an earlier run while the game was running:
        # they're put back once it closes.
        self._restore_waiting = False
        # What to do with the built playset's saves once the running build succeeds.
        self._rebuild: RebuildChoice | None = None
        # The playset whose Build waits for the saves to be read, to ask about them.
        self._build_waiting = ""
        # The game we started, watched so its errors can be read when it closes.
        self._game: Any = None
        self._game_timer = QTimer(self, interval=3000)
        self._game_timer.timeout.connect(self._check_game)
        # The window is sized to the mod list once, when it's first shown.
        self._fitted = False
        self._thumbnail_cache = thumbnail_cache or paths.cache_dir() / "thumbnails"
        self._playsets_path = playsets_path or playsets_file()
        self.backup_dir = backup_dir or paths.data_dir() / "backups"
        self.book: PlaysetBook | None = None
        # Writes dlc_load.json (and continue_game.json when the plan names a save),
        # then starts the game. Tests swap in a stand-in.
        self.launch: Launch = play
        # Starts the Paradox launcher. Tests swap in a stand-in.
        self.start_launcher: StartLauncher = start_launcher

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
        self._read_saves_setting()  # off until the game is found

        if scan is not None:
            QTimer.singleShot(0, self.rescan)

    # Building the window

    def _build_menu(self) -> None:
        def action(
            text: str, slot: Callable[[], object], shortcut: str | QKeySequence = ""
        ) -> QAction:
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            return act

        rescan = action("&Rescan mods", self.rescan, QKeySequence(QKeySequence.StandardKey.Refresh))
        search = action("Search &mods", self.focus_search, "Ctrl+F")
        settings = action("Se&ttings…", self.edit_settings, "Ctrl+,")
        quit_ = action("&Quit", self.close, QKeySequence(QKeySequence.StandardKey.Quit))
        self.sync_action = action("S&ync launcher…", self.sync_launcher)
        menu = self.menuBar().addMenu("&File")
        menu.addActions([rescan, search, settings])
        menu.addSeparator()
        menu.addAction(self.sync_action)
        menu.addSeparator()
        menu.addAction(quit_)

        self.new_action = action("&New playset…", self.new_playset, "Ctrl+N")
        self.copy_action = action("&Copy playset…", self.copy_playset, "Ctrl+D")
        self.rename_action = action("&Rename playset…", self.rename_playset, "F2")
        self.delete_action = action("&Delete playset", self.delete_playset, "Ctrl+Del")
        self.sort_action = action("&Sort load order", self.sort_mods, "Ctrl+L")
        self.dlc_action = action("&DLC…", self.choose_dlc)
        self.play_action = action("&Play", self.play, "Ctrl+Return")
        self.continue_action = action("Con&tinue", self.continue_playset)
        self.continue_action.setEnabled(False)
        self.saves_action = action("S&aves…", self.show_saves)
        self.errors_action = action("&Errors from the last game…", self.show_errors, "Ctrl+E")
        self.errors_action.setEnabled(False)
        self.conflicts_action = action("&Conflicts…", self.show_conflicts, "Ctrl+K")
        self.pins_action = action("Pinned &versions…", self.show_pins)
        self.build_action = action("&Build one mod", self.build_mod, "Ctrl+B")
        self.report_action = action("Build re&port…", self.show_build_report)
        self.delete_build_action = action("Delete b&uild…", self._delete_selected_build)
        self.delete_patch_action = action("Delete pa&tch mod…", self._delete_selected_patch)
        self.export_action = action("&Export to launcher", self.export_to_launcher, "Ctrl+Shift+E")
        self.open_launcher_action = action("&Open in launcher", self.open_in_launcher)
        self.save_file_action = action("&Save to file…", self.save_to_file, "Ctrl+S")
        self.load_file_action = action("&Load from file…", self.load_from_file, "Ctrl+O")
        self.import_menu = QMenu("&Import from launcher", self)
        self.import_menu.aboutToShow.connect(self._fill_import_menu)

        menu = self.menuBar().addMenu("&Playset")
        menu.addActions([self.new_action, self.copy_action, self.rename_action])
        menu.addAction(self.delete_action)
        menu.addSeparator()
        menu.addActions([self.play_action, self.continue_action, self.saves_action])
        menu.addActions([self.errors_action, self.conflicts_action])
        menu.addAction(self.delete_patch_action)
        menu.addActions([self.sort_action, self.dlc_action])
        menu.addSeparator()
        menu.addActions([self.pins_action, self.build_action, self.report_action])
        menu.addAction(self.delete_build_action)
        menu.addSeparator()
        menu.addMenu(self.import_menu)
        menu.addActions([self.export_action, self.open_launcher_action])
        menu.addSeparator()
        menu.addActions([self.load_file_action, self.save_file_action])

        menu = self.menuBar().addMenu("&Help")
        menu.addAction(action("&Keyboard shortcuts", self.show_shortcuts, "F1"))
        menu.addAction(action("&What Cold Steel changes…", self.show_welcome))
        menu.addSeparator()
        menu.addAction(action("&About Cold Steel", self.show_about))

    def _build_library_page(self) -> QWidget:
        self.playset_list = QListWidget()
        self._sidebar_choice: str | None = None  # playset id picked in the sidebar
        self.playset_list.currentRowChanged.connect(self._playset_selected)
        self.playset_list.itemDoubleClicked.connect(lambda _: self.rename_playset())

        # Equal space before, between and after the buttons.
        sidebar_buttons = QHBoxLayout()
        sidebar_buttons.setSpacing(0)
        self._sidebar_icons = (
            (self.new_action, "document-new", icons.draw_new),
            (self.copy_action, "edit-copy", icons.draw_copy),
            (self.rename_action, "edit-rename", icons.draw_rename),
            (self.delete_action, "edit-delete", icons.draw_delete),
        )
        for act, name, draw in self._sidebar_icons:
            sidebar_buttons.addStretch()
            sidebar_buttons.addWidget(self._icon_button(act, icons.theme_icon(name, draw)))
        sidebar_buttons.addStretch()
        sidebar = self.sidebar = QWidget()
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
        self.include_box = QComboBox()
        self.include_box.addItem("Include", False)  # data: exclude
        self.include_box.addItem("Exclude", True)
        self.include_box.setToolTip("Show only the mods in the chosen playset, or hide them")
        self.include_box.currentIndexChanged.connect(self._membership_chosen)
        self._playset_filtered = False  # the playset filter names a real playset
        self.membership_box = QComboBox()
        self._fill_membership()
        self.membership_box.currentIndexChanged.connect(self._membership_chosen)
        self.outdated_box = QCheckBox("Outdated only")
        self.outdated_box.toggled.connect(self.filter.set_outdated_only)
        self.problems_box = QCheckBox("Problems only")
        self.problems_box.setToolTip("Mods whose health check found errors or warnings")
        self.problems_box.toggled.connect(self.filter.set_problems_only)

        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.tag_box)
        filters.addWidget(self.include_box)
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
        self.continue_button = self._button("Continue", self.continue_action)
        playset_bar.addWidget(self.continue_button)
        self.errors_button = self._button("Errors", self.errors_action)
        playset_bar.addWidget(self.errors_button)
        playset_bar.addWidget(self._button("Conflicts", self.conflicts_action))
        playset_bar.addWidget(self._button("Sort", self.sort_action))
        playset_bar.addWidget(self._button("DLC…", self.dlc_action))
        playset_bar.addWidget(self._button("Pins…", self.pins_action))
        playset_bar.addWidget(self._button("Build", self.build_action))
        self.saves_button = self._button("Saves", self.saves_action)
        playset_bar.addWidget(self.saves_button)
        playset_bar.addStretch()
        playset_bar.addWidget(self._button("Export to launcher", self.export_action))
        playset_bar.addWidget(self._button("Open in launcher", self.open_launcher_action))

        self.problems_label = QLabel(wordWrap=True)
        self.problems_label.setStyleSheet("color: #d9534f;")
        self.problems_label.hide()
        self.missing_label = QLabel(wordWrap=True)
        self.missing_label.setStyleSheet("color: #d9534f;")
        self.missing_label.hide()
        self.pin_label = QLabel(wordWrap=True)
        self.pin_label.hide()
        self.launcher_label = QLabel(wordWrap=True)
        self.launcher_label.hide()
        self.saves_label = QLabel(wordWrap=True)
        self.saves_label.hide()
        self.old_label = QLabel(wordWrap=True)
        self.old_label.setStyleSheet("color: #d9534f;")
        self.old_label.hide()

        self.table = QTableView()
        self.table.setModel(self.filter)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setIconSize(SHOWN_SIZE)
        self.table.setWordWrap(True)
        self.table.setAlternatingRowColors(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.table.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.table.setDropIndicatorShown(True)
        self.table.setDragDropOverwriteMode(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.clicked.connect(self._cell_clicked)
        self.table.verticalHeader().hide()
        show_full_text(self.table, self.playset_list)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        header = self.table.horizontalHeader()
        # Name and Tags share the room the other columns leave (_share_name_and_tags).
        header.setSectionResizeMode(Column.NAME, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(Column.TAGS, QHeaderView.ResizeMode.Fixed)
        header.sectionResized.connect(self._other_column_resized)
        self.table.viewport().installEventFilter(self)
        for col in (Column.POSITION, Column.VERSION, Column.SUPPORTED, Column.SOURCE):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        # Health starts as "…", so it gets room for its widest text up front.
        header.resizeSection(
            Column.HEALTH,
            max(
                header.sectionSizeHint(Column.HEALTH),
                self.table.fontMetrics().horizontalAdvance(WIDEST_HEALTH) + 12,
            ),
        )

        right = QWidget()
        layout = QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.playset_bar)
        layout.addLayout(filters)
        layout.addWidget(self.problems_label)
        layout.addWidget(self.missing_label)
        layout.addWidget(self.pin_label)
        layout.addWidget(self.launcher_label)
        layout.addWidget(self.saves_label)
        layout.addWidget(self.old_label)
        layout.addWidget(self.table)

        self.splitter = QSplitter()
        self.splitter.addWidget(sidebar)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([200, 1000])
        return self.splitter

    @staticmethod
    def _button(text: str, action: QAction) -> QPushButton:
        """A button that runs `action` and is enabled whenever it is."""
        button = QPushButton(text)
        button.setToolTip(action.text().replace("&", ""))
        button.clicked.connect(action.trigger)
        action.enabledChanged.connect(button.setEnabled)
        button.setEnabled(action.isEnabled())
        return button

    @staticmethod
    def _icon_button(action: QAction, icon: QIcon) -> QToolButton:
        """An icon-only button for `action`. Its name shows on hover."""
        action.setIcon(icon)
        tip = action.text().replace("&", "")
        if not action.shortcut().isEmpty():
            shortcut = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            tip += f" ({shortcut})"
        action.setToolTip(tip)
        button = QToolButton()
        button.setDefaultAction(action)
        button.setAutoRaise(True)
        button.setIconSize(QSize(16, 16))
        return button

    def _build_message_page(self) -> QWidget:
        self.message = QLabel(wordWrap=True, alignment=Qt.AlignmentFlag.AlignCenter)
        choose = QPushButton("Settings…")
        choose.clicked.connect(self.edit_settings)
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

    def edit_settings(self) -> None:
        changed = self.ask_settings()
        if changed is not None and changed != self.settings:
            self.settings = changed
            self.settings_chosen.emit(changed)
        if self._read_saves_setting():
            self._scan_saves()

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
        if self.bindings is None:
            try:
                self.bindings = BindingBook.open(self._saves_path)
            except OSError as error:
                problems.append(f"Your saves couldn't be loaded: {error}")
            else:
                problems += self.bindings.problems
            self._put_back_saves()  # left hidden if Cold Steel closed while the game ran
        playsets = self.book.playsets if self.book else ()
        self.model.set_library(library, missing_mods(playsets, library))
        self.filter.set_playsets(playsets)
        self._fill_playsets()
        self._fill_tags(library)
        self._fit_columns()

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
        self._check_pins(library)
        self._check_old_copies()
        self._read_saves_setting()
        self._scan_saves()
        self._show_finished_build()
        self.library_shown.emit(library)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt's name)
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self._share_name_and_tags()
        return super().eventFilter(watched, event)

    def _other_column_resized(self, col: int, _old: int, _new: int) -> None:
        if col not in (Column.NAME, Column.TAGS):
            self._share_name_and_tags()

    def _share_name_and_tags(self) -> None:
        """Split the room the other columns leave between Name and Tags,
        with Tags TAGS_SHARE as wide as Name."""
        header = self.table.horizontalHeader()
        others = sum(
            header.sectionSize(c)
            for c in Column
            if c not in (Column.NAME, Column.TAGS) and not header.isSectionHidden(c)
        )
        room = max(0, self.table.viewport().width() - others)
        name = round(room / (1 + TAGS_SHARE))
        header.resizeSection(Column.NAME, name)
        header.resizeSection(Column.TAGS, room - name)

    def _fit_columns(self) -> None:
        """The first time, size the window and sidebar so every mod's name fits
        on two lines. Tags get their share of that; longer ones show on hover."""
        self._share_name_and_tags()
        header = self.table.horizontalHeader()
        if self._fitted:
            return
        self._fitted = True
        fm = self.playset_list.fontMetrics()
        bold = QFont(self.playset_list.font())
        bold.setBold(True)
        bold_fm = QFontMetrics(bold)
        names = [self.playset_list.item(r).text() for r in range(self.playset_list.count())]
        sidebar = max(
            (bold_fm.horizontalAdvance(n) for n in names), default=fm.horizontalAdvance("M" * 12)
        )
        sidebar += 2 * self.playset_list.frameWidth() + 16  # item margins
        sidebar = max(sidebar, self.sidebar.minimumSizeHint().width())
        sidebar = min(sidebar, 260)

        # The sized-to-fit columns may be hidden or not laid out yet, so they're measured.
        fitted = (Column.POSITION, Column.VERSION, Column.SUPPORTED, Column.SOURCE)
        name = self._wrapped_width(Column.NAME)
        columns = (
            name
            + math.ceil(name * TAGS_SHARE)
            + header.sectionSize(Column.HEALTH)
            + sum(self._content_width(c) for c in fitted)
        )
        table = (
            columns
            + 2 * self.table.frameWidth()
            + self.table.verticalScrollBar().sizeHint().width()
        )
        chrome = self.width() - self.splitter.width() + self.splitter.handleWidth()
        wanted = sidebar + table + chrome + 4  # a little slack against rounding
        screen = self.screen().availableGeometry()
        width = max(self.width(), min(wanted, screen.width()))
        self.resize(width, self.height())
        self.splitter.setSizes([sidebar, width - sidebar])

    def _content_width(self, col: Column) -> int:
        """The width a sized-to-fit column takes in any view, measured over every
        mod, not just the ones shown."""
        header = self.table.horizontalHeader()
        hidden = header.isSectionHidden(col)
        header.setSectionHidden(col, False)
        widest = header.sectionSizeHint(col)
        header.setSectionHidden(col, hidden)
        delegate = self.table.itemDelegate()
        option = QStyleOptionViewItem()
        self.table.initViewItemOption(option)
        for row in range(self.model.rowCount()):
            widest = max(widest, delegate.sizeHint(option, self.model.index(row, col)).width())
        return widest

    def _wrapped_width(self, col: Column) -> int:
        """The narrowest the column can be while every mod's text fits in TEXT_LINES lines."""
        # Ask the style where a cell puts its text. A Name cell always counts a
        # checkbox and a thumbnail: thumbnails may not be loaded yet, and only a
        # playset has checkboxes.
        style = self.table.style()
        option = QStyleOptionViewItem()
        self.table.initViewItemOption(option)
        option.rect = QRect(0, 0, 1000, ROW_HEIGHT)
        option.text = "x"
        option.features |= QStyleOptionViewItem.ViewItemFeature.HasDisplay
        if col == Column.NAME:
            option.features |= (
                QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
                | QStyleOptionViewItem.ViewItemFeature.HasDecoration
            )
            option.decorationSize = SHOWN_SIZE
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, self.table)
        # The text is drawn inset by this much on each side.
        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, self.table) + 1
        beside = option.rect.width() - text_rect.width() + 2 * margin

        widest = self.table.horizontalHeader().sectionSizeHint(col)
        for row in range(self.model.rowCount()):
            index = self.model.index(row, col)
            text = index.data() or ""
            if text:
                font = index.data(Qt.ItemDataRole.FontRole) or self.table.font()
                widest = max(widest, beside + _wrap_width(font, text, TEXT_LINES) + 1)
        return widest

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
            text = f"{error}\n\nIf Steam is somewhere else, choose its folder in Settings."
        else:
            text = f"Reading your mods failed:\n{type(error).__name__}: {error}"
        self.message.setText(text)
        self.pages.setCurrentIndex(1)
        self.statusBar().showMessage("Scan failed")
        if self._shown_after_scan is not None:
            record, _ = self._shown_after_scan
            self._shown_after_scan = None
            self.show_build_report(record)

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
        self._fill_membership()

    def _fill_membership(self) -> None:
        """The playset filter: All, No playset, then every playset."""
        box = self.membership_box
        current = box.currentData()
        box.blockSignals(True)
        box.clear()
        box.addItem("All", ALL)
        box.addItem("No playset", NO_PLAYSET)
        playsets = self.book.playsets if self.book else ()
        if playsets:
            box.insertSeparator(box.count())
        for playset in playsets:
            box.addItem(playset.name, playset.id)
        box.setCurrentIndex(max(0, box.findData(current)))
        box.blockSignals(False)
        self._membership_chosen()

    def _membership_chosen(self) -> None:
        playset_id = self.membership_box.currentData() or ALL
        # Exclude only makes sense for a real playset.
        real = playset_id not in (ALL, NO_PLAYSET)
        if real != self._playset_filtered:
            # Coming from All or No playset, a playset starts on Exclude, the usual
            # choice. Moving between playsets keeps whatever was picked.
            self.include_box.blockSignals(True)
            self.include_box.setCurrentIndex(1 if real else 0)  # Exclude, or Include
            self.include_box.blockSignals(False)
        self._playset_filtered = real
        self.include_box.setEnabled(real)
        self.filter.set_membership(playset_id, bool(self.include_box.currentData()))

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
        choice = playset.id if playset else None
        if choice != self._sidebar_choice:
            # A new sidebar choice starts with the playset filter back on Include All.
            # A refresh that keeps the same choice leaves the filter alone.
            self._sidebar_choice = choice
            self.membership_box.setCurrentIndex(0)
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
            self.saves_action,
            self.conflicts_action,
            self.pins_action,
            self.build_action,
            self.export_action,
            self.open_launcher_action,
            self.save_file_action,
        ):
            act.setEnabled(chosen)
        built = playset is not None and (self._build_dir / f"{playset.id}.json").exists()
        self.report_action.setEnabled(built)
        self.delete_build_action.setEnabled(built)
        patched = playset is not None and (self._patch_dir / playset.id).is_dir()
        self.delete_patch_action.setEnabled(patched)
        self.new_action.setEnabled(self.book is not None)
        self.load_file_action.setEnabled(self.book is not None)
        self.sync_action.setEnabled(self.book is not None)
        self.import_menu.setEnabled(self.book is not None)
        self.playset_bar.setVisible(chosen)
        self._refresh_conflicts()

    def _show_playset(self, playset: Playset | None) -> None:
        """Show this playset's order and missing mods. None shows all mods."""
        self.model.set_playset(playset)
        self.filter.set_playset(playset)
        installed = {m.key for m in self.library.every_mod} if self.library else set()
        missing = (
            [e.name or e.key for e in as_played(playset).entries if e.key not in installed]
            if (playset)
            else []
        )
        if missing:
            self.missing_label.setText(
                f"{len(missing)} mod(s) in this playset aren't installed (unsubscribed or "
                "deleted), so Play will leave them out: " + ", ".join(missing)
            )
        self.missing_label.setVisible(bool(missing))
        self._show_pin_state(playset)
        self._show_launcher_state(playset)
        self._show_saves(playset)
        self._check_old_copies()

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
            self._fill_membership()  # a rename changes its name there

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
            "The launcher's copy, if it has one, isn't touched. Its patch mod, its "
            "build and its pinned copies are deleted, unless another playset uses them."
            + self._saves_unbound_text(playset),
        ):
            self._forget_playset(playset)
            self._reload_playsets("")
            if playset.pins:
                self._collect_snapshots()

    def _forget_playset(self, playset: Playset) -> None:
        """Delete a playset with its choices, patch mod and build."""
        assert self.book is not None
        self.book.delete(playset.id)
        if self.bindings is not None:
            self.bindings.forget_playset(playset.id)
        resolutions_file(playset.id, self._choices_dir).unlink(missing_ok=True)
        game = self.library.game if self.library else None
        if game is not None:
            remove_patch(playset.id, game, self._patch_dir)
        remove_build(playset.id, self._build_dir, game)

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
        if len(keys) == 1 and (steam_id := self._workshop_id(keys[0])):
            menu.addSeparator()
            menu.addAction("Open in browser").triggered.connect(
                lambda: self.open_url(WORKSHOP_PAGE + steam_id)
            )
            # Only offered while Steam runs, so it never starts Steam by surprise.
            if processes.running(processes.STEAM):
                menu.addAction("Open in Steam").triggered.connect(
                    lambda: self.open_url(STEAM_WORKSHOP_PAGE + steam_id)
                )
        # Cold Steel's own mods have their own ways to go.
        built = [b for k in keys if (b := built_from(k))]
        if len(keys) == 1 and built:
            menu.addSeparator()
            menu.addAction("Delete built mod…").triggered.connect(
                lambda: self.delete_build(built[0])
            )
        patched = [p for k in keys if (p := patched_from(k))]
        if len(keys) == 1 and patched:
            menu.addSeparator()
            menu.addAction("Delete patch mod…").triggered.connect(
                lambda: self.delete_patch(patched[0])
            )
        if len(keys) == 1 and self._local_files(keys[0]) is not None:
            menu.addSeparator()
            menu.addAction("Delete local mod…").triggered.connect(
                lambda: self.delete_local_mod(keys[0])
            )
        menu.popup(self.table.viewport().mapToGlobal(pos))

    def _local_files(self, key: str) -> LocalFiles | None:
        library = self.library
        mod = next((m for m in library.mods if m.key == key), None) if library else None
        return local_files(mod, library.game.mod_dir) if library and mod else None

    def delete_local_mod(self, key: str) -> None:
        """Move a local mod's .mod file and folder (or zip) to the trash."""
        library, book = self.library, self.book
        files = self._local_files(key)
        if library is None or book is None or files is None:
            return
        name = next(m.name for m in library.mods if m.key == key)
        title = "Delete local mod"
        gone = "\n".join(
            f"  {p}" + (" (just the link)" if p == files.content and files.link else "")
            for p in files.paths
        )
        text = f"Move \u201c{name}\u201d to the trash? These go:\n{gone}"
        if files.kept is not None:
            text += f"\n\nIts files stay where they are, outside the mod folder:\n  {files.kept}"
        users = [p for p in book.playsets if any(e.key == key for e in p.entries)]
        if users:
            names = ", ".join(f"\u201c{p.name}\u201d" for p in users)
            text += f"\n\nIt's also taken out of {names}."
        if not self.confirm(title, text):
            return
        failed = []
        for path in files.paths:
            if path == files.content and files.link:
                try:
                    path.unlink()  # never what the link points at
                except OSError:
                    failed.append(path)
            elif not self.trash(path):
                failed.append(path)
        if failed:
            listed = "\n".join(f"  {p}" for p in failed)
            self.tell(title, f"These couldn't be moved to the trash:\n{listed}")
        else:
            # Gone for good, so playsets drop it instead of showing it as missing.
            for playset in users:
                book.update(ops.remove_mods(playset, [key]))
            self.statusBar().showMessage(f"Moved {name} to the trash")
        self.rescan()  # the rescan redraws the table and the playset counts

    def _workshop_id(self, key: str) -> str:
        """The mod's Steam Workshop ID, or "" if it isn't on the Workshop."""
        mods = self.library.every_mod if self.library else ()
        mod = next((m for m in mods if m.key == key), None)
        if mod is not None and mod.remote_file_id:
            return mod.remote_file_id
        source, _, ident = key.partition(":")
        return ident.partition("@")[0] if source == "workshop" else ""

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

    def _show_launcher_state(self, playset: Playset | None) -> None:
        """The line above the mod list saying the launcher's copy is out of date."""
        diff = launcher_difference(playset, self.library) if playset and self.library else None
        if not diff:
            self.launcher_label.hide()
            return
        self.launcher_label.setText(describe_difference(diff))
        self.launcher_label.show()

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
        self.rescan()  # reads the launcher's copy again
        self.tell("Export to launcher", text)

    def open_in_launcher(self) -> None:
        """Export the playset as the launcher's active one, then start the launcher."""
        playset = self.selected_playset()
        if playset is None or self.book is None or self.library is None:
            return
        game = self.library.game
        try:
            check_launcher_can_open()
            result = export_playset(self.book, playset, self.library, self.backup_dir, active=True)
            self.start_launcher(game)
        except (SyncError, LauncherDbError) as error:
            self.tell("Open in launcher", str(error))
            return
        self.statusBar().showMessage(f"Opening the Paradox launcher with {playset.name}")
        self.rescan()  # reads the launcher's copy again
        if result.skipped:
            self.tell(
                "Open in launcher",
                "The launcher doesn't know these mods yet, so they were left out. "
                "Close the launcher once it has found them, then open it from here again:\n"
                + "\n".join(result.skipped),
            )

    def sync_launcher(self) -> None:
        """Replace every playset in the launcher with Cold Steel's, if the user agrees."""
        if self.book is None or self.library is None:
            return
        count = len(self.book.playsets)
        if not self.confirm(
            "Sync launcher",
            "Are you sure?\n\nThis replaces all playsets in the Paradox launcher with "
            f"the {count} in Cold Steel. Launcher playsets that aren't in Cold Steel "
            "are removed, and the others are changed to match.\n\n"
            "The launcher database is backed up first.",
        ):
            return
        try:
            result = sync_launcher(self.book, self.library, self.backup_dir)
        except (SyncError, LauncherDbError) as error:
            self.tell("Sync launcher", str(error))
            return
        text = f"The launcher now has the same {count} playsets as Cold Steel."
        if result.removed:
            removed = (
                "1 other playset was" if result.removed == 1 else f"{result.removed} others were"
            )
            text += f" {removed} removed from it."
        missing = [
            f"{playset.name}: {mod}"
            for playset, skipped in zip(self.book.playsets, result.skipped, strict=True)
            for mod in skipped
        ]
        if missing:
            text += (
                "\n\nThe launcher doesn't know these mods yet, so they were left out. "
                "Open the launcher once so it finds them, then sync again:\n" + "\n".join(missing)
            )
        if result.backup:
            text += f"\n\nThe launcher database was backed up first, to {result.backup}"
        self.rescan()  # reads the launcher's copies again
        self.tell("Sync launcher", text)

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
        self._play(None)

    def continue_playset(self, folder: str = "") -> None:
        """Play the selected playset and open one of its saves at its newest
        file, skipping the game's main menu. By default its newest save."""
        playset, bindings = self.selected_playset(), self.bindings
        if playset is None or bindings is None or not self.saves_on:
            return
        bound = bound_saves(self.saves or (), bindings, playset.id)
        save = next((s for s in bound if s.folder == folder), None) if folder else None
        save = save or (bound[0] if bound else None)
        if save is None:
            return
        check = self._check_saves(playset, [save]).get(save.folder)
        if (
            check
            and check.marks
            and not self.confirm(
                "Continue",
                f"\u201c{save.empire}\u201d is marked: {', '.join(check.marks)}.\n\n"
                + "\n".join(check.details())
                + "\n\nContinue anyway?",
            )
        ):
            return
        self._play(_continue_game(save), skip_menu=True)

    def _play(self, continue_from: ContinueGame | None, *, skip_menu: bool = False) -> None:
        """Play the selected playset, with the game's Continue pointing at
        `continue_from`, or at the playset's newest save. `skip_menu` opens it."""
        playset = self.selected_playset()
        if playset is None or self.book is None or self.library is None:
            return
        self._read_saves_setting()  # read each time Play is pressed
        if continue_from is None and self.bindings is not None and self.saves_on:
            bound = bound_saves(self.saves or (), self.bindings, playset.id)
            continue_from = _continue_game(bound[0]) if bound else None
        plan = dataclass_replace(
            plan_play(as_played(playset), self.library),
            continue_from=continue_from,
            skip_menu=skip_menu and continue_from is not None,
            hide=self._hide_plan(playset),
        )
        if plan.skipped and not self.confirm(
            "Some mods aren't installed",
            "These mods aren't installed, so the game won't load them:\n"
            + "\n".join(plan.skipped)
            + "\n\nPlay anyway?",
        ):
            return
        started = time.time_ns()
        try:
            process = self.launch(plan, self.library.game, self.backup_dir)
        except PlayError as error:
            self.tell("Play", str(error))
            return
        self._played = (playset.id, started)
        self.book.set_active(playset.id)
        self._fill_playsets(playset.id)
        at = (
            f", at {continue_from.empire} {continue_from.date}"
            if skip_menu and continue_from
            else ""
        )
        hidden = len(hidden_now(self._hidden_path)) if plan.hide else 0
        hid = f". {hidden} save(s) of other playsets are hidden until it closes" if hidden else ""
        self.statusBar().showMessage(f"Stellaris is starting with {playset.name}{at}{hid}")
        if hasattr(process, "poll"):
            self._game = process
            self._game_timer.start()

    def _hide_plan(self, playset: Playset) -> HidePlan | None:
        """The saves of other playsets to hide from the game, when its autosaves
        stay local (decision 91)."""
        if self.book is None or self.bindings is None or not self.saves_on:
            return None
        others = self.bindings.bound_elsewhere(playset.id, {p.id for p in self.book.playsets})
        return HidePlan(tuple(sorted(others)), self._hidden_path) if others else None

    def _put_back_saves(self) -> None:
        """Move back the saves Play hid, once the game isn't running."""
        if not hidden_now(self._hidden_path):
            return
        if processes.running(processes.GAME):
            # Started before Cold Steel was, or by an earlier run of it: wait for it to close.
            self._restore_waiting = True
            self._game_timer.start()
            return
        self._restore_waiting = False
        result = restore(self._hidden_path)
        if result.stuck:
            listed = "\n".join(f"  {f}" for f in result.stuck)
            self.tell(
                "Hidden saves",
                "These saves are still hidden, because a save folder of the same name "
                f"is back in save games:\n{listed}\n\nThey're in {result.hidden_dir}. "
                "Move them back by hand once you've checked which copy to keep.",
            )

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
        self.errors_button.setText(f"Errors ({report.problems})" if report.problems else "Errors")
        dialog = self._errors_dialog
        if show and dialog is None:
            dialog = self._errors_dialog = ErrorsDialog(self)
            dialog.refresh_requested.connect(self.show_errors)
        if dialog is not None and (show or dialog.isVisible()):
            dialog.set_report(report)
            if show:
                self.show_dialog(dialog)
        if not show:  # read because the game closed
            if report.problems:
                text = (
                    f"Stellaris closed with {report.problems} errors in its log. "
                    "Press Errors to see which mods caused them."
                )
            elif report.total:
                text = "Stellaris closed. Its log only has overrides and other normal entries."
            else:
                text = "Stellaris closed. Its error log is empty."
            self.statusBar().showMessage(text)
        self.errors_read.emit(report)

    def _check_game(self) -> None:
        """Runs every few seconds while the game is open: the one we started,
        or one that was running when Cold Steel started with saves hidden."""
        if self._game is None:
            if self._restore_waiting and not processes.running(processes.GAME):
                self._game_timer.stop()
                self._put_back_saves()
                self._read_saves_setting()
                self._scan_saves()
            return
        if self._game.poll() is None:
            return
        self._game_timer.stop()
        self._game = None
        self._put_back_saves()
        self.statusBar().showMessage("Stellaris closed. Reading its error log…")
        self._read_errors(show=False)
        self._read_saves_setting()  # it may have been changed in the game
        self._bind_played = self.saves_on
        self._scan_saves()

    # Conflicts

    def show_conflicts(self, mod: str | None = None) -> None:
        """Open the Conflicts window for the selected playset. `mod` filters it to one mod."""
        playset = self.selected_playset()
        if playset is None or self.library is None:
            return
        if mod is not None:  # the table names a pinned mod by its own key
            played = {base_key(e.key): e.key for e in as_played(playset).entries}
            mod = played.get(mod, mod)
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
        task = self.tasks.start(self._playset_job(playset, library))
        self._conflicts_task = task
        task.progress.connect(window.show_progress)
        task.succeeded.connect(lambda result: self._conflicts_found(result, task, mod))
        task.failed.connect(lambda error: window.show_failure(f"Finding conflicts failed: {error}"))

    def _conflicts_found(self, result: PlaysetFindings, task: Task, mod: str | None) -> None:
        if task is not self._conflicts_task or self._conflicts_window is None:
            return  # a newer search replaced this one
        self._conflicts_task = None
        index, found, old = result
        self._index = index
        names = self.mod_names()
        playset = self.selected_playset()
        choices = self.choices_for(playset) if playset else None
        self._conflicts_window.set_found(found, index, names, mod, choices, old)
        self._show_old_copies(old)
        self.conflicts_shown.emit(found)

    def _playset_job(self, playset: Playset, library: Library) -> Job[PlaysetFindings]:
        """A job finding the playset's conflicts and old copies, leaving out its patch mod
        so the clashes it settles still show."""
        cache, previous = self._index_cache, self._index
        leave_out = patch_keys(playset.id, self._patch_dir, library.game)
        played = as_played(playset)
        versions = {GAME: library.game.version} | {
            m.key: m.supported_version for m in library.every_mod
        }

        def job(ctx: JobContext) -> PlaysetFindings:
            index = Indexer(library, cache, previous)(ctx)
            found = ConflictFinder(index, played, library, leave_out=leave_out)(ctx)
            return index, found, find_old_copies(found, versions)

        return job

    # Old copies

    def _check_old_copies(self) -> None:
        """Look for old copies in the selected playset, in the background."""
        if self._old_task is not None:
            self._old_task.cancel()
            self._old_task = None
        playset, library = self.selected_playset(), self.library
        if playset is None or library is None:
            self._show_old_copies(())
            return
        task = self.tasks.start(self._playset_job(playset, library))
        self._old_task = task
        task.succeeded.connect(lambda result: self._old_copies_found(result, task))
        task.failed.connect(
            lambda error: self.statusBar().showMessage(f"Checking for old copies failed: {error}")
        )

    def _old_copies_found(self, result: PlaysetFindings, task: Task) -> None:
        if task is not self._old_task:
            return  # the playset changed since
        self._old_task = None
        self._index = result[0]
        self._show_old_copies(result[2])
        self.old_copies_shown.emit(result[2])

    def _show_old_copies(self, copies: tuple[OldCopy, ...]) -> None:
        """The line above the mod list naming mods that replace newer files with old ones."""
        self.old_copies = copies
        if not copies:
            self.old_label.hide()
            return
        names = self.mod_names()
        lines = [describe(c, names) for c in copies[:OLD_COPIES_SHOWN]]
        if len(copies) > OLD_COPIES_SHOWN:
            lines.append(f"And {len(copies) - OLD_COPIES_SHOWN} more.")
        lines.append("Conflicts lists every file and missing object.")
        self.old_label.setText("\n".join(lines))
        self.old_label.show()

    def mod_names(self) -> dict[str, str]:
        """Every mod's name by Mod.key. A pinned copy is also named under its
        mod's own key, which conflict choices use, even if the mod is gone."""
        if self.library is None:
            return {}
        copies = {base_key(m.key): m.name for m in self.library.pinned}
        return copies | {m.key: m.name for m in self.library.every_mod}

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
        leave_out = patch_keys(playset.id, root, library.game)

        played = as_played(playset)

        def job(ctx: JobContext) -> PatchPlan:
            index = Indexer(library, cache, previous)(ctx)
            found = ConflictFinder(index, played, library, leave_out=leave_out)(ctx)
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

    def _delete_selected_patch(self) -> None:
        if playset := self.selected_playset():
            self.delete_patch(playset.id)

    def delete_patch(self, playset_id: str) -> None:
        """Delete a playset's patch mod, and take it out of every playset that
        has it. The conflict choices stay, so it can be generated again."""
        library, book = self.library, self.book
        if library is None or book is None or patched_from(patch_key(playset_id)) is None:
            return
        title = "Delete patch mod"
        if self._patch_task is not None:
            self.tell(title, "The patch mod is being written. Try again when it's done.")
            return
        if why := not_our_patch(playset_id, self._patch_dir, library.game):
            self.tell(title, f"The patch mod wasn't deleted: {why}")
            return
        key = patch_key(playset_id)
        source = book.get(playset_id)
        name = source.name if source else playset_id
        text = (
            f"Delete the patch mod for \u201c{name}\u201d? Your conflict choices stay, "
            "so you can generate it again."
        )
        users = [p for p in book.playsets if any(e.key == key for e in p.entries)]
        if users:
            names = ", ".join(f"\u201c{p.name}\u201d" for p in users)
            text += f" It's also taken out of {names}."
        if not self.confirm(title, text):
            return
        remove_patch(playset_id, library.game, self._patch_dir)
        for playset in users:
            book.update(ops.remove_mods(playset, [key]))
        # The choices are no longer in the game, so they count as not generated.
        path = resolutions_file(playset_id, self._choices_dir)
        open_now = self._choices
        choices = open_now if open_now and open_now.path == path else ResolutionBook.open(path)
        if choices.built:
            choices.mark_built(0)
        current = self.selected_playset()
        self._reload_playsets(current.id if current else "")
        self.statusBar().showMessage(f"Deleted the patch mod for {name}")
        self.rescan()  # the patch mod is gone from the mod folder

    def _patch_failed(self, error: Exception) -> None:
        if isinstance(error, PatchError | OSError):
            self.tell("Patch mod", f"The patch mod wasn't written: {error}")
        else:
            self.tell("Patch mod", f"Writing the patch mod failed: {type(error).__name__}: {error}")

    def _patch_done(self) -> None:
        self._patch_task = None
        self.progress.hide()

    # Pinned versions

    def show_pins(self) -> None:
        """Open the Pins window for the selected playset."""
        playset, library = self.selected_playset(), self.library
        if playset is None or library is None:
            return
        dialog = self._pins_dialog
        if dialog is None:
            dialog = self._pins_dialog = PinsDialog(self)
            dialog.pin_requested.connect(self.pin_mods)
            dialog.unpin_requested.connect(self.unpin_playset)
        dialog.set_state(playset, library, self._checked)
        self.show_dialog(dialog)

    def pin_mods(self, keys: Sequence[str]) -> None:
        """Copy these mods as they are on Steam now, and pin the selected playset to the copies."""
        playset, library = self.selected_playset(), self.library
        if playset is None or library is None or self._pin_task is not None or not keys:
            return
        previous = {p.key: p.snapshot for p in playset.pins}
        task = self.tasks.start(Pinner(self.snapshots, library, tuple(keys), previous))
        self._pin_task = task
        task.progress.connect(self._show_progress)
        task.succeeded.connect(lambda taken: self._pinned(playset.id, taken))
        task.failed.connect(self._pin_failed)
        task.finished.connect(self._pin_done)

    def _pinned(self, playset_id: str, taken: dict[str, Snapshot]) -> None:
        current = self.book.get(playset_id) if self.book else None
        if current is None or self.book is None:
            return
        changed = ops.set_pins(current, [Pin(key, s.id) for key, s in taken.items()])
        self.book.update(changed)
        if self.selected_playset() == changed:
            self._playset_changed(changed)
        self.statusBar().showMessage(f"Pinned {len(taken)} mod(s) in {changed.name}")
        self.pins_changed.emit(changed)
        # An accepted update leaves the old copy unused. The scan then finds the new copies.
        self._collect_snapshots(then=self.rescan)

    def _pin_failed(self, error: Exception) -> None:
        if isinstance(error, SnapshotError | OSError):
            self.tell("Pin", f"The mods weren't pinned: {error}")
        else:
            self.tell("Pin", f"Pinning failed: {type(error).__name__}: {error}")

    def _pin_done(self) -> None:
        self._pin_task = None
        self.progress.hide()

    def unpin_playset(self) -> None:
        playset = self.selected_playset()
        if playset is None or not playset.pins:
            return
        if not self.confirm(
            "Unpin",
            f"Play every mod in \u201c{playset.name}\u201d from Steam's folder again? "
            "The saved copies are deleted, unless another playset uses them.",
        ):
            return
        self._edit(ops.unpin)
        if self.book is not None:
            self.pins_changed.emit(self.book.get(playset.id))
        self._collect_snapshots(then=self.rescan)

    def _collect_snapshots(self, then: Callable[[], object] | None = None) -> None:
        """Delete the copies no playset uses any more, off the main thread."""
        keep = {p.snapshot for ps in (self.book.playsets if self.book else ()) for p in ps.pins}
        game, store = (self.library.game if self.library else None), self.snapshots
        task = self.tasks.start(lambda ctx: store.collect(keep, game))
        task.failed.connect(
            lambda error: self.statusBar().showMessage(f"Deleting unused copies failed: {error}")
        )
        if then is not None:
            task.finished.connect(then)

    def _check_pins(self, library: Library) -> None:
        """Compare every pinned mod with Steam's folder, to find updates."""
        if self._check_task is not None:
            self._check_task.cancel()
        playsets = self.book.playsets if self.book else ()
        ids = tuple(dict.fromkeys(p.snapshot for ps in playsets for p in ps.pins))
        if not ids:
            self._checked = {}
            self._show_pin_state(self.selected_playset())
            return
        task = self.tasks.start(PinChecker(self.snapshots, library, ids))
        self._check_task = task
        task.succeeded.connect(lambda checked: self._pins_found(checked, task))
        task.failed.connect(
            lambda error: self.statusBar().showMessage(f"Checking pinned mods failed: {error}")
        )

    def _pins_found(self, checked: dict[str, tuple[Snapshot, Drift]], task: Task) -> None:
        if task is not self._check_task:
            return  # a newer scan replaced this check
        self._check_task = None
        self._checked = checked
        self._show_pin_state(self.selected_playset())
        self.pins_checked.emit(checked)

    def _show_pin_state(self, playset: Playset | None) -> None:
        """The line above the mod list saying the playset is pinned, and what Steam updated."""
        if playset is not None and self.library is not None:
            dialog = self._pins_dialog
            if dialog is not None and dialog.isVisible():
                dialog.set_state(playset, self.library, self._checked)
        if playset is None or not playset.pins:
            self.pin_label.hide()
            return
        names = self.mod_names()
        updated = [
            names.get(p.key, p.key)
            for p in playset.pins
            if (found := self._checked.get(p.snapshot)) and found[1].updated
        ]
        text = f"Pinned: {len(playset.pins)} Workshop mod(s) play from saved copies."
        if updated:
            text += (
                f" {len(updated)} have an update on Steam: {', '.join(updated)}. "
                "Open Pins… to see what changed."
            )
        self.pin_label.setText(text)
        self.pin_label.show()

    # Saves

    def _scan_saves(self) -> None:
        """Read the save folders, off the main thread. Unchanged files come from the cache."""
        if self.library is None or not self.saves_on:
            return
        if self._saves_task is not None:
            self._saves_task.cancel()
        task = self.tasks.start(SaveScanner.for_game(self.library.game))
        self._saves_task = task
        task.succeeded.connect(lambda saves: self._saves_read(saves, task))
        task.failed.connect(lambda error: self._saves_failed(error, task))

    def _saves_failed(self, error: Exception, task: Task) -> None:
        if task is not self._saves_task:
            return
        self._saves_task = None
        self.statusBar().showMessage(f"Reading your saves failed: {error}")
        if self._build_waiting:
            self._build_waiting = ""
            self.tell("Build", f"The build didn't start: your saves couldn't be read. {error}")

    def _saves_read(self, saves: tuple[Save, ...], task: Task) -> None:
        if task is not self._saves_task:
            return  # a newer scan replaced this one
        self._saves_task = None
        self.saves = saves
        if self._bind_played:
            self._bind_new_saves(saves)
        self._show_saves(self.selected_playset())
        self.saves_found.emit(saves)
        if self._build_waiting:
            waiting, self._build_waiting = self._build_waiting, ""
            playset = self.selected_playset()
            if playset is not None and playset.id == waiting:
                self.build_mod()
            else:
                self.statusBar().showMessage("Build cancelled: another playset was chosen")

    def _bind_new_saves(self, saves: tuple[Save, ...]) -> None:
        """Bind the saves written since Play to the playset it played (decision 82)."""
        self._bind_played = False
        played, book, bindings = self._played, self.book, self.bindings
        if played is None or book is None or bindings is None:
            return
        playset = book.get(played[0])
        new = played_since(saves, bindings, played[1])
        if playset is None or not new:
            return
        bindings.bind(new, playset.id)
        names = ", ".join(s.empire for s in saves if s.folder in new)
        self.statusBar().showMessage(f"New save(s) bound to {playset.name}: {names}")

    def _read_saves_setting(self) -> bool:
        """Turn the saves features on or off from the game's settings.txt: on
        only with cloud autosaves off (decision 91). True if they just came on."""
        data_dir = self.library.game.data_dir if self.library else None
        on = data_dir is not None and autosaves_to_cloud(data_dir) is False
        came_on = on and not self.saves_on
        self.saves_on = on
        for act in (self.saves_action, self.continue_action):
            act.setVisible(on)
        self.saves_button.setVisible(on)
        self.continue_button.setVisible(on)
        if not on:
            self.saves_label.hide()
            if self._saves_dialog is not None:
                self._saves_dialog.hide()
        return came_on

    def show_saves(self) -> None:
        """Open the Saves window for the selected playset, and read the saves again."""
        playset = self.selected_playset()
        if playset is None or self.library is None or not self.saves_on:
            return
        dialog = self._saves_dialog
        if dialog is None:
            dialog = self._saves_dialog = SavesDialog(self)
            dialog.bind_requested.connect(self.bind_saves)
            dialog.unbind_requested.connect(self.unbind_saves)
            dialog.continue_requested.connect(self.continue_playset)
        self._show_saves(playset, opening=True)
        self.show_dialog(dialog)
        self._scan_saves()  # finds files written since

    def _show_saves(self, playset: Playset | None, *, opening: bool = False) -> None:
        """The Saves button's count, the line above the mod list, and the Saves
        window if it's open."""
        if not self.saves_on:
            self.saves_label.hide()
            return
        saves, bindings, library = self.saves, self.bindings, self.library
        bound = bound_saves(saves or (), bindings, playset.id) if playset and bindings else []
        self.saves_button.setText(f"Saves ({len(bound)})" if bound else "Saves")
        self.continue_action.setEnabled(bool(bound))
        newest = bound[0] if bound else None
        self.continue_button.setToolTip(
            f"Play and open {newest.empire}, the newest save of this playset"
            if newest
            else "This playset has no saves yet"
        )
        checks = self._check_saves(playset, bound) if playset and library else {}
        differ = [s.empire for s in bound if checks[s.folder].mods_differ]
        if differ:
            self.saves_label.setText(
                f"{len(differ)} save(s) of this playset were made with other mods than it "
                f"has now: {', '.join(differ)}. Saves… says which."
            )
        self.saves_label.setVisible(bool(differ))

        dialog = self._saves_dialog
        if dialog is None or playset is None or self.book is None or bindings is None:
            return
        if opening or dialog.isVisible():
            playsets = self.book.playsets
            unbound = unbound_saves(saves or (), bindings, {p.id for p in playsets})
            played = {p.id: played_mods(p, library) for p in playsets} if library else {}
            by_id = {p.id: p for p in playsets}
            suggested = {f: by_id[pid] for f, pid in suggest(unbound, bindings, played).items()}
            dialog.set_state(
                playset, playsets, None if saves is None else bound, unbound, checks, suggested
            )

    def _check_saves(self, playset: Playset, saves: Sequence[Save]) -> dict[str, SaveCheck]:
        """How each of a playset's saves compares with the playset now."""
        library, bindings = self.library, self.bindings
        if library is None or bindings is None or not saves:
            return {}
        mods = played_mods(playset, library)
        build = ""
        for entry in playset.entries:
            source = built_from(entry.key)
            stamp = load_stamp(self._build_dir, source) if source else None
            if stamp is not None and stamp.built_playset == playset.id:
                build = stamp.built
        patch_written = 0
        if any(e.key == patch_key(playset.id) and e.enabled for e in playset.entries):
            with contextlib.suppress(OSError):  # not generated yet
                patch_written = (self._patch_dir / playset.id / "descriptor.mod").stat().st_mtime_ns
        checks: dict[str, SaveCheck] = {}
        for save in saves:
            binding = bindings.get(save.folder)
            kept = binding is not None and binding.build == build
            checks[save.folder] = check_save(
                save,
                mods,
                library.game.version,
                build="" if kept else build,
                patch_written=patch_written,
            )
        return checks

    def bind_saves(self, folders: Sequence[str], playset_id: str) -> None:
        """Bind saves to a playset, or move them to it."""
        if self.bindings is None or self.book is None or not folders:
            return
        playset = self.book.get(playset_id)
        if playset is None:
            return
        self.bindings.bind(folders, playset_id)
        self.statusBar().showMessage(f"{len(folders)} save(s) now belong to {playset.name}")
        self._show_saves(self.selected_playset())

    def unbind_saves(self, folders: Sequence[str]) -> None:
        if self.bindings is None or not folders:
            return
        self.bindings.unbind(folders)
        self.statusBar().showMessage(f"Unbound {len(folders)} save(s). Their files stay")
        self._show_saves(self.selected_playset())

    def _saves_unbound_text(self, playset: Playset) -> str:
        """For a confirm box: what happens to a playset's saves when it goes."""
        count = len(self.bindings.saves_of(playset.id)) if self.bindings and self.saves_on else 0
        if not count:
            return ""
        return (
            f" Its {count} saves are unbound from it. Their files stay where they are."
            if count > 1
            else " Its save is unbound from it. The files stay where they are."
        )

    # Building one mod

    def build_mod(self) -> None:
        """Merge the selected playset, with its patch mod, into one mod."""
        playset, library = self.selected_playset(), self.library
        if playset is None or library is None or self._build_task is not None:
            return
        choices = self.choices_for(playset)
        if (
            choices.resolutions
            and choices.built != choices_digest(choices.resolutions)
            and not self.confirm(
                "Build one mod",
                "Your conflict choices changed since the patch mod was generated, so the "
                "build won't have the latest ones. Build anyway?",
            )
        ):
            return
        if self._saves_needed(playset):
            # Asking about the built playset's saves needs them read, off the main thread.
            self._build_waiting = playset.id
            self.statusBar().showMessage("Reading your saves before the build…")
            if self._saves_task is None:
                self._scan_saves()
            return
        go, rebuild = self._ask_about_saves(playset)
        if not go:
            return
        builder = Builder(
            library, as_played(playset), self._index_cache, self._build_dir, self._index
        )
        task = self.tasks.start(builder)
        self._build_task = task
        self._rebuild = rebuild
        task.progress.connect(self._show_progress)
        task.succeeded.connect(lambda result: self._built(result, playset))
        task.failed.connect(self._build_failed)
        task.finished.connect(self._build_done)

    def _built(self, result: tuple[BuildRecord, Index], playset: Playset) -> None:
        record, self._index = result
        book = self.book
        if book is not None and book.get(record.built_playset) is None:
            # A playset that plays just the built mod, with the same DLC.
            made = book.create(
                book.unused_name(f"{playset.name} (built)"),
                [PlaysetEntry(build_key(playset.id), True, build_name(playset))],
            )
            book.update(msgspec_replace(made, disabled_dlcs=playset.disabled_dlcs))
            record.built_playset = made.id
            save_record(self._build_dir, record)
        if self._rebuild is not None:
            self._apply_rebuild(record, self._rebuild)
        current = self.selected_playset()
        self._shown_after_scan = (record, current is not None and current.id == playset.id)
        self.statusBar().showMessage(f"Built {playset.name} into one mod")
        self.build_finished.emit(record)
        self.rescan()  # finds the built mod, then _show_finished_build

    def _show_finished_build(self) -> None:
        """Once the scan has found the built mod: select the playset that plays it
        if the source playset is still selected (decision 95), then open the report."""
        if self._shown_after_scan is None:
            return
        record, still_selected = self._shown_after_scan
        self._shown_after_scan = None
        book = self.book
        plays_it = book.get(record.built_playset) if book and record.built_playset else None
        if plays_it is not None:
            current = self.selected_playset()
            if still_selected and current is not None and current.id == record.playset:
                self._fill_playsets(plays_it.id)
                self.statusBar().showMessage(f"Built {record.name}. {plays_it.name} plays it")
            else:
                self.statusBar().showMessage(
                    f"Built {record.name}. Select {plays_it.name} to play it"
                )
        self.show_build_report(record)

    def _build_failed(self, error: Exception) -> None:
        if isinstance(error, BuildError | OSError):
            self.tell("Build", f"The mod wasn't built: {error}")
        else:
            self.tell("Build", f"Building failed: {type(error).__name__}: {error}")

    def _build_done(self) -> None:
        self._build_task = None
        self._rebuild = None  # a failed or cancelled build changes no binding
        self.progress.hide()

    def _built_playset(self, playset: Playset) -> Playset | None:
        """The playset that plays this playset's build, if it was built."""
        stamp = load_stamp(self._build_dir, playset.id)
        book = self.book
        return book.get(stamp.built_playset) if book and stamp and stamp.built_playset else None

    def _saves_needed(self, playset: Playset) -> bool:
        """The built playset has saves, and they haven't been read yet."""
        built, bindings = self._built_playset(playset), self.bindings
        return (
            self.saves_on
            and self.saves is None
            and built is not None
            and bool(bindings and bindings.saves_of(built.id))
        )

    def _ask_about_saves(self, playset: Playset) -> tuple[bool, RebuildChoice | None]:
        """Before a rebuild, ask what to do with the saves bound to the built
        playset (decision 85). Whether to build, and the answer if one was asked."""
        library, bindings = self.library, self.bindings
        built = self._built_playset(playset)
        if library is None or bindings is None or built is None or not self.saves_on:
            return True, None
        folders = bindings.saves_of(built.id)
        saves = [s for s in self.saves or () if s.folder in folders]
        if not saves:
            return True, None
        record = load_record(self._build_dir, playset.id)
        before = [record.names.get(k, k) for k in record.order] if record else []
        changes = describe_changes(*compare_mods(before, played_mods(playset, library)))
        choice = self.ask_rebuild(playset, built, saves, changes)
        return choice is not None, choice

    def _apply_rebuild(self, record: BuildRecord, choice: RebuildChoice) -> None:
        """After a successful rebuild: keep the chosen saves with the new build,
        unbind the rest, and trash their local files if asked."""
        self._rebuild = None
        bindings, library = self.bindings, self.library
        if bindings is None or library is None:
            return
        # A bound save whose files are gone wasn't asked about. It stays, as kept.
        dropped = sorted(choice.dropped)
        bindings.keep(bindings.saves_of(record.built_playset) - choice.dropped, record.built)
        if dropped:
            bindings.unbind(dropped)
        if choice.trash and dropped:
            self._trash_saves(dropped, library.game.data_dir)
        self._scan_saves()

    def _trash_saves(self, folders: Sequence[str], data_dir: Path) -> None:
        """Move these saves' local folders to the trash. Steam Cloud's stay."""
        title = "Rebuild"
        if processes.running(processes.GAME):
            self.tell(title, "Stellaris is running, so no save files were moved to the trash.")
            return
        local = local_save_dir(data_dir)
        failed = [
            path for folder in folders if (path := local / folder).is_dir() and not self.trash(path)
        ]
        if failed:
            listed = "\n".join(f"  {p}" for p in failed)
            self.tell(title, f"These couldn't be moved to the trash:\n{listed}")

    def _delete_selected_build(self) -> None:
        if playset := self.selected_playset():
            self.delete_build(playset.id)

    def delete_build(self, playset_id: str) -> None:
        """Delete the mod Cold Steel built from a playset, and the playset that
        plays just that mod. Nothing else can be deleted this way."""
        library, book = self.library, self.book
        if library is None or book is None or built_from(build_key(playset_id)) is None:
            return
        title = "Delete built mod"
        if self._build_task is not None:
            self.tell(title, "A build is running. Try again when it's done.")
            return
        if why := not_ours(playset_id, self._build_dir, library.game):
            self.tell(title, f"The mod wasn't deleted: {why}")
            return
        record = load_record(self._build_dir, playset_id)
        source = book.get(playset_id)
        name = source.name if source else record.name if record else playset_id
        plays_it = book.get(record.built_playset) if record and record.built_playset else None
        text = f"Delete the mod built from \u201c{name}\u201d? That playset and its mods stay."
        if plays_it is not None:
            text += f" \u201c{plays_it.name}\u201d, which plays just the built mod, goes too."
            text += self._saves_unbound_text(plays_it)
        if not self.confirm(title, text):
            return
        remove_build(playset_id, self._build_dir, library.game)
        current = self.selected_playset()
        if plays_it is not None:
            self._forget_playset(plays_it)
        self._reload_playsets(current.id if current and current != plays_it else "")
        self.statusBar().showMessage(f"Deleted the mod built from {name}")
        self.rescan()  # the built mod is gone from the mod folder

    def show_build_report(self, record: BuildRecord | None = None) -> None:
        """Where every file of the selected playset's build came from."""
        playset = self.selected_playset()
        if record is None and playset is not None:
            record = load_record(self._build_dir, playset.id)
        if record is None:
            return
        dialog = self._build_dialog
        if dialog is None:
            dialog = self._build_dialog = BuildDialog(self)
        dialog.set_record(record)
        self.show_dialog(dialog)

    # Questions for the user. Tests replace these.

    def ask_text(self, title: str, label: str, text: str) -> str | None:
        value, ok = QInputDialog.getText(self, title, label, QLineEdit.EchoMode.Normal, text)
        return value.strip() if ok and value.strip() else None

    def confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(self, title, text)
        return answer == QMessageBox.StandardButton.Yes

    def trash(self, path: Path) -> bool:
        return QFile(str(path)).moveToTrash()

    def open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def tell(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def ask_dlc(self, playset: Playset) -> tuple[str, ...] | None:
        """The DLC folders to turn off, or None if cancelled."""
        if self.library is None:
            return None
        dialog = DlcDialog(self.library.dlcs, playset.disabled_dlcs, self)
        return dialog.disabled() if dialog.exec() else None

    def ask_rebuild(
        self, playset: Playset, built: Playset, saves: Sequence[Save], changes: str
    ) -> RebuildChoice | None:
        """Which saves to keep with the rebuilt playset, or None if cancelled."""
        dialog = RebuildDialog(
            playset.name,
            built.name,
            saves,
            changes,
            game_running=processes.running(processes.GAME),
            parent=self,
        )
        return dialog.choice() if dialog.exec() else None

    def ask_settings(self) -> Settings | None:
        """The settings the user chose, or None if cancelled."""
        found = None
        if self.library is not None and not self.settings.game_data_dir:
            found = self.library.game.data_dir
        cloud = autosaves_to_cloud(self.library.game.data_dir) if self.library else None
        dialog = SettingsDialog(
            self.settings, found, self, game_found=self.library is not None, cloud_autosaves=cloud
        )
        return dialog.settings() if dialog.exec() else None

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

    # Help

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def show_shortcuts(self) -> None:
        ShortcutsDialog(self.menuBar(), self).exec()

    def show_welcome(self) -> None:
        WelcomeDialog(self).exec()

    def show_about(self) -> None:
        QMessageBox.about(self, "About Cold Steel", ABOUT)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt override)
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            # Drawn icons take the text colour, so they're drawn again for the new theme.
            for act, name, draw in self._sidebar_icons:
                act.setIcon(icons.theme_icon(name, draw))
        elif (
            event.type() == QEvent.Type.ActivationChange
            and self.isActiveWindow()
            # Back from the game or a text editor: cloud autosaves may have been turned off.
            and self._read_saves_setting()
        ):
            self._scan_saves()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        self.tasks.cancel_all()
        self.tasks.wait(5000)
        super().closeEvent(event)


def _continue_game(save: Save) -> ContinueGame:
    """What continue_game.json says to open a save at its newest readable file."""
    file = next((f for f in save.files if f.info), save.newest)
    return ContinueGame(save.folder, file.name, save.empire, file.info.date if file.info else "")


def _wrap_width(font: QFont, text: str, lines: int) -> int:
    """The narrowest width that wraps `text` onto `lines` lines, as the table wraps it."""
    full = QFontMetrics(font).horizontalAdvance(text)
    low, high = full // lines, full  # high always fits
    while low < high:
        mid = (low + high) // 2
        if line_count(font, text, mid) <= lines:
            high = mid
        else:
            low = mid + 1
    return high
