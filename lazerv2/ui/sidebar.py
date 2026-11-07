# the collapsible nav rail

from PyQt6.QtCore import (QEasingCurve, QParallelAnimationGroup, QPropertyAnimation,
                          Qt, pyqtSignal)
from PyQt6.QtWidgets import (QButtonGroup, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                             QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from lazerv2.ui import icons
from lazerv2.ui.theme import RAIL_COLLAPSED, RAIL_EXPANDED, restyle, theme

ANIM_MS = 170
ICON_SIZE = 20


class NavButton(QPushButton):

    def __init__(self, icon_name, text, accent):
        super().__init__()
        self.icon_name = icon_name
        self.accent = accent
        self.setObjectName("NavBtn")
        self.setProperty("accent", accent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(46)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        # lines the glyph up with the middle of the rail
        layout.setContentsMargins(18, 0, 10, 0)
        layout.setSpacing(14)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.text_label = QLabel(text)
        self.text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        layout.addStretch()

        self.retheme()

    def retheme(self):
        # icons bake in their colour so the glyph needs redrawing
        color = theme.color(self.accent) if self.isChecked() else theme.color("text_muted")
        self.icon_label.setPixmap(icons.pixmap(self.icon_name, color, ICON_SIZE))
        restyle(self)

    def setChecked(self, checked):
        super().setChecked(checked)
        self.retheme()


class RailButton(QPushButton):

    def __init__(self, icon_name, tooltip=""):
        super().__init__()
        self.icon_name = icon_name
        self.setObjectName("RailBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36, 36)
        self.setToolTip(tooltip)
        self.retheme()

    def set_icon_name(self, name):
        self.icon_name = name
        self.retheme()

    def retheme(self):
        self.setIcon(icons.icon(self.icon_name, theme.color("text_muted"), ICON_SIZE))


class Sidebar(QFrame):

    page_changed = pyqtSignal(int)
    theme_toggle_requested = pyqtSignal()
    account_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(RAIL_COLLAPSED)
        self.setMouseTracking(True)
        self._expanded = False
        self._nav_buttons = []

        self._build()
        self._build_animation()

        # rail starts collapsed, so the labels start hidden
        for effect in self._label_effects:
            effect.setOpacity(0.0)

        theme.changed.connect(self._on_theme_changed)


    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand = QWidget()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(18, 22, 10, 22)
        brand_layout.setSpacing(14)
        self.brand_mark = QLabel("◆")
        self.brand_mark.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_text = QLabel("LAZER")
        self.brand_text.setObjectName("Logo")
        brand_layout.addWidget(self.brand_mark)
        brand_layout.addWidget(self.brand_text)
        brand_layout.addStretch()
        layout.addWidget(brand)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for index, (icon_name, text, accent) in enumerate([
            ("overview", "Overview", "neutral"),
            ("purge", "Manual Purge", "red"),
            ("guard", "Conversation Guard", "blue"),
        ]):
            btn = NavButton(icon_name, text, accent)
            btn.clicked.connect(lambda _checked, i=index: self._select(i))
            self.group.addButton(btn, index)
            self._nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(14, 10, 10, 14)
        footer_layout.setSpacing(8)

        self.theme_btn = RailButton("sun", "Switch to light theme")
        self.theme_btn.clicked.connect(self.theme_toggle_requested.emit)
        footer_layout.addWidget(self.theme_btn)

        self.account_btn = QPushButton()
        self.account_btn.setObjectName("AccountBtn")
        self.account_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.account_btn.setFixedHeight(48)
        self.account_btn.clicked.connect(self.account_clicked.emit)

        account_layout = QHBoxLayout(self.account_btn)
        account_layout.setContentsMargins(4, 4, 8, 4)
        account_layout.setSpacing(12)

        from lazerv2.ui.widgets import Avatar
        self.account_avatar = Avatar(32)
        self.account_avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.account_name = QLabel("Account")
        self.account_name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.account_chevron = QLabel()
        self.account_chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        account_layout.addWidget(self.account_avatar)
        account_layout.addWidget(self.account_name)
        account_layout.addStretch()
        account_layout.addWidget(self.account_chevron)
        footer_layout.addWidget(self.account_btn)

        layout.addWidget(footer)

        # no effect on the rail itself, nested ones render badly in Qt
        self._label_effects = []
        labels = [self.brand_text, self.account_name, self.account_chevron]
        labels += [btn.text_label for btn in self._nav_buttons]
        for widget in labels:
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
            self._label_effects.append(effect)

        self._select(0)
        self._retheme_icons()

    def _build_animation(self):
        self._anim = QParallelAnimationGroup(self)

        for prop in (b"minimumWidth", b"maximumWidth"):
            a = QPropertyAnimation(self, prop, self)
            a.setDuration(ANIM_MS)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.addAnimation(a)

        for effect in self._label_effects:
            a = QPropertyAnimation(effect, b"opacity", self)
            a.setDuration(ANIM_MS)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.addAnimation(a)


    def _select(self, index):
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)
        self.page_changed.emit(index)

    def set_page(self, index):
        # select without emitting page_changed
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._animate(expand=True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._animate(expand=False)

    def _animate(self, expand):
        if expand == self._expanded:
            return
        self._expanded = expand

        target_width = RAIL_EXPANDED if expand else RAIL_COLLAPSED
        target_opacity = 1.0 if expand else 0.0

        # stop first or a fast hover leaves the rail stuck halfway
        self._anim.stop()

        for i in range(self._anim.animationCount()):
            a = self._anim.animationAt(i)
            if a.propertyName() in (b"minimumWidth", b"maximumWidth"):
                a.setStartValue(self.width())
                a.setEndValue(target_width)
            else:
                a.setStartValue(a.targetObject().opacity())
                a.setEndValue(target_opacity)

        self._anim.start()
        self.raise_()


    def _on_theme_changed(self, _name):
        self._retheme_icons()

    def _retheme_icons(self):
        for btn in self._nav_buttons:
            btn.retheme()
        self.theme_btn.set_icon_name("moon" if theme.is_dark() else "sun")
        self.theme_btn.setToolTip("Switch to light theme" if theme.is_dark()
                                  else "Switch to dark theme")
        self.account_chevron.setPixmap(
            icons.pixmap("chevron_down", theme.color("text_dim"), 14))
        self.brand_mark.setPixmap(icons.pixmap("bolt", theme.color("red_text"), ICON_SIZE))


    def set_account(self, name, avatar_bytes=None):
        self.account_name.setText(name or "Account")
        self.account_btn.setToolTip(f"Account: {name or 'Account'} - click to switch")
        if avatar_bytes:
            self.account_avatar.set_image(avatar_bytes)
        elif name:
            self.account_avatar._letter = name[:1].upper()
            self.account_avatar.setText(name[:1].upper())

    def set_presence(self, status):
        self.account_avatar.set_presence(status)
