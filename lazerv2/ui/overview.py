# who you are, what's loaded, what the app is doing

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from lazerv2 import app_config
from lazerv2.ui.widgets import Avatar, Card, StatCard, StatusRow

AVATAR_SIZE = 72

PRESENCE_LABELS = {
    "online": "Online",
    "idle": "Idle",
    "dnd": "Do not disturb",
    "invisible": "Invisible",
    "offline": "Offline",
    "unknown": "Unknown",
}
# the badge has the real colours, this dot is just reachable or not
PRESENCE_DOTS = {
    "online": "on",
    "idle": "warn",
    "dnd": "danger",
    "invisible": "off",
    "offline": "off",
    "unknown": "off",
}


def _ago(timestamp):
    # '2m ago' style relative time
    if not timestamp:
        return "never"
    seconds = max(0, time.time() - timestamp)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


class OverviewPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("accent", "neutral")
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 34, 40, 34)
        layout.setSpacing(20)

        title = QLabel("Overview")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        layout.addWidget(self._build_profile())
        layout.addLayout(self._build_stats())
        layout.addWidget(self._build_status())
        layout.addStretch()


    def _build_profile(self):
        card = Card()
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(18)

        self.avatar = Avatar(AVATAR_SIZE)

        names = QVBoxLayout()
        names.setSpacing(3)
        names.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.display_name = QLabel("Not logged in")
        self.display_name.setObjectName("PageTitle")
        self.handle = QLabel("")
        self.handle.setObjectName("Muted")
        self.user_id = QLabel("")
        self.user_id.setObjectName("Dim")

        names.addWidget(self.display_name)
        names.addWidget(self.handle)
        names.addWidget(self.user_id)

        row_layout.addWidget(self.avatar)
        row_layout.addLayout(names)
        row_layout.addStretch()

        card.add(row)
        return card

    def _build_stats(self):
        grid = QGridLayout()
        grid.setSpacing(14)
        self.card_dms = StatCard("DIRECT MESSAGES")
        self.card_servers = StatCard("SERVERS")
        self.card_channels = StatCard("TEXT CHANNELS")
        self.card_monitors = StatCard("ACTIVE MONITORS", 0)
        for i, card in enumerate((self.card_dms, self.card_servers,
                                  self.card_channels, self.card_monitors)):
            grid.addWidget(card, 0, i)
        return grid

    def _build_status(self):
        card = Card("STATUS")
        self.row_presence = StatusRow("Presence", "connecting...", "off")
        self.row_token = StatusRow("Token", "checking...", "off")
        self.row_guard = StatusRow("Conversation Guard", "idle", "off")
        self.row_daemon = StatusRow("Background daemon", "not running", "off")
        self.row_purge = StatusRow("Last purge", "never", "off")
        for row in (self.row_presence, self.row_token, self.row_guard,
                    self.row_daemon, self.row_purge):
            card.add(row)
        return card

    def set_presence(self, status, custom_text=""):
        # status can be 'unknown'
        label = PRESENCE_LABELS.get(status, "Unknown")
        state = PRESENCE_DOTS.get(status, "off")
        detail = custom_text or ("not connected" if status == "unknown" else label)
        self.row_presence.update_row(state, detail)
        self.avatar.set_presence(status)


    def set_user(self, user, avatar_bytes=None):
        if not user:
            return
        display = getattr(user, "global_name", None) or getattr(user, "username", "Unknown")
        username = getattr(user, "username", "")
        self.display_name.setText(display)
        self.handle.setText(f"@{username}" if username else "")
        self.user_id.setText(str(getattr(user, "id", "")))
        if avatar_bytes:
            self.avatar.set_image(avatar_bytes)
        else:
            self.avatar.setText(display[:1].upper())
        self.row_token.update_row("on", "valid")

    def set_token_invalid(self, reason="rejected by Discord"):
        self.row_token.update_row("danger", reason)

    def set_token_warning(self, reason="could not reach Discord"):
        self.row_token.update_row("warn", reason)

    def set_counts(self, dms=None, servers=None, channels=None, monitors=None):
        # anything left None keeps its value
        if dms is not None:
            self.card_dms.set_value(dms)
        if servers is not None:
            self.card_servers.set_value(servers)
        if channels is not None:
            self.card_channels.set_value(channels)
        if monitors is not None:
            self.card_monitors.set_value(monitors)

    def refresh_status(self, watchlist, monitoring_active):
        count = len(watchlist)
        self.card_monitors.set_value(count)

        if monitoring_active and count:
            self.row_guard.update_row("on", f"{count} channel{'s' if count != 1 else ''} watched")
        elif count:
            self.row_guard.update_row("warn", f"{count} configured, monitoring off")
        else:
            self.row_guard.update_row("off", "no channels configured")

        age = app_config.daemon_heartbeat_age()
        if app_config.daemon_running():
            self.row_daemon.update_row("on", f"heartbeat {int(age)}s ago")
        elif age is not None:
            self.row_daemon.update_row("off", f"last seen {_ago(time.time() - age)}")
        else:
            self.row_daemon.update_row("off", "not running")

        last = app_config.get("last_purge")
        if isinstance(last, dict):
            count = last.get("count", 0)
            where = last.get("channel", "")
            self.row_purge.update_row(
                "off", f"{count} message{'s' if count != 1 else ''} in {where} · "
                       f"{_ago(last.get('timestamp'))}")
        else:
            self.row_purge.update_row("off", "never")
