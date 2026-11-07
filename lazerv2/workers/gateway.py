# gateway client, only used for presence

import json
import random

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QAbstractSocket
from PyQt6.QtWebSockets import QWebSocket

GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

# set False to stop MainWindow opening a real socket
ENABLED = True

# opcodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# close codes where reconnecting won't help
FATAL_CLOSE_CODES = {
    4004,   # authentication failed
    4010,   # invalid shard
    4011,   # sharding required
    4012,   # invalid API version
    4013,   # invalid intents
    4014,   # disallowed intents
}

# close codes after which the session can't be resumed, only re-identified
SESSION_RESET_CLOSE_CODES = {
    4007,   # invalid seq
    4009,   # session timed out
}

VALID_STATUSES =("online", "idle", "dnd", "invisible", "offline")

BACKOFF_BASE = 1.0
BACKOFF_MAX = 60.0


class GatewayProtocol:
    # the state machine, no I/O

    def __init__(self, token, identify_properties=None):
        self.token = token
        self.properties = identify_properties or {
            "os": "Windows",
            "browser": "Chrome",
            "device": "",
        }
        self.reset()

    def reset(self):
        self.sequence = None
        self.session_id = None
        self.resume_url = None
        self.heartbeat_interval = None
        self.awaiting_ack = False
        self.status = "unknown"
        self.custom_status = ""
        self.user_id = None
        self.fatal = False
        self._backoff_attempt = 0


    def identify_frame(self):
        # user IDENTIFY, `intents` is the bot shape and gets rejected
        return {
            "op": OP_IDENTIFY,
            "d": {
                "token": self.token,
                "properties": self.properties,
                "compress": False,
            },
        }

    def resume_frame(self):
        return {
            "op": OP_RESUME,
            "d": {
                "token": self.token,
                "session_id": self.session_id,
                "seq": self.sequence,
            },
        }

    def heartbeat_frame(self):
        return {"op": OP_HEARTBEAT, "d": self.sequence}

    def can_resume(self):
        return bool(self.session_id) and self.sequence is not None

    def hello_response(self):
        # RESUME if we can, otherwise IDENTIFY
        return self.resume_frame() if self.can_resume() else self.identify_frame()


    def handle(self, payload):
        # one payload in, (frames_to_send, events) out
        if not isinstance(payload, dict):
            return [], []

        op = payload.get("op")
        seq = payload.get("s")
        if seq is not None:
            self.sequence = seq

        if op == OP_HELLO:
            data = payload.get("d") or {}
            interval = data.get("heartbeat_interval")
            self.heartbeat_interval = interval if isinstance(interval, (int, float)) else 41250
            self.awaiting_ack = False
            return [self.hello_response()], [("hello", self.heartbeat_interval)]

        if op == OP_HEARTBEAT_ACK:
            self.awaiting_ack = False
            self._backoff_attempt = 0
            return [], []

        if op == OP_HEARTBEAT:
            # gateway wants a heartbeat now
            return [self.heartbeat_frame()], []

        if op == OP_RECONNECT:
            return [], [("reconnect", self.can_resume())]

        if op == OP_INVALID_SESSION:
            # d:true means the session can still be resumed
            resumable = bool(payload.get("d"))
            if not resumable:
                self.session_id = None
                self.sequence = None
            return [], [("reconnect", resumable)]

        if op == OP_DISPATCH:
            return [], self._dispatch(payload.get("t"), payload.get("d") or {})

        return [], []

    def _dispatch(self, event, data):
        if not isinstance(data, dict):
            return []

        if event == "READY":
            self.session_id = data.get("session_id") or self.session_id
            self.resume_url = data.get("resume_gateway_url") or self.resume_url
            # remember who we are; presence updates arrive for other users too
            self.user_id = (data.get("user") or {}).get("id") or self.user_id
            events = [("ready", None)]
            status = self._status_from_ready(data)
            if status:
                events.append(self._set_status(status, self._custom_from(data)))
            return [e for e in events if e]

        if event in ("SESSIONS_REPLACE", "USER_SETTINGS_UPDATE", "USER_SETTINGS_PROTO_UPDATE"):
            status = self._status_from_sessions(data)
            if status:
                return [e for e in [self._set_status(status, self.custom_status)] if e]
            return []

        if event == "PRESENCE_UPDATE":
            # friends' updates land here too, only take our own
            user = data.get("user") or {}
            if self.user_id and user.get("id") == self.user_id and data.get("status"):
                return [e for e in [self._set_status(data["status"], self.custom_status)] if e]

        return []

    def _status_from_ready(self, data):
        settings = data.get("user_settings")
        if isinstance(settings, dict) and settings.get("status"):
            return settings["status"]
        sessions = data.get("sessions")
        if isinstance(sessions, list) and sessions:
            first = sessions[0]
            if isinstance(first, dict) and first.get("status"):
                return first["status"]
        return None

    def _status_from_sessions(self, data):
        if data.get("status"):
            return data["status"]
        sessions = data if isinstance(data, list) else data.get("sessions")
        if isinstance(sessions, list) and sessions:
            first = sessions[0]
            if isinstance(first, dict) and first.get("status"):
                return first["status"]
        return None

    def _custom_from(self, data):
        sessions = data.get("sessions")
        if isinstance(sessions, list):
            for session in sessions:
                if not isinstance(session, dict):
                    continue
                for activity in session.get("activities") or []:
                    if isinstance(activity, dict) and activity.get("type") == 4:
                        return activity.get("state") or ""
        return ""

    def _set_status(self, status, custom):
        status = status if status in VALID_STATUSES else "unknown"
        if status == self.status and custom == self.custom_status:
            return None
        self.status = status
        self.custom_status = custom or ""
        return ("presence", (self.status, self.custom_status))


    def handle_close(self, code, consume_backoff=True):
        # ('fatal', reason) or ('retry', seconds); seconds is 0 when the
        # caller already has a reconnect scheduled and passes False
        if code in FATAL_CLOSE_CODES:
            self.fatal = True
            reason = "token rejected" if code == 4004 else f"gateway refused ({code})"
            return ("fatal", reason)
        if code in SESSION_RESET_CLOSE_CODES:
            # resuming would just be rejected again, forever
            self.session_id = None
            self.sequence = None
        return ("retry", self.next_backoff() if consume_backoff else 0)

    def next_backoff(self):
        delay = min(BACKOFF_BASE * (2 ** self._backoff_attempt), BACKOFF_MAX)
        self._backoff_attempt += 1
        # jittered so clients don't all reconnect at once
        return delay * (0.8 + random.random() * 0.4)

    def reset_backoff(self):
        self._backoff_attempt = 0

    def missed_ack(self):
        # last heartbeat never came back, the link is dead
        return self.awaiting_ack

    def mark_heartbeat_sent(self):
        self.awaiting_ack = True


