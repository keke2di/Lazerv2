import datetime

from PyQt6.QtGui import QFont, QFontDatabase

from lazerv2 import watchlist_store
from lazerv2.guard_utils import DISCORD_EPOCH_MS


def get_snowflake_for_days(days):
    if days == 0:
        return None
    date = datetime.datetime.now() - datetime.timedelta(days=days)
    # int() wraps the ms value, floats can't be shifted
    return int(date.timestamp() * 1000 - DISCORD_EPOCH_MS) << 22

def snowflake_to_ms(snowflake):
    return (int(snowflake) >> 22) + DISCORD_EPOCH_MS

BASE_URL = "https://discord.com/api/v9"


class LazerContext:
    # live process state. Watchlists are per account, so nothing is loaded
    # until the dashboard binds the signed-in user.

    def __init__(self):
        self.token = None
        self.user = None
        self.dms = []
        self.servers = []
        self.watchlist = {}
        self.monitoring_active = False
        self.account_id = None
        self.watchlist_error = None

    def bind_account(self, user_id):
        self.account_id = str(user_id) if user_id else None
        if self.account_id:
            # the daemon follows whichever account the GUI is using
            watchlist_store.set_active_account(self.account_id)
        return self.reload_watchlist()

    def reload_watchlist(self):
        if not self.account_id:
            self.watchlist = {}
            self.monitoring_active = False
            self.watchlist_error = None
            return False

        entries, active, error = watchlist_store.load_entries(self.account_id)
        if error:
            # keep whatever is in memory rather than showing an empty list
            self.watchlist_error = error
            return False

        self.watchlist_error = None
        self.watchlist = entries
        self.monitoring_active = active
        return True

    def update_watchlist(self, change, active=None):
        # read-modify-write under the cross-process lock, so a daemon edit
        # between our read and our write is not clobbered
        if not self.account_id:
            return False
        ok, error, entries = watchlist_store.mutate_entries(
            self.account_id, change, active)
        if not ok:
            self.watchlist_error = error
            print(f"[watchlist] save failed: {error}")
            return False
        self.watchlist = entries
        self.watchlist_error = None
        if active is not None:
            self.monitoring_active = bool(active)
        return True

    def set_monitoring_active(self, active):
        if not self.account_id:
            return False
        ok, error = watchlist_store.set_active(self.account_id, active)
        if ok:
            self.monitoring_active = bool(active)
            self.watchlist_error = None
        else:
            self.watchlist_error = error
            print(f"[watchlist] save failed: {error}")
        return ok

    def deactivate_watchlist(self):
        return self.set_monitoring_active(False)


context = LazerContext()

_FONT_STACK = ("Inter", "Segoe UI Variable Display", "Segoe UI", "Helvetica Neue", "Arial")
_brand_family = None


def get_brand_family():
    # first font in the stack that's actually installed
    global _brand_family
    if _brand_family is not None:
        return _brand_family

    installed = set(QFontDatabase.families())
    if not installed:
        # no fonts listed yet, answer but don't cache it
        return _FONT_STACK[-1]

    _brand_family = next((f for f in _FONT_STACK if f in installed), _FONT_STACK[-1])
    return _brand_family


def get_brand_font(size, bold=False):
    font = QFont(get_brand_family(), size)
    if bold:
        font.setWeight(QFont.Weight.Bold)
    return font

def get_avatar_url(user):
    if hasattr(user, 'avatar') and user.avatar:
        return f"https://cdn.discordapp.com/avatars/{user.id}/{user.avatar}.png?size=128"
    return "https://cdn.discordapp.com/embed/avatars/0.png"