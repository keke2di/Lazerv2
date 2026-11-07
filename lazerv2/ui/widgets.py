# shared widgets

import requests
from PyQt6.QtCore import QRect, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QSizePolicy, QVBoxLayout, QWidget)

AVATAR_TIMEOUT = 5

# one objectName per size so border-radius can live in the stylesheet
_AVATAR_OBJECTS = {32: "AvatarSm", 50: "Avatar", 72: "AvatarLg"}


# Discord's status colours, same in both themes on purpose
PRESENCE_COLORS = {
    "online": "#23A55A",
    "idle": "#F0B232",
    "dnd": "#F23F43",
    "invisible": "#80848E",
    "offline": "#80848E",
}


class Avatar(QLabel):
    # circular image with a presence badge, letter if there's none

    def __init__(self, size=50, letter=""):
        super().__init__()
        self.size_val = size
        self.setFixedSize(size, size)
        self.setObjectName(_AVATAR_OBJECTS.get(size, "Avatar"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._source = None
        self._presence = None
        self._dpr = 1.0
        self._letter = letter[:1].upper() if letter else ""
        if self._letter:
            self.setText(self._letter)

    def _ratio(self):
        # physical pixels per logical pixel for this screen
        try:
            return self.devicePixelRatioF() or 1.0
        except AttributeError:
            return 1.0

    def _canvas(self, ratio):
        physical = max(1, int(round(self.size_val * ratio)))
        canvas = QPixmap(physical, physical)
        canvas.fill(Qt.GlobalColor.transparent)
        canvas.setDevicePixelRatio(ratio)
        return canvas

    def set_image(self, data):
        # False if the data isn't an image
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return False
        self._source = pixmap
        self._render()
        return True

    def set_presence(self, status):
        # None hides the badge
        new = status if status in PRESENCE_COLORS else None
        if new == self._presence:
            return
        self._presence = new
        self._render()

    def _circular(self, pixmap):
        ratio = self._ratio()
        self._dpr = ratio
        rounded = self._canvas(ratio)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(ratio, ratio)
        path = QPainterPath()
        path.addEllipse(0, 0, self.size_val, self.size_val)
        painter.setClipPath(path)
        physical = max(1, int(round(self.size_val * ratio)))
        painter.drawPixmap(0, 0, self.size_val, self.size_val, pixmap.scaled(
            physical, physical,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation))
        painter.end()
        return rounded

    def _paint_badge(self, canvas):
        # transparent ring so the badge reads on any background
        if not self._presence:
            return
        diameter = max(10, self.size_val // 3)
        ring = max(2, diameter // 5)
        x = self.size_val - diameter
        y = self.size_val - diameter

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ratio = canvas.devicePixelRatio() or 1.0
        painter.scale(ratio, ratio)
        painter.setPen(Qt.PenStyle.NoPen)

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.setBrush(Qt.GlobalColor.transparent)
        painter.drawEllipse(x - ring, y - ring, diameter + ring * 2, diameter + ring * 2)

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setBrush(QColor(PRESENCE_COLORS[self._presence]))
        painter.drawEllipse(x, y, diameter, diameter)
        painter.end()

    def _render(self):
        # a QLabel shows text or a pixmap, never both
        if self._source is not None:
            canvas = self._circular(self._source)
        elif self._presence:
            canvas = self._letter_pixmap()
        else:
            # no image and no badge, just show the letter
            self.setText(self._letter)
            return

        self._paint_badge(canvas)
        self.setText("")
        self.setPixmap(canvas)

    def _letter_pixmap(self):
        from lazerv2.ui.theme import theme
        ratio = self._ratio()
        self._dpr = ratio
        canvas = self._canvas(ratio)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(ratio, ratio)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.color("surface_alt")))
        painter.drawEllipse(0, 0, self.size_val, self.size_val)

        if self._letter:
            painter.setPen(QColor(theme.color("text_muted")))
            font = painter.font()
            font.setPixelSize(max(10, self.size_val // 2))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRect(0, 0, self.size_val, self.size_val),
                             Qt.AlignmentFlag.AlignCenter, self._letter)
        painter.end()
        return canvas

    def showEvent(self, event):
        super().showEvent(event)
        # a window dragged to a different-scale monitor needs a fresh pixmap
        if abs(self._ratio() - self._dpr) > 0.01:
            self._render()


class ImageFetcher(QThread):

    loaded = pyqtSignal(bytes)

    def __init__(self, url, timeout=AVATAR_TIMEOUT):
        super().__init__()
        self.url = url
        self.timeout = timeout

    def run(self):
        try:
            res = requests.get(self.url, timeout=self.timeout)
            if res.status_code == 200:
                self.loaded.emit(res.content)
        except requests.RequestException:
            pass


class StatCard(QFrame):
    # big number over a caption

    def __init__(self, label, value="--"):
        super().__init__()
        self.setObjectName("StatCard")
        self.setMinimumHeight(92)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(2)

        self.value_label = QLabel(str(value))
        self.value_label.setObjectName("CardValue")
        self.caption_label = QLabel(label)
        self.caption_label.setObjectName("CardLabel")

        layout.addWidget(self.value_label)
        layout.addWidget(self.caption_label)
        layout.addStretch()

    def set_value(self, value):
        self.value_label.setText(str(value))


class StatusRow(QWidget):
    # coloured dot, label, detail text

    STATES = {
        "on": "DotOn",
        "off": "DotOff",
        "warn": "DotWarn",
        "danger": "DotDanger",
    }

    def __init__(self, title, detail="", state="off"):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(10)

        self.dot = QLabel("●")
        self.dot.setFixedWidth(14)
        self.title_label = QLabel(title)
        self.title_label.setMinimumWidth(130)
        self.detail_label = QLabel(detail)
        self.detail_label.setObjectName("Muted")

        layout.addWidget(self.dot)
        layout.addWidget(self.title_label)
        layout.addWidget(self.detail_label)
        layout.addStretch()

        self.set_state(state)

    def set_state(self, state):
        from lazerv2.ui.theme import restyle
        self.dot.setObjectName(self.STATES.get(state, "DotOff"))
        restyle(self.dot)

    def set_detail(self, text):
        self.detail_label.setText(text)

    def update_row(self, state, detail):
        self.set_state(state)
        self.set_detail(detail)


class Segmented(QWidget):
    # pill group that replaces a dropdown

    changed = pyqtSignal(object)

    def __init__(self, options=None):
        super().__init__()
        self._values = []
        self._buttons = []

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self._on_clicked)

        for label, value in (options or []):
            self.add_option(label, value)

    def add_option(self, label, value):
        index = len(self._buttons)
        btn = QPushButton(label)
        btn.setObjectName("Segment")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setMinimumHeight(38)
        # position drives the corner radius so it reads as one control
        btn.setProperty("segment", "only" if index == 0 else "last")
        if index == 0:
            btn.setChecked(True)

        self._buttons.append(btn)
        self._values.append(value)
        self._group.addButton(btn, index)
        self._layout.addWidget(btn)
        self._refresh_positions()
        return btn

    def _refresh_positions(self):
        from lazerv2.ui.theme import restyle
        count = len(self._buttons)
        for i, btn in enumerate(self._buttons):
            if count == 1:
                position = "only"
            elif i == 0:
                position = "first"
            elif i == count - 1:
                position = "last"
            else:
                position = "middle"
            btn.setProperty("segment", position)
            restyle(btn)

    def _on_clicked(self, index):
        self.changed.emit(self._values[index])

    def value(self):
        checked = self._group.checkedId()
        return self._values[checked] if 0 <= checked < len(self._values) else None

    def label(self):
        checked = self._group.checkedId()
        return self._buttons[checked].text() if 0 <= checked < len(self._buttons) else None

    def set_value(self, value):
        if value not in self._values:
            return False
        self._buttons[self._values.index(value)].setChecked(True)
        return True

    def values(self):
        return list(self._values)


class Chip(QFrame):

    removed = pyqtSignal(str)

    def __init__(self, text):
        super().__init__()
        self.text_value = text
        self.setObjectName("Chip")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 5, 3)
        layout.setSpacing(6)

        label = QLabel(text)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        close = QPushButton("✕")
        close.setObjectName("ChipClose")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFixedSize(16, 16)
        close.clicked.connect(lambda: self.removed.emit(self.text_value))

        layout.addWidget(label)
        layout.addWidget(close)


class ChipInput(QWidget):
    # type a word, press Enter, get a removable chip

    changed = pyqtSignal()

    def __init__(self, placeholder="add a word..."):
        super().__init__()
        self._values = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        self.chip_row = QWidget()
        self.chip_layout = QHBoxLayout(self.chip_row)
        self.chip_layout.setContentsMargins(0, 0, 0, 0)
        self.chip_layout.setSpacing(6)
        self.chip_layout.addStretch()
        self.chip_row.setVisible(False)

        self.entry = QLineEdit()
        self.entry.setPlaceholderText(placeholder)
        self.entry.returnPressed.connect(self._commit)
        self.entry.installEventFilter(self)

        outer.addWidget(self.chip_row)
        outer.addWidget(self.entry)

    def eventFilter(self, obj, event):
        # backspace on an empty field removes the last chip
        if obj is self.entry and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Backspace and not self.entry.text() and self._values:
                self.remove(self._values[-1])
                return True
        return super().eventFilter(obj, event)

    def _commit(self):
        text = self.entry.text().strip()
        if text:
            self.add(text)
        self.entry.clear()

    def add(self, text):
        # False if blank or already there
        text = (text or "").strip()
        if not text or text.lower() in [v.lower() for v in self._values]:
            return False

        self._values.append(text)
        chip = Chip(text)
        chip.removed.connect(self.remove)
        self.chip_layout.insertWidget(self.chip_layout.count() - 1, chip)
        self.chip_row.setVisible(True)
        self.changed.emit()
        return True

    def remove(self, text):
        if text not in self._values:
            return
        self._values.remove(text)
        for i in range(self.chip_layout.count()):
            item = self.chip_layout.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, Chip) and widget.text_value == text:
                self.chip_layout.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
                break
        self.chip_row.setVisible(bool(self._values))
        self.changed.emit()

    def values(self):
        return list(self._values)

    def set_values(self, values):
        for text in list(self._values):
            self.remove(text)
        for text in values or []:
            self.add(text)

    def clear(self):
        self.set_values([])