class GatewayClient(QObject):

    presence_changed = pyqtSignal(str, str)
    state_changed = pyqtSignal(str)
    auth_failed = pyqtSignal()

    def __init__(self, token, parent=None):
        super().__init__(parent)
        self.protocol = GatewayProtocol(token)
        self._enabled = False
        self._last_state = None
        self._interval_ms = 41250

        self.socket = QWebSocket()
        self.socket.connected.connect(self._on_connected)
        self.socket.disconnected.connect(self._on_disconnected)
        self.socket.textMessageReceived.connect(self._on_message)
        # a failed handshake may never reach "disconnected", retry explicitly
        self.socket.errorOccurred.connect(self._on_socket_error)

        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.timeout.connect(self._send_heartbeat)

        # the jittered first beat; a member timer so a reconnect can cancel it
        self.first_beat_timer = QTimer(self)
        self.first_beat_timer.setSingleShot(True)
        self.first_beat_timer.timeout.connect(self._send_first_heartbeat)

        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.setSingleShot(True)
        self.reconnect_timer.timeout.connect(self._open)


    def start(self):
        if not ENABLED:
            self._emit_state("disabled")
            return
        if not self.protocol.token:
            self._emit_state("no token")
            return
        self._enabled = True
        self._open()

    def stop(self):
        self._enabled = False
        self._stop_heartbeat()
        self.reconnect_timer.stop()
        try:
            self.socket.close()
        except RuntimeError:
            pass
        self._emit_state("stopped")

    def _open(self):
        if not self._enabled or self.protocol.fatal:
            return
        if self.socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            # a close is still in flight; try again shortly instead of stalling
            QTimer.singleShot(500, self._open)
            return
        self._emit_state("connecting")
        url = self.protocol.resume_url or GATEWAY_URL
        if self.protocol.resume_url and "?" not in url:
            url = f"{url}/?v=10&encoding=json"
        self.socket.open(QUrl(url))

    def _schedule_reconnect(self, delay):
        if not self._enabled or self.protocol.fatal:
            return
        self._emit_state("reconnecting")
        self.reconnect_timer.start(int(delay * 1000))


    def _on_connected(self):
        self.reconnect_timer.stop()
        self._emit_state("connected")

    def _on_socket_error(self, _error):
        if not self._enabled or self.protocol.fatal or self.reconnect_timer.isActive():
            return
        self._stop_heartbeat()
        self._schedule_reconnect(self.protocol.next_backoff())

    def _on_disconnected(self):
        self._stop_heartbeat()
        if not self._enabled:
            return

        code = self.socket.closeCode()
        already_scheduled = self.reconnect_timer.isActive()
        action, value = self.protocol.handle_close(
            code.value if hasattr(code, "value") else int(code),
            consume_backoff=not already_scheduled)
        if action == "fatal":
            self.reconnect_timer.stop()
            self._emit_state(value)
            self.auth_failed.emit()
            return
        if already_scheduled:
            # a reconnect is already scheduled (we closed it ourselves, or the
            # error handler got there first); scheduling again would restart
            # the timer with a doubled backoff
            return
        self._schedule_reconnect(value)

    def _on_message(self, text):
        try:
            payload = json.loads(text)
        except ValueError:
            # unparseable payload, not an auth failure
            return

        frames, events = self.protocol.handle(payload)
        for frame in frames:
            self._send(frame)

        for name, data in events:
            if name == "hello":
                self._start_heartbeat(data)
            elif name == "ready":
                self.protocol.reset_backoff()
                self._emit_state("ready")
            elif name == "presence":
                self.presence_changed.emit(data[0], data[1])
            elif name == "reconnect":
                self._reconnect_now()

    def _send(self, frame):
        try:
            self.socket.sendTextMessage(json.dumps(frame))
        except (RuntimeError, TypeError):
            pass

    def _reconnect_now(self):
        self._stop_heartbeat()
        # schedule before closing, so the disconnected handler sees it
        self._schedule_reconnect(self.protocol.next_backoff())
        try:
            self.socket.close()
        except RuntimeError:
            pass


    def _start_heartbeat(self, interval_ms):
        # first beat is jittered, then the regular interval counts from it;
        # starting both at once could land a regular beat right after the
        # first one, before its ACK, and look like a dead link
        self._stop_heartbeat()
        self._interval_ms = int(interval_ms)
        self.first_beat_timer.start(int(interval_ms * random.random()))

    def _send_first_heartbeat(self):
        self._send_heartbeat()
        if self._enabled:
            self.heartbeat_timer.start(self._interval_ms)

    def _stop_heartbeat(self):
        self.first_beat_timer.stop()
        self.heartbeat_timer.stop()

    def _send_heartbeat(self):
        if self.protocol.missed_ack():
            # socket is open but nothing is answering, drop it
            self._emit_state("no heartbeat ack")
            self._reconnect_now()
            return
        self.protocol.mark_heartbeat_sent()
        self._send(self.protocol.heartbeat_frame())

    def _emit_state(self, state):
        if state != self._last_state:
            self._last_state = state
            self.state_changed.emit(state)
