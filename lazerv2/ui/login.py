from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QScrollArea, QStackedWidget,
                             QVBoxLayout, QWidget)

from lazerv2.api import discord as api
from lazerv2 import session_store
from lazerv2.ui import threads
from lazerv2.ui.widgets import Avatar, ImageFetcher
from lazerv2.workers.token_check import TokenCheck


class LoginWindow(QDialog):
    login_success = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lazer Login")
        self.setFixedSize(620, 470)
        # blue, logging in is the safe path
        self.setProperty("accent", "blue")
        self.accounts = session_store.load_accounts()
        self._avatar_fetchers = []
        self._check = None            # the TokenCheck in flight, if any
        self._check_saved = False     # was it a saved account card?
        self.init_ui()

    def init_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(30, 30, 30, 30)

        self.title = QLabel("SELECT ACCOUNT")
        self.title.setObjectName("SectionHeader")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_layout.addWidget(self.title)

        self.stack = QStackedWidget()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.profile_page = QWidget()
        self.prof_container = QHBoxLayout(self.profile_page)
        self.prof_container.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.profile_page)

        self.input_page = QWidget()
        input_layout = QVBoxLayout(self.input_page)
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Paste token here...")
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setFixedHeight(45)
        self.token_input.returnPressed.connect(lambda: self.attempt_login(self.token_input.text()))

        manual_btn = QPushButton("Log In")
        manual_btn.setObjectName("PrimaryBtn")
        manual_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        manual_btn.clicked.connect(lambda: self.attempt_login(self.token_input.text()))

        back_btn = QPushButton("Back")
        back_btn.setObjectName("LinkBtn")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))

        input_layout.addStretch()
        input_layout.addWidget(self.token_input)
        input_layout.addWidget(manual_btn)
        input_layout.addWidget(back_btn)
        input_layout.addStretch()

        self.stack.addWidget(self.scroll)
        self.stack.addWidget(self.input_page)
        self.main_layout.addWidget(self.stack)

        self.busy_label = QLabel("")
        self.busy_label.setObjectName("Muted")
        self.busy_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.busy_label.setVisible(False)
        self.main_layout.addWidget(self.busy_label)

        self.add_acc_btn = QPushButton("Use Different Account")
        self.add_acc_btn.setObjectName("PrimaryBtn")
        self.add_acc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_acc_btn.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        self.main_layout.addWidget(self.add_acc_btn)

        self.refresh_profiles()

    def refresh_profiles(self):
        while self.prof_container.count():
            item = self.prof_container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if not self.accounts:
            self.stack.setCurrentIndex(1)
            return

        self.stack.setCurrentIndex(0)
        for token, user_data in self.accounts.items():
            self.prof_container.addWidget(self.create_card(token, user_data))

    def create_card(self, token, user):
        card = QFrame()
        card.setFixedSize(168, 232)
        card.setObjectName("ProfileCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 18, 16, 16)

        display = user.get('global_name') or user.get('username') or "Unknown"
        pfp = Avatar(72, letter=display)
        self.load_card_avatar(pfp, user)

        name = QLabel(display)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setWordWrap(True)

        btn = QPushButton("Log in")
        btn.setObjectName("PrimaryBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: self.attempt_login(token, saved=True))

        remove_btn = QPushButton("Remove")
        remove_btn.setObjectName("LinkBtn")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(lambda: self.forget_account(token))

        layout.addWidget(pfp, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(12)
        layout.addWidget(name)
        layout.addStretch()
        layout.addWidget(btn)
        layout.addWidget(remove_btn)
        return card

    def load_card_avatar(self, widget, user):
        user_id = user.get('id')
        avatar_hash = user.get('avatar')
        cached = session_store.get_cached_avatar(user_id, avatar_hash)
        if cached and widget.set_image(cached):
            return
        if not (avatar_hash and user_id):
            return

        url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png?size=128"
        # off the UI thread: an offline account used to freeze this window
        fetcher = ImageFetcher(url)
        fetcher.loaded.connect(
            lambda data, target=widget, account=user_id, seed=avatar_hash:
                self._on_card_avatar(target, account, seed, data))
        self._avatar_fetchers.append(fetcher)
        fetcher.start()

    def _on_card_avatar(self, widget, user_id, avatar_hash, data):
        try:
            if widget.set_image(data):
                session_store.cache_avatar(user_id, data, avatar_hash)
        except RuntimeError:
            pass

    def attempt_login(self, token, saved=False):
        # the saved copy must be the cleaned token: the daemon sends it as a
        # header, and leading whitespace or quotes make it invalid
        token = api.normalize_token(token)
        if self.is_checking():
            return
        if not token:
            QMessageBox.warning(self, "Login Failed", "No token provided.")
            return
        # off the UI thread: a dead connection used to freeze this window
        self._set_busy(True)
        self._check_saved = saved
        self._check = TokenCheck(token)
        self._check.done.connect(self._on_checked)
        self._check.start()

    def is_checking(self):
        return self._check is not None and self._check.isRunning()

    def _set_busy(self, busy):
        self.stack.setEnabled(not busy)
        self.add_acc_btn.setEnabled(not busy)
        self.busy_label.setText("Checking the token with Discord..." if busy else "")
        self.busy_label.setVisible(busy)

    def _on_checked(self, user_data, error, token):
        saved = self._check_saved
        self._set_busy(False)
        if user_data:
            api.apply_login(token, user_data)
            if not self.save_account(token, user_data):
                QMessageBox.warning(
                    self, "Session",
                    "Logged in, but the account could not be saved to disk.")
            self.login_success.emit()
            return

        # drop a saved token Discord no longer accepts
        if saved and error == "Invalid or expired token.":
            self.forget_account(token, silent=True)
            QMessageBox.warning(self, "Session Expired",
                                "That saved token is no longer valid and has been removed.")
            return

        QMessageBox.warning(self, "Login Failed", error or "Invalid token.")

    def forget_account(self, token, silent=False):
        if not silent:
            confirm = QMessageBox.question(self, "Remove Account",
                                           "Remove this saved account from this device?")
            if confirm != QMessageBox.StandardButton.Yes:
                return
        self.accounts.pop(token, None)
        if not session_store.save_accounts(self.accounts):
            QMessageBox.warning(self, "Session", "Could not save the account list to disk.")
        self.refresh_profiles()

    def save_account(self, token, user_data):
        # drop older tokens for the same account so cards don't stack up
        user_id = user_data.get("id")
        if user_id:
            self.accounts = {
                t: u for t, u in self.accounts.items()
                if t == token or u.get("id") != user_id
            }
        self.accounts[token] = user_data
        return session_store.save_accounts(self.accounts)

    def closeEvent(self, event):
        threads.park(self._check)
        # avatar downloads time out on their own (ImageFetcher); don't block
        # the window on them or terminate() them, just keep them referenced
        # until they finish
        for fetcher in self._avatar_fetchers:
            threads.park(fetcher)
        self._avatar_fetchers = []
        super().closeEvent(event)
