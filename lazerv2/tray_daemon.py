# conversation guard without the gui

import argparse
import os
import sys
import threading
import time
from dataclasses import dataclass

import requests

try:
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
    from PyQt6.QtGui import QIcon, QColor, QPixmap, QPainter
    from PyQt6.QtCore import QTimer, pyqtSignal, QObject, QLockFile, Qt
except ImportError as error:
    print(f"tray_daemon: PyQt6 is required but could not be imported: {error}", file=sys.stderr)
    sys.exit(1)

from lazerv2 import app_config
from lazerv2 import app_paths
from lazerv2 import guard_core
from lazerv2 import guard_utils
from lazerv2 import http_client
from lazerv2 import logging_setup
from lazerv2 import session_store
from lazerv2 import watchlist_store
BASE_URL = "https://discord.com/api/v9"
APP_DIR = app_paths.project_root()
ERROR_LOG = app_paths.path("daemon_error.log")

TIMEOUT = 10
POLL_INTERVAL = 3
AUTOSTART_VALUE = "LAZER_ConversationGuard"
THREAD_JOIN_TIMEOUT = 10


@dataclass(frozen=True)
class _Identity:
    # one thread, one account. Never read self.* mid-run.
    token: str
    user_id: str
    account_id: str
    generation: int = 0     # which start_monitoring() this thread belongs to


class MonitorSignals(QObject):
    status_changed = pyqtSignal(bool)
    notification_requested = pyqtSignal(str, str)
    # the generation of the thread that saw the 401; -1 means "current"
    auth_failed = pyqtSignal(int)


