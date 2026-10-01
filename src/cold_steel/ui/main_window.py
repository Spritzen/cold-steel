"""The main Cold Steel window: playsets on the left, the mod list on the right."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QFont, QImage, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library, Playset
from cold_steel.paradox.game import GameNotFound
from cold_steel.store import paths
from cold_steel.ui.mod_table import Column, Membership, ModFilter, ModTableModel
from cold_steel.ui.tasks import Task, TaskRunner
from cold_steel.ui.thumbnails import SHOWN_SIZE, blank_thumbnail, thumbnail_job

type Scan = Callable[[JobContext], Library]

ROW_HEIGHT = SHOWN_SIZE.height() + 4


class MainWindow(QMainWindow):
    # The user picked a Steam folder by hand. The app saves it and rescans.
    steam_dir_chosen = Signal(Path)
    # A scan finished and its result is on screen. For tests and timing.
    library_shown = Signal(object)

    def __init__(
        self,
        scan: Scan | None = None,
        thumbnail_cache: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cold Steel")
        self.resize(1200, 760)

        self.tasks = TaskRunner(self)
        self.library: Library | None = None
        self._scan = scan
        self._scan_task: Task | None = None
        self._thumbnail_task: Task | None = None
        self._thumbnail_cache = thumbnail_cache or paths.cache_dir() / "thumbnails"

        self.model = ModTableModel()
        self.filter = ModFilter(self.model)

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

    def _build_library_page(self) -> QWidget:
        self.playset_list = QListWidget()
        self.playset_list.currentRowChanged.connect(self._playset_selected)

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

        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.tag_box)
        filters.addWidget(self.membership_box)
        filters.addWidget(self.outdated_box)

        self.problems_label = QLabel(wordWrap=True)
        self.problems_label.setStyleSheet("color: #d9534f;")
        self.problems_label.hide()

        self.table = QTableView()
        self.table.setModel(self.filter)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setIconSize(SHOWN_SIZE)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(Column.NAME, QHeaderView.ResizeMode.Stretch)
        for col in (Column.POSITION, Column.VERSION, Column.SUPPORTED, Column.SOURCE):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.resizeSection(Column.TAGS, 240)

        right = QWidget()
        layout = QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(filters)
        layout.addWidget(self.problems_label)
        layout.addWidget(self.table)

        splitter = QSplitter()
        splitter.addWidget(self.playset_list)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 960])
        return splitter

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
        self.model.set_library(library)
        self.filter.set_library(library)
        self._fill_playsets(library)
        self._fill_tags(library)

        self.problems_label.setText("\n".join(library.problems))
        self.problems_label.setVisible(bool(library.problems))
        self.game_label.setText(f"Stellaris {library.game.version_name}")
        self.game_label.setToolTip(str(library.game.install_dir))
        outdated = sum(library.is_outdated(m) for m in library.mods)
        self.statusBar().showMessage(f"{len(library.mods)} mods, {outdated} outdated")
        self.pages.setCurrentIndex(0)
        self._load_thumbnails(library)
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

    def _fill_playsets(self, library: Library) -> None:
        current = self.playset_list.currentItem()
        previous = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.playset_list.blockSignals(True)
        self.playset_list.clear()
        everything = QListWidgetItem(f"All mods ({len(library.mods)})")
        everything.setData(Qt.ItemDataRole.UserRole, None)
        self.playset_list.addItem(everything)
        selected = 0
        for row, playset in enumerate(library.playsets, 1):
            item = QListWidgetItem(f"{playset.name} ({len(playset.entries)})")
            item.setData(Qt.ItemDataRole.UserRole, playset.id)
            if playset.active:
                font = QFont()
                font.setBold(True)
                item.setFont(font)
                item.setToolTip("The launcher's active playset")
            if playset.id == previous:
                selected = row
            self.playset_list.addItem(item)
        self.playset_list.blockSignals(False)
        self.playset_list.setCurrentRow(selected)
        self._playset_selected(selected)

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
        if item is None or self.library is None:
            return None
        pid = item.data(Qt.ItemDataRole.UserRole)
        return next((p for p in self.library.playsets if p.id == pid), None)

    def _playset_selected(self, row: int) -> None:
        playset = self.selected_playset()
        self.model.set_playset(playset)
        self.filter.set_playset(playset)
        self.table.setColumnHidden(Column.POSITION, playset is None)
        if playset is None:
            self.table.sortByColumn(Column.NAME, Qt.SortOrder.AscendingOrder)
        else:
            self.table.sortByColumn(Column.POSITION, Qt.SortOrder.AscendingOrder)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        self.tasks.cancel_all()
        self.tasks.wait(5000)
        super().closeEvent(event)