class StepIndicator(QWidget):
    # (1) Targets - (2) Filters - (3) Run

    def __init__(self, labels):
        super().__init__()
        self._dots = []
        self._labels = []
        self._lines = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        for index, text in enumerate(labels):
            if index:
                line = QFrame()
                line.setObjectName("StepLine")
                line.setFixedHeight(2)
                line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                self._lines.append(line)
                layout.addWidget(line, 1)

            dot = QLabel(str(index + 1))
            dot.setObjectName("StepDot")
            dot.setFixedSize(24, 24)
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)

            label = QLabel(text)
            label.setObjectName("StepLabel")

            self._dots.append(dot)
            self._labels.append(label)
            layout.addWidget(dot)
            layout.addWidget(label)

        layout.addStretch(2)
        self.set_step(0)

    def set_step(self, index):
        from lazerv2.ui.theme import restyle
        for i, (dot, label) in enumerate(zip(self._dots, self._labels)):
            state = "done" if i < index else ("current" if i == index else "todo")
            for widget in (dot, label):
                widget.setProperty("step", state)
                restyle(widget)
        for i, line in enumerate(self._lines):
            line.setProperty("step", "done" if i < index else "todo")
            restyle(line)


class Card(QFrame):
    # panel with an optional title

    def __init__(self, title=None):
        super().__init__()
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 16, 20, 16)
        self.body.setSpacing(6)

        if title:
            header = QLabel(title)
            header.setObjectName("SectionHeader")
            self.body.addWidget(header)
            self.body.addSpacing(6)

    def add(self, widget):
        self.body.addWidget(widget)
        return widget