class TrayDaemon:
    def __init__(self, token=None, headless=False):
        self.app = QApplication.instance()
        if not self.app:
            self.app = QApplication(sys.argv)
        # tray only, don't quit when a dialog closes
        self.app.setQuitOnLastWindowClosed(False)

        self.logger = logging_setup.configure("lazer.daemon", "daemon.log")
        self.headless = headless
        self.explicit_token = bool(token or os.getenv("DISCORD_TOKEN"))
        self.token = token or os.getenv("DISCORD_TOKEN")
        self.user_id = None
        self.account_id = None
        self.monitoring_active = False
        self.watchlist_active = False
        self.watchlist = {}
        self.monitor_thread = None
        self.retired_threads = []
        self.stop_event = None
        self._unfollowable_account = None
        self.signals = MonitorSignals()
        # bumped by every start_monitoring(); a 401 from an older thread
        # (a previous account, or a token since replaced) is stale
        self._generation = 0
        self.signals.status_changed.connect(self.on_status_changed)
        self.signals.notification_requested.connect(self.show_tray_notification)
        self.signals.auth_failed.connect(self.on_auth_failed)

        self.tray_icon = None
        self.menu = None
        self.action_status = None
        self.action_toggle = None

        if not self.headless:
            self.setup_tray()

        if self.token:
            self.resolve_identity()
            self.account_id = str(self.user_id) if self.user_id else None
        else:
            self.load_session()

        if not self.account_id:
            if self.explicit_token:
                self.log("Could not resolve the user for the supplied token.")
            # otherwise load_session already explained what is missing
        elif self.explicit_token:
            # an explicit --token wins; point the watchlist at that account
            watchlist_store.set_active_account(self.account_id)
            self.reload_watchlist()
        else:
            target = watchlist_store.active_account_id()
            if target and target != self.account_id:
                self.follow_account(target, initial=True)
            else:
                watchlist_store.set_active_account(self.account_id)
            self.reload_watchlist()

        self.update_tray_icon(False)
        self._update_toggle()
        self._announce_alive()

        if self.token and self.watchlist and self.watchlist_active:
            self.start_monitoring()

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_watchlist)
        self.timer.start(2000)

    # ------------------------------------------------------------------ misc

    def log(self, message):
        # file logging works under pythonw, where stdout is None
        self.logger.info(message)

    def _announce_alive(self):
        # Only claim the GUI's monitor slot once an account is resolved: a
        # daemon with no usable session cannot monitor, so it must not suppress
        # the in-window guard while it idles waiting for a login.
        if self.account_id:
            app_config.write_heartbeat()

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.log("No system tray available on this desktop session.")
            return False

        self.tray_icon = QSystemTrayIcon(self.app)
        self.tray_icon.setToolTip("LAZER - Conversation Guard")
        self.tray_icon.show()

        self.menu = QMenu()

        self.action_open_lazer = self.menu.addAction("Open LAZER")
        self.action_open_lazer.triggered.connect(self.open_lazer)

        self.action_status = self.menu.addAction("Status: Inactive")
        self.action_status.setEnabled(False)

        self.menu.addSeparator()

        self.action_toggle = self.menu.addAction("Start Monitoring")
        self.action_toggle.triggered.connect(self.toggle_monitoring)
        self.action_toggle.setEnabled(False)

        self.action_exit = self.menu.addAction("Exit")
        self.action_exit.triggered.connect(self.exit_daemon)

        self.tray_icon.setContextMenu(self.menu)
        return True

    def update_tray_icon(self, monitoring=False):
        if self.tray_icon is None:
            return

        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.fillRect(0, 0, 32, 32, QColor(0, 200, 0) if monitoring else QColor(100, 100, 100))
        painter.end()

        self.tray_icon.setIcon(QIcon(pixmap))

    def _update_toggle(self):
        if self.action_toggle is None:
            return
        self.action_toggle.setEnabled(bool(self.watchlist) and bool(self.token))

    def on_status_changed(self, monitoring):
        if self.action_status is None:
            return
        if monitoring:
            self.action_status.setText("Status: Monitoring Active")
            self.action_toggle.setText("Stop Monitoring")
            self.action_toggle.setEnabled(True)
        else:
            self.action_status.setText("Status: Inactive")
            self.action_toggle.setText("Start Monitoring")
            self.action_toggle.setEnabled(bool(self.watchlist) and bool(self.token))
        self.update_tray_icon(monitoring)

    def show_tray_notification(self, title, message):
        if self.tray_icon is not None:
            self.tray_icon.showMessage(
                title, message, QSystemTrayIcon.MessageIcon.Information, 5000)

    # --------------------------------------------------------------- session

    def resolve_identity(self):
        if not self.token:
            return None
        try:
            with requests.Session() as session:
                res = http_client.request(
                    session, "GET", f"{BASE_URL}/users/@me",
                    headers={"Authorization": self.token}, timeout=5, attempts=1)
        except http_client.TransportError as error:
            self.log(f"Failed to fetch user identity: {error}")
            return None
        if res.status_code != 200:
            self.log(f"Could not resolve user identity (HTTP {res.status_code})")
            return None
        try:
            self.user_id = res.json().get("id")
        except (ValueError, AttributeError):
            return None
        self.log(f"Resolved user identity: {self.user_id}")
        return self.user_id

    def load_session(self, preferred_account=None):
        accounts = session_store.load_accounts()
        if not accounts:
            self.log("No saved session found; log in through the GUI first.")
            return False

        wanted = preferred_account or app_config.get("last_account")
        token = None
        if wanted:
            token = next(
                (candidate for candidate, data in accounts.items()
                 if str(data.get("id")) == str(wanted)), None)
        if token is None:
            token = next(iter(accounts))

        self.token = token
        self.user_id = accounts[token].get("id")
        if not self.user_id:
            self.resolve_identity()
        self.account_id = str(self.user_id) if self.user_id else None
        self.log(f"Loaded saved session for account {self.account_id}")
        return bool(self.token and self.user_id)

    def reload_session_token(self):
        # returns True only if a different, saved token is available
        accounts = session_store.load_accounts()
        wanted = self.account_id or app_config.get("last_account")
        for token, data in accounts.items():
            if wanted and str(data.get("id")) == str(wanted):
                if token != self.token:
                    self.token = token
                    return True
                return False
        return False

    # ------------------------------------------------------------- watchlist

    def reload_watchlist(self):
        # False means "nothing changed or nothing could be read"
        account, active, entries, error = watchlist_store.snapshot(self.account_id)
        if error:
            self.log(f"Watchlist read failed: {error}")
            return False
        if account and account != self.account_id:
            self.follow_account(account)
            return True

        changed = entries != self.watchlist or active != self.watchlist_active
        self.watchlist = entries
        self.watchlist_active = active
        return changed

    def follow_account(self, target, initial=False):
        target = str(target)
        if target == self.account_id:
            return

        token = None
        accounts = session_store.load_accounts()
        for candidate, data in accounts.items():
            if str(data.get("id")) == target:
                token = candidate
                break
        if token is None:
            # refresh_watchlist asks again every 2s; only say so once per target
            if target != self._unfollowable_account:
                self._unfollowable_account = target
                self.log(f"No saved token for account {target}; keeping the current session.")
            if initial:
                self.account_id = self.account_id or target
            return

        self._unfollowable_account = None
        self.log(f"Following account {target}")
        self.stop_monitoring(mark_inactive=False)
        self.token = token
        self.user_id = target
        self.account_id = target
        self.watchlist = {}
        self.watchlist_active = False
        self.reload_watchlist()
        self._update_toggle()
        # we are the monitor now, so take the slot from the GUI immediately
        self._announce_alive()
        if self.watchlist and self.watchlist_active:
            self.start_monitoring()

    def refresh_watchlist(self):
        # tell the GUI we're alive on the existing poll, once there is an
        # account to watch
        self._announce_alive()
        self._prune_retired_threads()

        account = watchlist_store.active_account_id()
        if account and account != self.account_id:
            self.follow_account(account)
            return

        if not self.reload_watchlist():
            return

        self.log(f"Watchlist updated: {len(self.watchlist)} channel(s), "
                 f"active={self.watchlist_active}")
        self._update_toggle()

        if self.watchlist and self.watchlist_active and not self.monitoring_active and self.token:
            self.start_monitoring()
        elif self.monitoring_active and not self.watchlist_active:
            self.stop_monitoring()

    # ------------------------------------------------------------ monitoring

    def start_monitoring(self):
        if self.monitoring_active:
            return
        if not self.token or not self.user_id or not self.account_id:
            self.log("Refusing to monitor without a resolved account.")
            return

        self.monitoring_active = True
        self.stop_event = threading.Event()
        self._generation += 1
        identity = _Identity(self.token, str(self.user_id), str(self.account_id),
                             self._generation)
        thread = threading.Thread(
            target=self.monitor_loop, args=(identity, self.stop_event),
            name="lazer-guard", daemon=True)
        self.monitor_thread = thread
        thread.start()
        self.signals.status_changed.emit(True)

    def stop_monitoring(self, mark_inactive=True, notify=False):
        self.monitoring_active = False
        if self.stop_event is not None:
            self.stop_event.set()
            self.stop_event = None

        thread = self.monitor_thread
        self.monitor_thread = None
        if thread is not None and thread.is_alive():
            self.retired_threads.append(thread)

        if mark_inactive and self.account_id:
            watchlist_store.set_active(self.account_id, False)
            self.watchlist_active = False
        self.signals.status_changed.emit(False)
        if notify:
            self.signals.notification_requested.emit(
                "Monitoring Suspended", "Background monitoring has been disabled")

    def _prune_retired_threads(self):
        alive = []
        for thread in self.retired_threads:
            if thread.is_alive():
                alive.append(thread)
            else:
                thread.join(timeout=0)
        self.retired_threads = alive

    def toggle_monitoring(self):
        if self.monitoring_active:
            self.stop_monitoring(notify=True)
            return

        self.reload_watchlist()
        if not self.watchlist:
            self.log("Cannot start monitoring: no watchlist configured.")
            self.signals.notification_requested.emit(
                "Monitoring Failed", "No channel watchlist configured.")
            return

        if not self.account_id:
            self.log("Cannot start monitoring: no account resolved.")
            return

        # same rule as the GUI switch: arming starts a fresh guard window, so
        # nothing sent while monitoring was off is wiped or aged out
        armed_at = guard_utils.current_time_ms()

        def restamp(entries):
            for entry in entries.values():
                if isinstance(entry, dict):
                    entry["armed_at"] = armed_at

        ok, error, _ = watchlist_store.mutate_entries(self.account_id, restamp, active=True)
        if not ok:
            self.log(f"Cannot start monitoring: {error}")
            return
        self.reload_watchlist()
        self.watchlist_active = True
        self.start_monitoring()

    def on_auth_failed(self, generation=-1):
        if generation not in (-1, self._generation) or not self.monitoring_active:
            # a retired thread's 401 must not stop the account running now
            self.log("Ignoring an auth failure from a stopped monitor thread.")
            return
        # 401 from a worker: try the token saved after a rotation, once
        self.log("Discord rejected the token while monitoring.")
        if self.reload_session_token():
            self.log("Picked up a newer saved token; restarting monitoring.")
            self.stop_monitoring(mark_inactive=False)
            self.start_monitoring()
        else:
            self.stop_monitoring(mark_inactive=False)
            self.signals.notification_requested.emit(
                "Monitoring Stopped", "Discord rejected the saved token. Log in again.")

    def monitor_loop(self, identity, stop_event):
        monitor = guard_core.GuardMonitor(
            identity.token, identity.user_id,
            account_id=identity.account_id,
            stop_event=stop_event,
            on_trigger=self._notify_trigger,
            on_auth_failed=lambda: self.signals.auth_failed.emit(identity.generation),
            on_missing_channel=lambda channel_id, name: self._drop_channel(
                identity.account_id, channel_id, name),
            on_log=self.log,
            purge_on_trigger=True)
        try:
            while not stop_event.is_set():
                if not self.monitoring_active or self.account_id != identity.account_id:
                    stop_event.wait(2.0)
                    continue

                _, active, watchlist, error = watchlist_store.snapshot(identity.account_id)
                if error:
                    self.log(f"Watchlist read failed: {error}")
                    stop_event.wait(POLL_INTERVAL)
                    continue
                if not watchlist or not active:
                    stop_event.wait(POLL_INTERVAL)
                    continue

                monitor.sweep(watchlist)
                stop_event.wait(POLL_INTERVAL)
        finally:
            monitor.close()

    def _notify_trigger(self, _channel_id, name):
        # called on the guard thread; the signal is queued to the tray thread.
        # guard_core already logged the trigger.
        self.signals.notification_requested.emit(
            "Keyword Detected", f"Trigger found in {name}. Purging channel history!")

    def _drop_channel(self, account_id, channel_id, name):
        self.log(f"Channel {name} is gone; removing it from the watchlist.")
        watchlist_store.remove_channel(account_id, channel_id)

    # --------------------------------------------------------------- actions

    def open_lazer(self):
        import subprocess
        try:
            main_path = os.path.join(APP_DIR, "main.py")
            creationflags = subprocess.DETACHED_PROCESS if sys.platform == 'win32' else 0
            subprocess.Popen([sys.executable, main_path], cwd=APP_DIR,
                             creationflags=creationflags,
                             start_new_session=(sys.platform != 'win32'))
        except OSError as error:
            self.signals.notification_requested.emit(
                "Error", f"Failed to launch Lazer: {error}")

    def exit_daemon(self):
        self.monitoring_active = False
        self.timer.stop()
        if self.stop_event is not None:
            self.stop_event.set()

        deadline = time.monotonic() + THREAD_JOIN_TIMEOUT
        threads = [self.monitor_thread] + list(self.retired_threads)
        for thread in threads:
            if thread is None or not thread.is_alive():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)

        app_config.clear_heartbeat()
        QApplication.quit()

    def run(self):
        return self.app.exec()


