import os
import sys
import traceback

from PyQt6.QtCore import QLockFile, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from lazerv2 import app_config
from lazerv2 import app_paths
from lazerv2.api import discord as api
from lazerv2.ui import threads
from lazerv2.ui.dashboard import MainWindow
from lazerv2.ui.login import LoginWindow
from lazerv2.ui.theme import theme
from lazerv2.workers.token_check import TokenCheck

# longest request timeout in the app (fetcher: 15s) plus a little slack
EXIT_THREAD_TIMEOUT_MS = 17000


class AppController:
    # swaps between login and dashboard, keeps the live window referenced

    def __init__(self, app):
        self.app = app
        self.login_win = None
        self.main_win = None
        self.switch_check = None    # TokenCheck for an account switch in flight
        self.show_login()

    def show_login(self):
        old_main = self.main_win
        self.main_win = None

        self.login_win = LoginWindow()
        self.login_win.login_success.connect(lambda: QTimer.singleShot(50, self.show_dashboard))
        self.login_win.show()

        # tear down after login is up, zero visible windows quits the app
        if old_main is not None:
            old_main.hide()
            old_main.cleanup()
            old_main.deleteLater()

    def show_dashboard(self):
        old_login = self.login_win
        self.login_win = None

        self.main_win = MainWindow()
        self.main_win.logout_requested.connect(lambda: QTimer.singleShot(50, self.show_login))
        self.main_win.account_switch_requested.connect(self.switch_account)
        self.main_win.show()

        if old_login is not None:
            # close(), not hide(): closeEvent parks its avatar downloads so
            # they are not destroyed mid-request along with the window
            old_login.close()
            old_login.deleteLater()

    def switch_account(self, token):
        # checked off the UI thread; the window stays responsive meanwhile
        if self.switch_check is not None and self.switch_check.isRunning():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.switch_check = TokenCheck(token)
        self.switch_check.done.connect(self._on_switch_checked)
        self.switch_check.start()

    def _on_switch_checked(self, user_data, error, token):
        QApplication.restoreOverrideCursor()
        if error or not user_data:
            QMessageBox.warning(self.main_win, "Could not switch account",
                                error or "Discord did not return the account.")
            return
        api.apply_login(token, user_data)

        old_main = self.main_win
        self.main_win = MainWindow()
        self.main_win.logout_requested.connect(lambda: QTimer.singleShot(50, self.show_login))
        self.main_win.account_switch_requested.connect(self.switch_account)
        self.main_win.show()

        # new window up before the old one goes, same as show_login
        if old_main is not None:
            old_main.hide()
            old_main.cleanup()
            old_main.deleteLater()


def _excepthook(exc_type, exc_value, exc_tb):
    # print crashes instead of letting Qt die silently
    traceback.print_exception(exc_type, exc_value, exc_tb)


def main():
    sys.excepthook = _excepthook
    app = QApplication(sys.argv)
    app.setApplicationName("Lazer V2")

    # two GUIs would mean two monitors and two purges in the same channels
    instance_lock = QLockFile(os.path.join(app_paths.data_dir(), "lazerv2.ui.lock"))
    if not instance_lock.tryLock(200):
        QMessageBox.warning(None, "Lazer is already running",
                            "Another copy of Lazer V2 is open on this account.")
        sys.exit(0)

    # paint before any window exists so nothing flashes unstyled
    theme.apply(app, app_config.get("theme"), persist=False)

    controller = AppController(app)   # keep the reference or it gets collected

    def shutdown():
        if controller.login_win is not None:
            controller.login_win.close()
        if controller.main_win is not None:
            controller.main_win.cleanup()

    app.aboutToQuit.connect(shutdown)
    exit_code = app.exec()
    if controller.main_win is not None:
        controller.main_win.cleanup()
    threads.park(controller.switch_check)

    # let parked workers finish their last request; each one is bounded by
    # a request timeout and listens to its stop event
    finished = threads.wait_all(EXIT_THREAD_TIMEOUT_MS)
    instance_lock.unlock()
    if not finished:
        # something is stuck past every timeout. Exiting normally would
        # destroy a running QThread and abort; all state is already written
        # atomically, so skip interpreter teardown instead.
        if sys.stdout is not None:   # None under pythonw
            sys.stdout.flush()
        os._exit(exit_code)
    sys.exit(exit_code)
