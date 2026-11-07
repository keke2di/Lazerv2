import threading

from PyQt6.QtCore import QThread, pyqtSignal

from lazerv2 import guard_core
from lazerv2.globals import context

POLL_INTERVAL = 3
IDLE_INTERVAL = 2


class MonitorWorker(QThread):
    # polls watched channels for the trigger word or expired messages

    wipe_triggered = pyqtSignal(str, str)
    auth_failed = pyqtSignal()

    def __init__(self, token=None, user_id=None):
        super().__init__()
        # snapshot identity: the daemon owns one account, so must this thread
        self.token = token or context.token
        self.user_id = user_id or getattr(context.user, "id", None)
        self._running = True
        self._stop = threading.Event()
        # the dashboard switches this off while the tray daemon owns monitoring
        self.enabled = True
        self._core = None

    def stop(self):
        self._running = False
        self._stop.set()
        if self._core is not None:
            self._core.stop_event.set()

    def _on_auth_failed(self):
        self._running = False
        self._stop.set()
        self.auth_failed.emit()

    def run(self):
        self._core = guard_core.GuardMonitor(
            self.token, self.user_id or "",
            stop_event=self._stop,
            on_trigger=lambda channel_id, name: self.wipe_triggered.emit(channel_id, name),
            on_auth_failed=self._on_auth_failed,
            on_log=print)
        try:
            while self._running and not self._stop.is_set():
                if not self._ready():
                    self._stop.wait(IDLE_INTERVAL)
                    continue
                self._core.sweep(context.watchlist)
                self._stop.wait(POLL_INTERVAL)
        finally:
            self._core.close()

    def _ready(self):
        return bool(self.enabled and self.token and self.user_id
                    and context.monitoring_active and context.watchlist)