def setup_windows_autostart():
    try:
        import winreg
    except ImportError:
        print("Autostart registration is Windows-only.", file=sys.stderr)
        return False

    try:
        # anchored to the checkout, not the working directory
        launcher = os.path.join(APP_DIR, "main.py")
        python_exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(python_exe):
            python_exe = sys.executable
        cmd = f'"{python_exe}" "{launcher}" daemon'

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as reg_key:
            winreg.SetValueEx(reg_key, AUTOSTART_VALUE, 0, winreg.REG_SZ, cmd)
        print(f"Autostart registered: {cmd}")
        return True
    except OSError as error:
        print(f"Autostart registration failed: {error}", file=sys.stderr)
        return False


def remove_windows_autostart():
    try:
        import winreg
    except ImportError:
        print("Autostart is Windows-only; nothing to remove.", file=sys.stderr)
        return True

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as reg_key:
            winreg.DeleteValue(reg_key, AUTOSTART_VALUE)
    except FileNotFoundError:
        print("Autostart was not registered; nothing to remove.")
        return True
    except OSError as error:
        print(f"Could not remove autostart: {error}", file=sys.stderr)
        return False
    print("Autostart removed.")
    return True


def main():
    parser = argparse.ArgumentParser(description="LAZER System Tray Daemon")
    parser.add_argument("--token", help="Discord token (defaults to the saved session)")
    parser.add_argument("--headless", action="store_true", help="Run without a tray icon")
    parser.add_argument("--autostart", action="store_true", help="Register Windows autostart and exit")
    parser.add_argument("--no-autostart", action="store_true",
                        help="Remove the Windows autostart entry and exit")
    args = parser.parse_args()

    if args.no_autostart:
        return 0 if remove_windows_autostart() else 1
    if args.autostart:
        return 0 if setup_windows_autostart() else 1

    # QSystemTrayIcon needs a QApplication before it can answer
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not args.headless and not QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray available on this desktop session. "
              "Use --headless if you really want it to run invisibly.", file=sys.stderr)
        return 1

    instance_lock = QLockFile(os.path.join(app_paths.data_dir(), "daemon.lock"))
    if not instance_lock.tryLock(200):
        print("Another LAZER background monitor is already running.", file=sys.stderr)
        return 0

    try:
        daemon = TrayDaemon(token=args.token, headless=args.headless)
        return daemon.run()
    except SystemExit:
        raise
    except Exception as error:
        import traceback
        try:
            with open(ERROR_LOG, "a", encoding="utf-8") as handle:
                handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {error}\n")
                handle.write(traceback.format_exc())
        except OSError:
            pass
        traceback.print_exc()
        return 1
    finally:
        instance_lock.unlock()


if __name__ == "__main__":
    sys.exit(main())