import os
import subprocess
import sys
import time

from PyQt6.QtCore import QPoint, QStandardPaths, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QMessageBox,
                             QStackedWidget, QWidget)

from lazerv2 import app_config
from lazerv2 import app_paths
from lazerv2 import session_store
from lazerv2.guard_utils import current_time_ms
from lazerv2.globals import context, get_avatar_url
from lazerv2.ui.account_menu import AccountMenu
from lazerv2.ui.guard_page import GuardPage
from lazerv2.ui.overview import OverviewPage
from lazerv2.ui.purge_page import PurgePage
from lazerv2.ui.sidebar import Sidebar
from lazerv2.ui import threads
from lazerv2.ui.theme import RAIL_COLLAPSED, theme
from lazerv2.ui.widgets import ImageFetcher
from lazerv2.workers.deletion import DeletionWorker
from lazerv2.workers.fetcher import DataFetcher
from lazerv2.workers.gateway import GatewayClient
from lazerv2.workers.monitor import POLL_INTERVAL as MONITOR_POLL_INTERVAL
from lazerv2.workers.monitor import MonitorWorker

SYNC_INTERVAL_MS = 2000
THREAD_STOP_TIMEOUT_MS = 5000
# a cold start of Python + PyQt6 can take several seconds on a slow disk
DAEMON_START_TIMEOUT = 6.0
# after a guard wipe is stopped, ignore that channel's trigger for a couple of
# polls: a sweep already in flight may still report the old trigger message
STOPPED_WIPE_QUIET_S = MONITOR_POLL_INTERVAL * 3
BACKUP_FOLDER = "Lazer backups"


def backup_dir():
    # Documents\Lazer backups, where people will look; the profile folder
    # only if there is no Documents location
    documents = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation)
    if documents:
        return os.path.join(documents, BACKUP_FOLDER)
    return app_paths.path("backups")


def purge_target_for(channel_id, name, entry=None):
    # the richest target we can build for a guard wipe: with a guild id (or a
    # DM marker) the worker can search instead of reading the whole history
    target = {"id": channel_id, "name": name}
    entry = entry if isinstance(entry, dict) else {}
    if entry.get("guild_id"):
        target.update(type="channel", guild_id=str(entry["guild_id"]))
        return target
    if entry.get("kind") == "dm":
        target["type"] = "dm"
        return target
    for dm in context.dms or []:
        if str(dm.get("id")) == str(channel_id):
            target["type"] = "dm"
            return target
    for server in context.servers or []:
        for channel in server.get("channels", []):
            if str(channel.get("id")) == str(channel_id):
                target.update(type="channel", guild_id=str(server["id"]))
                return target
    return target


