# the monitor list plus an inline add screen

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QStackedWidget, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from lazerv2.ui.channel_picker import ChannelPicker
from lazerv2.ui.widgets import Segmented

MODE_LIST, MODE_ADD = 0, 1

TTL_PRESETS = [("Instant", 0), ("30s", 30), ("5m", 300), ("1h", 3600)]


class GuardPage(QWidget):
    monitors_added = pyqtSignal(list, str, int, bool)   # channels, trigger, ttl, whole word
    monitors_removed = pyqtSignal(list)                 # channel ids
    entry_edited = pyqtSignal(str, str, object)         # channel id, field, value
    guard_toggled = pyqtSignal(bool)                    # armed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("accent", "blue")
        self.channel_ids = []
        self.entries = {}
        self.updating_table = False
        self._guard_active = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(14)

        self.title = QLabel("Conversation Guard")
        self.title.setObjectName("PageTitle")
        layout.addWidget(self.title)

        self.subtitle = QLabel("Watches channels: a trigger word wipes history; TTL removes only new messages.")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_list())
        self.stack.addWidget(self._build_add())
        layout.addWidget(self.stack, 1)


    def _build_list(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # the one switch for the whole account's guard; the daemon and the
        # tray menu read and write the same flag
        guard_row = QHBoxLayout()
        guard_row.setSpacing(12)
        guard_header = QLabel("GUARD")
        guard_header.setObjectName("SectionHeader")
        self.guard_selector = Segmented([("Armed", True), ("Off", False)])
        self.guard_selector.setFixedWidth(220)
        self.guard_selector.changed.connect(self._on_guard_selected)
        self.guard_state_label = QLabel("")
        self.guard_state_label.setObjectName("Muted")
        self.guard_state_label.setWordWrap(True)
        guard_row.addWidget(guard_header)
        guard_row.addWidget(self.guard_selector)
        guard_row.addWidget(self.guard_state_label, 1)
        layout.addLayout(guard_row)

        hint = QLabel("Trigger, TTL and Match cells are editable. Changing any of them starts a fresh guard window. "
                      "TTL is in seconds; 0 (or 'Instant') disables age-based deletion.")
        hint.setObjectName("Dim")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.watchlist_table = QTableWidget(0, 4)
        self.watchlist_table.setHorizontalHeaderLabels(["Target", "Trigger", "TTL", "Match"])
        self.watchlist_table.horizontalHeader().setStretchLastSection(True)
        self.watchlist_table.verticalHeader().setVisible(False)
        self.watchlist_table.setAlternatingRowColors(True)
        self.watchlist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.watchlist_table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.watchlist_table, 1)

        self.empty_label = QLabel("No monitors yet. Add one to arm Conversation Guard.")
        self.empty_label.setObjectName("Dim")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        add_btn = QPushButton("Add monitor")
        add_btn.setObjectName("PrimaryBtn")
        add_btn.setFixedWidth(160)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self.enter_add_mode)

        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.setObjectName("GhostBtn")
        self.remove_btn.setFixedWidth(160)
        self.remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_btn.clicked.connect(self._remove_selected)

        buttons.addWidget(add_btn)
        buttons.addWidget(self.remove_btn)
        buttons.addStretch()
        layout.addLayout(buttons)
        return page


    def _build_add(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        split = QHBoxLayout()
        split.setSpacing(18)

        self.picker = ChannelPicker()
        self.picker.selection_changed.connect(self._on_picker_selection)
        split.addWidget(self.picker, 3)

        config = QWidget()
        config_layout = QVBoxLayout(config)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(8)

        trigger_header = QLabel("TRIGGER WORD")
        trigger_header.setObjectName("SectionHeader")
        self.trigger_input = QLineEdit()
        self.trigger_input.setPlaceholderText("e.g. nukeit")
        trigger_hint = QLabel("When you post this, your history in the channel is wiped. "
                              "Leave blank for TTL-only.")
        trigger_hint.setObjectName("Dim")
        trigger_hint.setWordWrap(True)

        match_header = QLabel("TRIGGER MATCH")
        match_header.setObjectName("SectionHeader")
        self.match_selector = Segmented([("Whole word", True), ("Contains", False)])
        match_hint = QLabel("Whole word matches 'nuke' but not 'nuked'.")
        match_hint.setObjectName("Dim")
        match_hint.setWordWrap(True)

        ttl_header = QLabel("TTL")
        ttl_header.setObjectName("SectionHeader")
        self.ttl_selector = Segmented(TTL_PRESETS)
        ttl_hint = QLabel("Delete only your messages sent after this monitor is added, once they are older than this.")
        ttl_hint.setObjectName("Dim")
        ttl_hint.setWordWrap(True)

        config_layout.addWidget(trigger_header)
        config_layout.addWidget(self.trigger_input)
        config_layout.addWidget(trigger_hint)
        config_layout.addSpacing(14)
        config_layout.addWidget(match_header)
        config_layout.addWidget(self.match_selector)
        config_layout.addWidget(match_hint)
        config_layout.addSpacing(14)
        config_layout.addWidget(ttl_header)
        config_layout.addWidget(self.ttl_selector)
        config_layout.addWidget(ttl_hint)
        config_layout.addStretch()
        split.addWidget(config, 2)

        layout.addLayout(split, 1)

        bar = QFrame()
        bar.setObjectName("ActionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)

        back_btn = QPushButton("‹  Back")
        back_btn.setObjectName("GhostBtn")
        back_btn.setFixedWidth(120)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self.exit_add_mode)

        self.add_selection_label = QLabel("Nothing selected")
        self.add_selection_label.setObjectName("Muted")

        self.confirm_add_btn = QPushButton("Add monitor")
        self.confirm_add_btn.setObjectName("PrimaryBtn")
        self.confirm_add_btn.setFixedWidth(160)
        self.confirm_add_btn.setEnabled(False)
        self.confirm_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_add_btn.clicked.connect(self._confirm_add)

        row.addWidget(back_btn)
        row.addWidget(self.add_selection_label)
        row.addStretch()
        row.addWidget(self.confirm_add_btn)
        layout.addWidget(bar)
        return page

    def enter_add_mode(self):
        self.trigger_input.clear()
        self.ttl_selector.set_value(0)
        self.match_selector.set_value(True)
        self.picker.clear_selection()
        self.stack.setCurrentIndex(MODE_ADD)

    def exit_add_mode(self):
        self.stack.setCurrentIndex(MODE_LIST)

    def in_add_mode(self):
        return self.stack.currentIndex() == MODE_ADD

    def _on_picker_selection(self, count):
        self.confirm_add_btn.setEnabled(count > 0)
        if count:
            self.add_selection_label.setText(f"{count} channel{'s' if count != 1 else ''} selected")
        else:
            self.add_selection_label.setText("Nothing selected")

    def _confirm_add(self):
        channels = self.picker.get_selected()
        if not channels:
            return
        self.monitors_added.emit(channels, self.trigger_input.text().strip(),
                                 self.ttl_selector.value(), self.match_selector.value())
        self.exit_add_mode()

    def _remove_selected(self):
        rows = {idx.row() for idx in self.watchlist_table.selectedIndexes()}
        ids = [self.channel_ids[r] for r in rows if r < len(self.channel_ids)]
        if ids:
            self.monitors_removed.emit(ids)

    def set_data(self, dms, servers):
        self.picker.set_data(dms, servers)


    def refresh(self, watchlist):
        self.updating_table = True
        try:
            self.watchlist_table.setRowCount(0)
            self.channel_ids = []
            self.entries = {}
            for cid, data in watchlist.items():
                if not isinstance(data, dict):
                    continue
                row = self.watchlist_table.rowCount()
                self.watchlist_table.insertRow(row)
                self.channel_ids.append(cid)
                self.entries[cid] = dict(data)

                name_item = QTableWidgetItem(data.get('name', cid))
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.watchlist_table.setItem(row, 0, name_item)
                self.watchlist_table.setItem(row, 1, QTableWidgetItem(data.get('wipe_key') or ""))
                self.watchlist_table.setItem(row, 2,
                                             QTableWidgetItem(format_ttl(data.get('ttl', 0))))
                self.watchlist_table.setItem(row, 3,
                                             QTableWidgetItem(format_match(data.get('match_words', True))))
        finally:
            self.updating_table = False

        has_rows = bool(self.channel_ids)
        self.empty_label.setVisible(not has_rows)
        self.remove_btn.setEnabled(has_rows)

    def _on_item_changed(self, item):
        if self.updating_table:
            return
        row, col = item.row(), item.column()
        if row >= len(self.channel_ids):
            return
        channel_id = self.channel_ids[row]

        if col == 1:
            self.entries.setdefault(channel_id, {})["wipe_key"] = item.text()
            self.entry_edited.emit(channel_id, "wipe_key", item.text())
        elif col == 2:
            ttl = parse_ttl(item.text())
            if ttl is None:
                self.entry_edited.emit(channel_id, "ttl_invalid", item.text())
                return
            self.updating_table = True
            item.setText(format_ttl(ttl))
            self.updating_table = False
            self.entries.setdefault(channel_id, {})["ttl"] = ttl
            self.entry_edited.emit(channel_id, "ttl", ttl)
        elif col == 3:
            match = parse_match(item.text())
            if match is None:
                self.entry_edited.emit(channel_id, "match_invalid", item.text())
                return
            self.updating_table = True
            item.setText(format_match(match))
            self.updating_table = False
            self.entries.setdefault(channel_id, {})["match_words"] = match
            self.entry_edited.emit(channel_id, "match_words", match)

    def revert_cell(self, channel_id, field):
        if channel_id not in self.channel_ids:
            return
        row = self.channel_ids.index(channel_id)
        entry = self.entries.get(channel_id, {})
        if field == "ttl":
            column = 2
            text = format_ttl(entry.get("ttl", 0))
        elif field == "match_words":
            column = 3
            text = format_match(entry.get("match_words", True))
        else:
            column = 1
            text = entry.get("wipe_key") or ""
        item = self.watchlist_table.item(row, column)
        if item is None:
            return
        self.updating_table = True
        item.setText(text)
        self.updating_table = False

    def set_guard_state(self, active, has_monitors):
        self._guard_active = bool(active and has_monitors)
        self.guard_selector.set_value(self._guard_active)
        self.guard_selector.setEnabled(bool(has_monitors))
        if not has_monitors:
            text = "Add a monitor to arm the guard."
        elif self._guard_active:
            text = "Watching your channels. Turning it off keeps your monitors."
        else:
            text = ("Off: nothing is watched or deleted. Arming starts a fresh guard "
                    "window, so nothing you sent while it was off is affected.")
        self.guard_state_label.setText(text)

    def guard_active(self):
        return self._guard_active

    def _on_guard_selected(self, armed):
        # re-clicking the current state must not restart the guard window
        if bool(armed) == self._guard_active:
            return
        self.guard_toggled.emit(bool(armed))

    def set_status(self, text):
        self.status_label.setText(text or "")
        self.status_label.setVisible(bool(text))


def format_ttl(ttl):
    try:
        ttl = int(ttl)
    except (TypeError, ValueError):
        ttl = 0
    return "Instant" if ttl <= 0 else f"{ttl}s"


def parse_ttl(text):
    # takes '', 'Instant', '30' or '30s'. None if it's junk
    text = (text or "").strip().lower().rstrip("s")
    if not text or text == "instant":
        return 0
    try:
        return max(0, int(text))
    except ValueError:
        return None


def format_match(value):
    return "Whole word" if value else "Contains"


def parse_match(text):
    # takes 'Whole word' or 'Contains'. None if it's junk
    value = (text or "").strip().lower()
    if value in ("whole word", "whole", "word", "words"):
        return True
    if value in ("contains", "substring", "any"):
        return False
    return None
