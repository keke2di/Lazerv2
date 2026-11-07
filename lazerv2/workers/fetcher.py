import threading

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from lazerv2 import http_client
from lazerv2.globals import context, BASE_URL, snowflake_to_ms

TIMEOUT = 15
GUILD_DELAY = 0.15          # pause between per-guild channel lookups
MAX_RATE_LIMIT_RETRIES = 3

# guild channel types that hold messages directly, with how the picker
# describes them. Forum and media channels (15, 16) hold only threads; those
# are reached through a whole-server purge, which searches threads too.
MESSAGE_CHANNEL_TYPES = {
    0: "Channel",
    2: "Voice chat",
    5: "Announcements",
    13: "Stage chat",
}


class DataFetcher(QThread):
    # loads DMs and guild text channels in the background

    data_loaded = pyqtSignal(list, list)
    # sent early so the dashboard isn't empty during the guild walk
    counts_ready = pyqtSignal(int, int)
    error_occurred = pyqtSignal(str)
    auth_failed = pyqtSignal()

    def __init__(self, token=None):
        super().__init__()
        self.token = token or context.token
        self.headers = {"Authorization": self.token}
        self.session = requests.Session()
        self._auth_failed = False
        self._last_status = None
        # a big guild list takes a while; stop() ends it between requests
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

    def run(self):
        try:
            dms = self.fetch_dms()
            if self._auth_failed:
                self.auth_failed.emit()
                return

            guilds = self._get(f"{BASE_URL}/users/@me/guilds") or []
            if self._auth_failed:
                self.auth_failed.emit()
                return
            self.counts_ready.emit(len(dms), len(guilds))

            servers = self.fetch_servers(guilds)
            if self.stopped():
                # the window that asked is going away; don't hand it a half list
                return
            context.dms = dms
            context.servers = servers

            if not dms and not servers:
                self.error_occurred.emit(self._reason("Could not load any DMs or servers"))
            self.data_loaded.emit(dms, servers)
        except Exception as error:
            self.error_occurred.emit(str(error))
        finally:
            self.session.close()

    def _reason(self, prefix):
        if self._last_status is None:
            return f"{prefix}: could not reach Discord."
        return f"{prefix} (HTTP {self._last_status})."

    def _get(self, url):
        # data on success, None on any failure
        if self.stopped():
            return None
        try:
            res = http_client.request(
                self.session, "GET", url, headers=self.headers, timeout=TIMEOUT,
                attempts=2, rate_limit_attempts=MAX_RATE_LIMIT_RETRIES,
                stop_event=self._stop_event)
        except http_client.TransportError as error:
            print(f"DataFetcher request error for {url}: {error}")
            self._last_status = None
            return None

        self._last_status = res.status_code
        if res.status_code == 401:
            self._auth_failed = True
            return None
        if res.status_code != 200:
            print(f"DataFetcher: HTTP {res.status_code} for {url}")
            return None

        try:
            return res.json()
        except ValueError:
            return None

    def fetch_dms(self):
        channels = self._get(f"{BASE_URL}/users/@me/channels")
        if not channels:
            return []

        if not isinstance(channels, list):
            return []

        dms = []
        for channel in channels:
            if not isinstance(channel, dict) or not channel.get("id"):
                continue
            if channel.get("type") not in (1, 3):   # DM, group DM
                continue
            recipients = [u.get("username", "?") for u in channel.get("recipients") or []
                          if isinstance(u, dict)]
            display_name = ", ".join(recipients) if recipients else "Unknown User"
            last_id = channel.get('last_message_id') or channel.get('id')
            if not last_id:
                continue
            dms.append({
                "id": channel["id"],
                "name": display_name,
                "type": "dm",
                "recipients": channel.get("recipients", []),
                "timestamp": snowflake_to_ms(last_id),
            })
        dms.sort(key=lambda item: item['timestamp'], reverse=True)
        return dms

    def fetch_servers(self, guilds=None):
        if guilds is None:
            guilds = self._get(f"{BASE_URL}/users/@me/guilds")
        if not isinstance(guilds, list) or not guilds:
            return []

        server_list = []
        for guild in guilds:
            if self._auth_failed or self.stopped():
                break
            # one odd entry must not take the whole channel list down with it
            if not isinstance(guild, dict) or not guild.get("id"):
                continue
            if self._stop_event.wait(GUILD_DELAY):
                break
            guild_id = guild["id"]
            channels = self._get(f"{BASE_URL}/guilds/{guild_id}/channels")
            if not isinstance(channels, list):
                channels = []
            entries = []
            for channel in sorted(
                    (c for c in channels if isinstance(c, dict)),
                    key=lambda c: (c.get("type") != 0, c.get("position") or 0)):
                kind = MESSAGE_CHANNEL_TYPES.get(channel.get("type"))
                if kind is None or not channel.get("id"):
                    continue
                entries.append({
                    "id": channel["id"],
                    "name": channel.get("name") or str(channel["id"]),
                    "type": "channel",
                    "guild_id": guild_id,
                    "kind": kind,
                })
            server_list.append({
                "id": guild_id,
                "name": guild.get("name") or f"Server {guild_id}",
                "type": "server",
                "icon": guild.get("icon"),
                "channels": entries,
            })
        return server_list