class MainWindow(QWidget):
    logout_requested = pyqtSignal()
    account_switch_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lazer V2")
        # objectName so the stylesheet paints the window background
        self.setObjectName("Window")
        self.resize(1100, 700)
        self.setMinimumSize(940, 620)

        self.worker = None
        self.wipes_in_flight = set()
        self._auto_wipe_channels = []   # channel ids of the running guard wipe
        self._quiet_until = {}          # channel id -> monotonic time
        self.avatar_bytes = None
        self._avatar_fetcher = None
        self._avatar_hash = None
        self._purge_target = "channel"

        # every monitor belongs to the account that is signed in right now
        context.bind_account(getattr(context.user, "id", None))

        self.watchlist_sync_timer = QTimer(self)
        self.watchlist_sync_timer.timeout.connect(self.sync_watchlist_from_file)

        self.init_ui()

        self.fetcher = DataFetcher(context.token)
        self.fetcher.counts_ready.connect(self.on_counts_ready)
        self.fetcher.data_loaded.connect(self.cache_data)
        self.fetcher.error_occurred.connect(self.on_fetch_error)
        self.fetcher.auth_failed.connect(self.on_fetch_auth_failed)
        self.fetcher.start()

        self.monitor = MonitorWorker(
            context.token, getattr(context.user, "id", None))
        self.monitor.wipe_triggered.connect(self.auto_wipe_handler)
        self.monitor.auth_failed.connect(self.on_monitor_auth_failed)
        # if the daemon is already up it owns monitoring; the GUI stays idle
        self.monitor.enabled = not app_config.daemon_running()
        self.monitor.start()

        # presence, only runs while this window is open
        self.gateway = GatewayClient(context.token)
        self.gateway.presence_changed.connect(self.on_presence_changed)
        self.gateway.state_changed.connect(self.on_gateway_state)
        self.gateway.auth_failed.connect(self.on_monitor_auth_failed)
        self.gateway.start()

        self.load_profile_pic()
        self.sync_watchlist_from_file()
        self.watchlist_sync_timer.start(SYNC_INTERVAL_MS)

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # gutter for the collapsed rail, the rail itself is an overlay
        gutter = QWidget()
        gutter.setFixedWidth(RAIL_COLLAPSED)
        main_layout.addWidget(gutter)

        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack)

        self.overview_page = OverviewPage()

        self.purge_page = PurgePage()
        self.purge_page.purge_requested.connect(self.start_deletion)
        self.purge_page.preview_requested.connect(self.start_preview)
        self.purge_page.stop_requested.connect(self.stop_deletion)
        self.purge_page.confirm_requested.connect(self.confirm_deletion)
        self.purge_page.cancel_requested.connect(self.cancel_deletion)

        self.guard_page = GuardPage()
        self.guard_page.monitors_added.connect(self.add_monitors)
        self.guard_page.monitors_removed.connect(self.remove_monitors)
        self.guard_page.entry_edited.connect(self.edit_monitor)
        self.guard_page.guard_toggled.connect(self.set_guard_armed)

        for page in (self.overview_page, self.purge_page, self.guard_page):
            self.stack.addWidget(page)

        self.sidebar = Sidebar(self)
        self.sidebar.page_changed.connect(self.stack.setCurrentIndex)
        self.sidebar.theme_toggle_requested.connect(self.toggle_theme)
        self.sidebar.account_clicked.connect(self.show_account_menu)
        self.sidebar.set_account(context.user.username if context.user else "Account")

        self.account_menu = AccountMenu(self)
        self.account_menu.account_selected.connect(self.on_account_selected)
        self.account_menu.add_account_requested.connect(self.handle_logout)
        self.account_menu.logout_requested.connect(self.handle_logout)

        if context.user:
            self.overview_page.set_user(context.user)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # height follows the window, width belongs to the hover animation
        self.sidebar.setFixedHeight(self.height())
        self.sidebar.move(0, 0)
        self.sidebar.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self.sidebar.setFixedHeight(self.height())
        self.sidebar.raise_()
        self.sync_watchlist_from_file()
        self.watchlist_sync_timer.start(SYNC_INTERVAL_MS)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.watchlist_sync_timer.stop()

    def toggle_theme(self):
        theme.toggle(QApplication.instance())

    def show_account_menu(self):
        menu = self.account_menu.build(current_token=context.token)
        button = self.sidebar.account_btn
        # pop upwards, the button sits at the bottom of the rail
        point = button.mapToGlobal(QPoint(0, 0))
        menu.exec(QPoint(point.x(), point.y() - menu.sizeHint().height() - 4))
        menu.deleteLater()

    def on_account_selected(self, token):
        self.account_switch_requested.emit(token)

    def load_profile_pic(self):
        # fetched off the UI thread, shared with the rail and overview
        if not context.user:
            return
        self._avatar_hash = getattr(context.user, "avatar", None)
        cached = session_store.get_cached_avatar(
            getattr(context.user, "id", None), self._avatar_hash)
        if cached:
            self.apply_avatar(cached)
            return

        self._avatar_fetcher = ImageFetcher(get_avatar_url(context.user))
        self._avatar_fetcher.loaded.connect(self.on_avatar_loaded)
        self._avatar_fetcher.start()

    def on_avatar_loaded(self, data):
        session_store.cache_avatar(
            getattr(context.user, "id", None), data, self._avatar_hash)
        self.apply_avatar(data)

    def apply_avatar(self, data):
        self.avatar_bytes = data
        self.sidebar.set_account(
            (getattr(context.user, "global_name", None) or
             getattr(context.user, "username", "Account")), data)
        self.overview_page.set_user(context.user, data)

    def on_presence_changed(self, status, custom_text):
        self.overview_page.set_presence(status, custom_text)
        self.sidebar.set_presence(status)

    def on_gateway_state(self, state):
        # only show the transport state while it isn't usable
        if state != "ready":
            self.overview_page.set_presence("unknown", state)
            self.sidebar.set_presence("unknown")

    def on_counts_ready(self, dm_count, server_count):
        self.overview_page.set_counts(dms=dm_count, servers=server_count)

    def cache_data(self, dms, servers):
        # hand the channel list out to the pages
        channels = sum(len(server.get("channels", [])) for server in servers)
        self.overview_page.set_counts(dms=len(dms), servers=len(servers), channels=channels)
        self.purge_page.set_data(dms, servers)
        self.guard_page.set_data(dms, servers)

    def on_fetch_error(self, message):
        self.purge_page.on_status(f"Could not load channel list: {message}")
        self.overview_page.set_token_warning(message)

    def on_fetch_auth_failed(self):
        self.purge_page.on_status("Discord rejected the token; the channel list is unavailable.")
        self.overview_page.set_token_invalid("rejected by Discord")

    def on_monitor_auth_failed(self):
        self.purge_page.on_status("Monitoring stopped: Discord rejected the token.")
        self.overview_page.set_token_invalid("rejected by Discord")

    def update_monitor_ownership(self):
        # one owner only: while the daemon heartbeats, the in-window monitor idles
        self.monitor.enabled = not app_config.daemon_running()

    def sync_watchlist_from_file(self):
        # pick up edits made by the daemon
        previous = dict(context.watchlist)
        context.reload_watchlist()
        self.update_monitor_ownership()

        if (context.watchlist != previous
                or self.guard_page.watchlist_table.rowCount() != len(context.watchlist)):
            # don't rebuild the table mid-add
            if not self.guard_page.in_add_mode():
                self.guard_page.refresh(context.watchlist)

        if context.watchlist_error:
            self.guard_page.set_status(f"Watchlist: {context.watchlist_error}")
        self._refresh_guard_state()

    def _refresh_guard_state(self):
        self.guard_page.set_guard_state(context.monitoring_active, bool(context.watchlist))
        self.overview_page.refresh_status(context.watchlist, context.monitoring_active)

    def set_guard_armed(self, armed):
        if armed:
            if not context.watchlist:
                self._refresh_guard_state()
                return
            # arming starts a fresh guard window: a trigger word typed, or a
            # message sent, while the guard was off must not fire on re-arm
            armed_at = current_time_ms()

            def apply(entries):
                for entry in entries.values():
                    if isinstance(entry, dict):
                        entry["armed_at"] = armed_at

            ok = context.update_watchlist(apply, active=True)
        else:
            ok = context.set_monitoring_active(False)

        if not ok:
            context.reload_watchlist()
            self.guard_page.set_status("Could not save the guard state; see the log.")
        else:
            self.guard_page.set_status("")
        self._refresh_guard_state()

    def deletion_running(self):
        return self.worker is not None and self.worker.isRunning()

    def start_deletion(self, channels, filters, catch_up=False):
        if self.deletion_running():
            self.purge_page.on_status("A purge is already running.")
            return False

        # a guard wipe (catch_up) was already asked for by the trigger word;
        # a manual purge stops after the scan and waits for the user
        self._auto_wipe_channels = [c["id"] for c in channels] if catch_up else []

        self._purge_target = ", ".join(c["name"] for c in channels[:2]) or "channel"
        if len(channels) > 2:
            self._purge_target += f" +{len(channels) - 2}"

        # a guard wipe is a panic button: no confirmation and no backup
        export_dir = backup_dir() if (filters.get("backup") and not catch_up) else None

        self.purge_page.on_started()
        self.worker = DeletionWorker(
            channels, filters, token=context.token,
            user_id=getattr(context.user, "id", None),
            catch_up=catch_up, require_confirmation=not catch_up,
            export_dir=export_dir)
        self.worker.awaiting_confirmation.connect(self._on_awaiting_confirmation)
        self.worker.channel_progress.connect(self.purge_page.on_status)
        self.worker.total_messages_updated.connect(self.purge_page.on_total)
        self.worker.message_progress.connect(self.purge_page.on_progress)
        self.worker.run_finished.connect(self.on_deletion_finished)
        self.worker.start()
        return True

    def start_preview(self, channels, filters):
        if self.deletion_running():
            self.purge_page.on_status("A purge is already running.")
            self.purge_page.abort_preview()
            return False

        self.purge_page.on_started(preview=True)
        self.worker = DeletionWorker(
            channels, filters, token=context.token,
            user_id=getattr(context.user, "id", None), dry_run=True)
        self.worker.channel_progress.connect(self.purge_page.on_status)
        self.worker.total_messages_updated.connect(self.purge_page.on_total)
        self.worker.run_finished.connect(self.on_deletion_finished)
        self.worker.start()
        return True

    def on_deletion_finished(self, deleted, gone, failed):
        if self.worker is not None and self.worker.dry_run:
            self.purge_page.on_preview_finished(len(self.worker.all_messages))
            return
        self.purge_page.on_finished(deleted, gone, failed)
        self.wipes_in_flight.clear()
        self._auto_wipe_channels = []
        # a purge declined at the confirmation step deleted nothing
        if not (self.worker is not None and self.worker.cancelled):
            app_config.record_purge(deleted, self._purge_target)
        self.overview_page.refresh_status(context.watchlist, context.monitoring_active)

    def _on_awaiting_confirmation(self, total):
        export_dir = self.worker.export_dir if self.worker is not None else None
        self.purge_page.on_awaiting_confirmation(total, export_dir)

    def confirm_deletion(self):
        if self.worker is not None and self.worker.is_awaiting_confirmation():
            self.worker.confirm()

    def cancel_deletion(self):
        if self.worker is not None and self.worker.is_awaiting_confirmation():
            self.worker.cancel()

    def stop_deletion(self):
        if not self.deletion_running():
            return
        if self._auto_wipe_channels:
            self._disarm_stopped_wipe(self._auto_wipe_channels)
        self.worker.stop()
        self.purge_page.on_status("Stopping...")

    def _disarm_stopped_wipe(self, channel_ids):
        # The trigger message is still in the channel, so without this the
        # monitor finds it again and restarts the wipe 3 seconds after Stop.
        # Starting a fresh guard window puts that message out of scope.
        armed_at = current_time_ms()
        wanted = set(channel_ids)

        def apply(entries):
            for channel_id, entry in entries.items():
                if channel_id in wanted and isinstance(entry, dict):
                    entry["armed_at"] = armed_at

        context.update_watchlist(apply)
        quiet_until = time.monotonic() + STOPPED_WIPE_QUIET_S
        for channel_id in channel_ids:
            self._quiet_until[channel_id] = quiet_until

    def auto_wipe_handler(self, ch_id, name):
        if time.monotonic() < self._quiet_until.get(ch_id, 0):
            return
        self._quiet_until.pop(ch_id, None)
        # a manual purge parked at its confirmation step must not hold up a
        # guard wipe; drop it, and the next poll (3s) starts the wipe
        if self.worker is not None and self.worker.is_awaiting_confirmation():
            self.worker.cancel()
            self.purge_page.on_status("Cancelled: a guard trigger fired. Nothing was deleted.")
            return
        # the monitor re-fires every poll, so only allow one wipe per channel
        if ch_id in self.wipes_in_flight or self.deletion_running():
            return
        self.wipes_in_flight.add(ch_id)
        target = purge_target_for(ch_id, name, context.watchlist.get(ch_id))
        if not self.start_deletion([target], {}, catch_up=True):
            self.wipes_in_flight.discard(ch_id)

    def add_monitors(self, channels, trigger, ttl, match_words=True):
        armed_at = current_time_ms()

        def apply(entries):
            for channel in channels:
                entry = {
                    "wipe_key": trigger,
                    "ttl": ttl,
                    "name": channel['name'],
                    "armed_at": armed_at,
                    "match_words": bool(match_words),
                }
                # lets a trigger wipe search instead of reading all history
                if channel.get("guild_id"):
                    entry["guild_id"] = str(channel["guild_id"])
                elif channel.get("type") == "dm":
                    entry["kind"] = "dm"
                entries[channel['id']] = entry

        if not context.update_watchlist(apply, active=True):
            context.reload_watchlist()
            self.guard_page.set_status("Could not save the watchlist; see the log.")
        else:
            self.guard_page.set_status("")
        self.guard_page.refresh(context.watchlist)
        self._refresh_guard_state()

    def remove_monitors(self, channel_ids):
        def apply(entries):
            for channel_id in channel_ids:
                entries.pop(channel_id, None)

        if not context.update_watchlist(apply):
            context.reload_watchlist()
            self.guard_page.set_status("Could not save the watchlist; see the log.")
        else:
            # removing the last monitor disarms; removing one of several must
            # not re-arm a guard the user switched off
            if not context.watchlist:
                context.set_monitoring_active(False)
            self.guard_page.set_status("")
        self.guard_page.refresh(context.watchlist)
        self._refresh_guard_state()

    def edit_monitor(self, channel_id, field, value):
        entry = context.watchlist.get(channel_id)
        if not isinstance(entry, dict):
            return
        if field == "ttl_invalid":
            self.guard_page.revert_cell(channel_id, "ttl")
            self.guard_page.set_status("TTL must be a whole number of seconds.")
            return
        if field == "match_invalid":
            self.guard_page.revert_cell(channel_id, "match_words")
            self.guard_page.set_status("Match must be 'Whole word' or 'Contains'.")
            return
        # typing the same value must not move the guard window
        if entry.get(field) == value:
            return

        armed_at = current_time_ms()

        def apply(entries):
            target = entries.get(channel_id)
            if not isinstance(target, dict):
                return
            target[field] = value
            if field in {"wipe_key", "ttl", "match_words"}:
                target["armed_at"] = armed_at

        if not context.update_watchlist(apply):
            context.reload_watchlist()
            self.guard_page.refresh(context.watchlist)
            self.guard_page.set_status("Could not save the change; see the log.")
        else:
            self.guard_page.set_status("")

    def stop_thread(self, thread):
        # ask it to stop and join it; one that is still busy (a request in
        # flight) is parked to finish on its own rather than terminate()d
        threads.stop_and_park(thread, THREAD_STOP_TIMEOUT_MS)

    def cleanup(self):
        self.watchlist_sync_timer.stop()
        gateway = getattr(self, "gateway", None)
        if gateway is not None:
            gateway.stop()
        self.stop_thread(getattr(self, "monitor", None))
        self.stop_thread(getattr(self, "worker", None))
        for name in ("fetcher", "_avatar_fetcher"):
            # without a parked reference, a fetcher still walking guilds would
            # be destroyed with this window and abort the process
            self.stop_thread(getattr(self, name, None))
        # icon fetchers are not windows, so nothing else closes them
        for page in (getattr(self, "purge_page", None), getattr(self, "guard_page", None)):
            picker = getattr(page, "picker", None)
            if picker is not None:
                picker.shutdown()

    def handle_logout(self):
        context.token = None
        context.user = None
        # monitors are per account, so drop the bound list with the session
        context.account_id = None
        context.watchlist = {}
        context.monitoring_active = False
        self.logout_requested.emit()

    def launch_background_daemon(self):
        launcher = os.path.join(app_paths.project_root(), "main.py")
        if not os.path.exists(launcher):
            QMessageBox.warning(self, "Error", f"Launcher not found at: {launcher}")
            return False

        startup_info = None
        creationflags = 0
        if sys.platform == 'win32':
            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup_info.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.DETACHED_PROCESS

        try:
            process = subprocess.Popen(
                [sys.executable, launcher, "daemon"],
                cwd=app_paths.project_root(),
                startupinfo=startup_info,
                creationflags=creationflags,
                start_new_session=(sys.platform != 'win32'),
            )
        except OSError as error:
            QMessageBox.warning(self, "Error", f"Failed to start background monitor:\n{error}")
            return False

        # the daemon writes a heartbeat as soon as it is up; wait briefly so a
        # crash on startup is reported before the window disappears
        deadline = time.monotonic() + DAEMON_START_TIMEOUT
        while time.monotonic() < deadline:
            if app_config.daemon_running():
                return True
            if process.poll() is not None:
                break
            time.sleep(0.05)

        QMessageBox.warning(
            self, "Background monitor",
            "The background monitor did not start. Run "
            "`python main.py daemon` in a terminal to see why.")
        return False

    def closeEvent(self, event):
        if not (context.watchlist and context.monitoring_active):
            self.cleanup()
            event.accept()
            return

        reply = QMessageBox.question(
            self,
            "Conversation Guard Active",
            "You have Conversation Guard enabled.\n\n"
            "Keep monitoring in the background?\n"
            "(The daemon keeps running after Lazer closes.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )

        if reply == QMessageBox.StandardButton.Cancel:
            event.ignore()
            return

        if reply == QMessageBox.StandardButton.No:
            # monitoring off but keep the configured targets
            context.deactivate_watchlist()
            self.cleanup()
            event.accept()
            return

        context.monitoring_active = True
        context.set_monitoring_active(True)

        # never stack a second daemon on top of a live one
        if not app_config.daemon_running():
            if not self.launch_background_daemon():
                event.ignore()
                return

        self.cleanup()
        event.accept()