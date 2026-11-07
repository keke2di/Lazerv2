# filter controls for the purge stepper

from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from lazerv2 import app_config
from lazerv2.globals import get_snowflake_for_days
from lazerv2.ui.widgets import ChipInput, Segmented

MODE_EXCLUDE = "Exclude these words"
MODE_INCLUDE = "Only include these words"
CONTENT_ALL = "Delete everything"
CONTENT_ATTACHMENTS = "Only delete attachments"

TIME_RANGES = [("All time", 0), ("24 hours", 1), ("7 days", 7), ("31 days", 31)]


class FilterPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.mode_selector = Segmented([("Exclude", MODE_EXCLUDE),
                                        ("Only include", MODE_INCLUDE)])
        self.kw_input = ChipInput("type a word, press Enter")
        # same choice the guard's trigger has, and the same default
        self.match_selector = Segmented([("Whole word", True), ("Contains", False)])
        self._add_section(layout, "KEYWORDS",
                          [self.mode_selector, self.kw_input, self.match_selector],
                          "Leave empty to match every message. Whole word matches "
                          "'cat' but not 'concatenate'.")

        self.time_selector = Segmented([(label, days) for label, days in TIME_RANGES])
        self._add_section(layout, "TIME RANGE", [self.time_selector])

        self.media_selector = Segmented([("Everything", CONTENT_ALL),
                                         ("Attachments only", CONTENT_ATTACHMENTS)])
        self._add_section(layout, "CONTENT TYPE", [self.media_selector])

        self.backup_selector = Segmented([("Save a copy first", True), ("No backup", False)])
        self.backup_selector.set_value(bool(app_config.get("backup_before_purge")))
        self.backup_selector.changed.connect(
            lambda value: app_config.set_value("backup_before_purge", bool(value)))
        self._add_section(layout, "BACKUP", [self.backup_selector],
                          "Writes everything about to be deleted to a JSON file and a "
                          "readable HTML page in Documents\\Lazer backups before the "
                          "first delete. If the backup can't be written, nothing is "
                          "deleted.")

        layout.addStretch()

    def _add_section(self, layout, title, widgets, hint=None):
        header = QLabel(title)
        header.setObjectName("SectionHeader")
        layout.addSpacing(10)
        layout.addWidget(header)

        card = QFrame()
        card.setObjectName("FilterCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(12)
        for widget in widgets:
            card_layout.addWidget(widget)
        if hint:
            note = QLabel(hint)
            note.setObjectName("Dim")
            note.setWordWrap(True)
            card_layout.addWidget(note)
        layout.addWidget(card)

    def get_data(self):
        return {
            "min_snowflake": get_snowflake_for_days(self.time_selector.value()),
            "keywords": self.kw_input.values(),
            "keyword_mode": self.mode_selector.value(),
            "keyword_whole_word": bool(self.match_selector.value()),
            "attachment_filter": self.media_selector.value(),
            "backup": bool(self.backup_selector.value()),
        }

    def reset(self):
        self.mode_selector.set_value(MODE_EXCLUDE)
        self.match_selector.set_value(True)
        self.media_selector.set_value(CONTENT_ALL)
        self.time_selector.set_value(0)
        self.kw_input.clear()

    def summary(self):
        parts = []
        keywords = self.kw_input.values()
        if keywords:
            verb = "excluding" if self.mode_selector.value() == MODE_EXCLUDE else "only"
            how = "" if self.match_selector.value() else " (contains)"
            parts.append(f"{verb} {', '.join(keywords)}{how}")
        if self.time_selector.value():
            parts.append(f"last {self.time_selector.label().lower()}")
        if self.media_selector.value() == CONTENT_ATTACHMENTS:
            parts.append("attachments only")
        text = " · ".join(parts) if parts else "everything"
        if self.backup_selector.value():
            text += " · backup first"
        return text
