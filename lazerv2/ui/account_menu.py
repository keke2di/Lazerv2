# account switcher

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu

from lazerv2 import session_store
from lazerv2.ui import icons
from lazerv2.ui.theme import theme
from lazerv2.ui.widgets import Avatar

MENU_AVATAR = 32


class AccountMenu(QObject):

    account_selected = pyqtSignal(str)      # token
    add_account_requested = pyqtSignal()
    logout_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent_widget = parent

    def build(self, current_token=None):
        menu = QMenu(self._parent_widget)
        accounts = session_store.load_accounts()

        if accounts:
            header = menu.addAction("SWITCH ACCOUNT")
            header.setEnabled(False)

        for token, user in accounts.items():
            label = user.get("global_name") or user.get("username") or "Unknown"
            action = QAction(label, menu)
            action.setIcon(self._avatar_icon(user))

            if token == current_token:
                action.setText(f"{label}  ✓")
                action.setEnabled(False)
            else:
                action.triggered.connect(
                    lambda _checked=False, t=token: self.account_selected.emit(t))
            menu.addAction(action)

        if accounts:
            menu.addSeparator()

        add = QAction("Use another account", menu)
        add.setIcon(icons.icon("user_plus", theme.color("text_muted"), 18))
        add.triggered.connect(self.add_account_requested.emit)
        menu.addAction(add)

        logout = QAction("Log out", menu)
        logout.setIcon(icons.icon("logout", theme.color("red_text"), 18))
        logout.triggered.connect(self.logout_requested.emit)
        menu.addAction(logout)

        return menu

    def _avatar_icon(self, user):
        cached = session_store.get_cached_avatar(user.get("id"), user.get("avatar"))
        if cached:
            avatar = Avatar(MENU_AVATAR)
            if avatar.set_image(cached):
                return QIcon(avatar.pixmap())
        return icons.icon("message", theme.color("text_dim"), 18)
