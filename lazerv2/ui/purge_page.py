# manual purge: targets -> filters -> run

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar,
                             QPushButton, QScrollArea, QStackedWidget,
                             QVBoxLayout, QWidget)

from lazerv2.ui.channel_picker import ChannelPicker
from lazerv2.ui.filters import FilterPanel
from lazerv2.ui.widgets import StepIndicator

STEP_TARGETS, STEP_FILTERS, STEP_RUN = 0, 1, 2


class PurgePage(QWidget):
    purge_requested = pyqtSignal(list, dict)
    preview_requested = pyqtSignal(list, dict)
    stop_requested = pyqtSignal()
    confirm_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("accent", "red")
        self._selected = []
        self._running = False
        self._previewing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(16)

        title = QLabel("Manual Purge")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self.steps = StepIndicator(["Targets", "Filters", "Run"])
        layout.addWidget(self.steps)
        layout.addSpacing(4)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_targets())
        self.stack.addWidget(self._build_filters())
        self.stack.addWidget(self._build_run())
        layout.addWidget(self.stack, 1)

        self.filter_panel.kw_input.changed.connect(self._clear_preview)
        self.filter_panel.mode_selector.changed.connect(self._clear_preview)
        self.filter_panel.time_selector.changed.connect(self._clear_preview)
        self.filter_panel.media_selector.changed.connect(self._clear_preview)

        self.set_step(STEP_TARGETS)


    def _action_bar(self, left_widget, right_widget):
        bar = QFrame()
        bar.setObjectName("ActionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)
        row.addWidget(left_widget)
        row.addStretch()
        row.addWidget(right_widget)
        return bar

    def _build_targets(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.picker = ChannelPicker(server_wide=True)
        self.picker.selection_changed.connect(self._on_selection_changed)
        layout.addWidget(self.picker, 1)

        self.selection_label = QLabel("Nothing selected")
        self.selection_label.setObjectName("Muted")

        self.next_btn = QPushButton("Next: Filters  ›")
        self.next_btn.setObjectName("PrimaryBtn")
        self.next_btn.setFixedWidth(180)
        self.next_btn.setEnabled(False)
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.clicked.connect(self._go_to_filters)

        layout.addWidget(self._action_bar(self.selection_label, self.next_btn))
        return page

    def _build_filters(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.filter_panel = FilterPanel()
        scroll.setWidget(self.filter_panel)
        layout.addWidget(scroll, 1)

        back_btn = QPushButton("‹  Back")
        back_btn.setObjectName("GhostBtn")
        back_btn.setFixedWidth(120)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(lambda: self.set_step(STEP_TARGETS))
        self.back_btn = back_btn

        self.start_btn = QPushButton("Scan && delete…")
        self.start_btn.setObjectName("DangerBtn")
        self.start_btn.setFixedWidth(180)
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.clicked.connect(self._start)

        self.preview_btn = QPushButton("Preview")
        self.preview_btn.setObjectName("GhostBtn")
        self.preview_btn.setFixedWidth(120)
        self.preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview_btn.clicked.connect(self._preview)

        self.preview_label = QLabel("")
        self.preview_label.setObjectName("Muted")
        self.preview_label.setWordWrap(True)

        bar = QFrame()
        bar.setObjectName("ActionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)
        row.addWidget(back_btn)
        row.addWidget(self.preview_label, 1)
        row.addWidget(self.preview_btn)
        row.addWidget(self.start_btn)
        layout.addWidget(bar)
        return page

    def _build_run(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        header = QLabel("PROGRESS")
        header.setObjectName("SectionHeader")
        self.target_label = QLabel("")
        self.target_label.setObjectName("Dim")
        self.target_label.setWordWrap(True)
        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        self.progress_bar = QProgressBar()

        card_layout.addWidget(header)
        card_layout.addWidget(self.target_label)
        card_layout.addWidget(self.status_label)
        card_layout.addWidget(self.progress_bar)

        layout.addWidget(card)
        layout.addStretch()

        self.restart_btn = QPushButton("‹  New purge")
        self.restart_btn.setObjectName("GhostBtn")
        self.restart_btn.setFixedWidth(150)
        self.restart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restart_btn.clicked.connect(self._restart)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("DangerBtn")
        self.stop_btn.setFixedWidth(120)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.clicked.connect(self.stop_requested.emit)

        self.run_bar = self._action_bar(self.restart_btn, self.stop_btn)
        layout.addWidget(self.run_bar)

        # shown once the scan is done: nothing is deleted until Delete is hit
        self.confirm_bar = QFrame()
        self.confirm_bar.setObjectName("ActionBar")
        row = QHBoxLayout(self.confirm_bar)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)

        self.confirm_label = QLabel("")
        self.confirm_label.setWordWrap(True)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("GhostBtn")
        self.cancel_btn.setFixedWidth(120)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._cancel_confirmation)

        self.confirm_btn = QPushButton("Delete")
        self.confirm_btn.setObjectName("DangerBtn")
        self.confirm_btn.setMinimumWidth(180)
        self.confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_btn.clicked.connect(self._confirm)

        row.addWidget(self.confirm_label, 1)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.confirm_btn)
        self.confirm_bar.setVisible(False)
        layout.addWidget(self.confirm_bar)
        return page

    def on_awaiting_confirmation(self, total, backup_dir=None):
        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(0)
        backup = (f" A backup is saved to {backup_dir} first." if backup_dir
                  else " No backup will be saved.")
        self.confirm_label.setText(
            f"{total} message{'s' if total != 1 else ''} will be permanently deleted. "
            "This can't be undone." + backup)
        self.confirm_btn.setText(f"Delete {total} message{'s' if total != 1 else ''}")
        self.confirm_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.run_bar.setVisible(False)
        self.confirm_bar.setVisible(True)
        # Cancel is the default; Enter must never be the thing that deletes
        self.cancel_btn.setFocus()

    def _hide_confirmation(self):
        self.confirm_bar.setVisible(False)
        self.run_bar.setVisible(True)

    def _confirm(self):
        # one click only: a double click must not queue anything twice
        self.confirm_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self._hide_confirmation()
        self.confirm_requested.emit()

    def _cancel_confirmation(self):
        self.confirm_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self._hide_confirmation()
        self.cancel_requested.emit()


    def set_step(self, index):
        self.stack.setCurrentIndex(index)
        self.steps.set_step(index)

    def _on_selection_changed(self, count):
        self._clear_preview()
        self.next_btn.setEnabled(count > 0)
        if count:
            self.selection_label.setText(f"{count} channel{'s' if count != 1 else ''} selected")
        else:
            self.selection_label.setText("Nothing selected")

    def _clear_preview(self, *_args):
        self.preview_label.setText("")

    def _go_to_filters(self):
        # snapshot here so Back and forward again keeps the selection
        self._selected = self.picker.get_selected()
        if not self._selected:
            return
        self.set_step(STEP_FILTERS)

    def _start(self):
        if not self._selected:
            self.set_step(STEP_TARGETS)
            return
        names = ", ".join(c["name"] for c in self._selected[:3])
        if len(self._selected) > 3:
            names += f" +{len(self._selected) - 3} more"
        self.target_label.setText(f"{names}  —  {self.filter_panel.summary()}")
        self.preview_label.setText("")
        self.set_step(STEP_RUN)
        self.purge_requested.emit(self._selected, self.filter_panel.get_data())

    def _preview(self):
        if self._running:
            return
        self._selected = self.picker.get_selected()
        if not self._selected:
            self.set_step(STEP_TARGETS)
            return
        names = ", ".join(c["name"] for c in self._selected[:3])
        if len(self._selected) > 3:
            names += f" +{len(self._selected) - 3} more"
        self.target_label.setText(f"{names}  —  {self.filter_panel.summary()}")
        self.set_step(STEP_RUN)
        self.preview_requested.emit(self._selected, self.filter_panel.get_data())

    def abort_preview(self):
        if self._running:
            return
        self._previewing = False
        self.set_step(STEP_FILTERS)

    def _restart(self):
        if self._running:
            return
        self.picker.clear_selection()
        self.filter_panel.reset()
        self._selected = []
        self._previewing = False
        self.preview_label.setText("")
        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(0)
        self.status_label.setText("Idle")
        self.set_step(STEP_TARGETS)


    def set_data(self, dms, servers):
        self.picker.set_data(dms, servers)

    def on_started(self, preview=False):
        self._running = True
        self._previewing = bool(preview)
        self._hide_confirmation()
        self.stop_btn.setEnabled(True)
        self.restart_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.back_btn.setEnabled(False)
        if preview:
            self.preview_label.setText("Scanning...")
        self.progress_bar.setMaximum(0)   # indeterminate until the scan finishes
        self.progress_bar.setValue(0)

    def on_total(self, total):
        if self._previewing:
            return
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(0)

    def on_progress(self, done, total):
        if total and self.progress_bar.maximum() != total:
            self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(min(done, self.progress_bar.maximum()))

    def on_status(self, text):
        self.status_label.setText(text)

    def on_preview_finished(self, total):
        self._running = False
        self._previewing = False
        self.stop_btn.setEnabled(False)
        self.restart_btn.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.back_btn.setEnabled(True)
        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(0)
        self.preview_label.setText(f"{total} message(s) match. Nothing was deleted.")
        self.set_step(STEP_FILTERS)

    def on_finished(self, deleted=0, gone=0, failed=0):
        self._running = False
        self._previewing = False
        self._hide_confirmation()
        self.progress_bar.setMaximum(max(self.progress_bar.maximum(), 1))
        self.stop_btn.setEnabled(False)
        self.restart_btn.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.back_btn.setEnabled(True)
        if not self.status_label.text().strip():
            self.status_label.setText(f"Finished: deleted {deleted}.")
