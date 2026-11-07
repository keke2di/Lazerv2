# the Conversation Guard engine
#
# One implementation of "watch these channels, delete my expired messages,
# purge on a trigger" used by both the in-window monitor and the tray daemon.
# It has no Qt dependency; the callers supply callbacks and a stop event.

import threading

import requests

from lazerv2 import http_client
from lazerv2 import message_search
from lazerv2.guard_utils import (current_time_ms, is_message_after_guard_started,
                         is_message_ttl_expired, normalize_ttl,
                         snowflake_at_timestamp_ms, trigger_matches,
                         valid_armed_at)

BASE_URL = "https://discord.com/api/v9"
TIMEOUT = 10
DELETE_DELAY = 0.35
MESSAGE_PAGE_SIZE = 100
TRIGGER_MESSAGE_LIMIT = 5
MISSING_CHANNEL_STRIKES = 3
PURGE_PAGE_LIMIT = 100
CATCH_UP_PASSES = 2


class GuardMonitor:
    def __init__(self, token, user_id, *, account_id=None, session=None,
                 stop_event=None, on_trigger=None, on_auth_failed=None,
                 on_missing_channel=None, on_log=None, purge_on_trigger=False,
                 use_search=True):
        self.token = token
        self.user_id = str(user_id) if user_id else None
        self.account_id = str(account_id) if account_id else None
        self.session = session or requests.Session()
        self._owns_session = session is None
        self.headers = {"Authorization": token}
        self.stop_event = stop_event or threading.Event()
        self.on_trigger = on_trigger or (lambda _channel_id, _name: None)
        self.on_auth_failed = on_auth_failed or (lambda: None)
        self.on_missing_channel = on_missing_channel or (lambda _channel_id, _name: None)
        self.on_log = on_log or (lambda _message: None)
        self.purge_on_trigger = purge_on_trigger
        self.use_search = use_search
        self.missing_channels = {}   # channel id -> 404s in a row
        # channel id -> (armed_at, snowflake): every expired message older
        # than the snowflake was dealt with, so the next sweep stops there
        self._ttl_checked = {}
        # channel id -> search scope kwargs, or None for "history only"
        self._scopes = {}
        self.last_delete_outcome = None

    def stopped(self):
        return self.stop_event.is_set()

    def close(self):
        if self._owns_session:
            self.session.close()

    def sweep(self, watchlist):
        for channel_id, settings in list(watchlist.items()):
            if self.stopped():
                return
            if not isinstance(settings, dict):
                continue
            try:
                self.check_channel(channel_id, settings)
            except Exception as error:   # a bad channel must not kill the sweep
                self.on_log(f"Error checking channel {channel_id}: {error}")

    def check_channel(self, channel_id, settings):
        name = settings.get("name", channel_id)
        wipe_key = settings.get("wipe_key") or ""
        ttl = normalize_ttl(settings.get("ttl"))
        if not wipe_key and not ttl:
            return

        armed_at = settings.get("armed_at")
        whole_word = settings.get("match_words", True)

        if wipe_key:
            messages = self.fetch_page(channel_id, name, limit=TRIGGER_MESSAGE_LIMIT)
            if messages is None:
                return
            for message in messages:
                if not isinstance(message, dict):
                    continue
                message_id = message.get("id")
                if not is_message_after_guard_started(message_id, armed_at):
                    break
                if (message.get("author") or {}).get("id") != self.user_id:
                    continue
                if trigger_matches(message.get("content") or "", wipe_key, whole_word):
                    self.on_log(f"Keyword trigger detected in {name}. Purging channel history...")
                    self.on_trigger(channel_id, name)
                    if self.purge_on_trigger and not self.stopped():
                        self.purge_channel(channel_id, name, settings)
                    return

        if ttl:
            self.delete_expired(channel_id, name, ttl, armed_at)

    def fetch_page(self, channel_id, name, before=None, limit=MESSAGE_PAGE_SIZE):
        if self.stopped():
            return None
        params = {"limit": limit}
        if before:
            params["before"] = before
        try:
            res = http_client.request(
                self.session, "GET", f"{BASE_URL}/channels/{channel_id}/messages",
                headers=self.headers, params=params, timeout=TIMEOUT,
                stop_event=self.stop_event)
        except http_client.TransportError as error:
            self.on_log(f"Network error for {name}: {error}")
            return None

        if res.status_code == 401:
            self.on_log("Authentication failed: invalid token")
            self._fail_auth()
            return None
        if res.status_code == 404:
            # also count pagination 404s: a deleted channel returns 404 for
            # every page, and a TTL-only monitor never fetches page one
            self._note_missing(channel_id, name)
            return None
        if res.status_code != 200:
            return None

        self.missing_channels.pop(channel_id, None)
        try:
            messages = res.json()
        except ValueError:
            return None
        return messages if isinstance(messages, list) else None

    def _note_missing(self, channel_id, name):
        misses = self.missing_channels.get(channel_id, 0) + 1
        self.missing_channels[channel_id] = misses
        if misses < MISSING_CHANNEL_STRIKES:
            self.on_log(f"Channel {name} returned 404 ({misses}/{MISSING_CHANNEL_STRIKES})")
            return
        self.on_log(f"Channel {name} gone after {misses} checks. Removing it.")
        self.missing_channels.pop(channel_id, None)
        self.on_missing_channel(channel_id, name)

    def _fail_auth(self):
        self.stop_event.set()
        self.on_auth_failed()

    def delete_expired(self, channel_id, name, ttl, armed_at):
        start = valid_armed_at(armed_at)
        now = current_time_ms()
        if start is None or now - start < ttl * 1000:
            return 0

        cutoff = snowflake_at_timestamp_ms(now - ttl * 1000)
        if cutoff is None:
            return 0

        # Without this, every sweep (every 3s) re-reads the channel all the way
        # back to armed_at; on a busy channel armed weeks ago that is dozens of
        # requests per sweep. Messages below the floor were handled last time.
        checked = self._ttl_checked.get(channel_id)
        floor = checked[1] if checked and checked[0] == start else None

        deleted = 0
        failures = 0
        complete = False
        before = cutoff
        while not self.stopped():
            messages = self.fetch_page(channel_id, name, before)
            if messages is None:
                break             # an error: don't move the floor
            if not messages:
                complete = True
                break

            reached_floor = False
            for message in messages:
                if not isinstance(message, dict):
                    continue
                message_id = message.get("id")
                if not is_message_after_guard_started(message_id, start):
                    reached_floor = True
                    break
                try:
                    if floor is not None and int(message_id) < floor:
                        reached_floor = True
                        break
                except (TypeError, ValueError):
                    continue
                if (message.get("author") or {}).get("id") != self.user_id:
                    continue
                if not message_search.is_deletable(message):
                    continue
                if is_message_ttl_expired(message_id, ttl, start, now):
                    if self.delete_message(channel_id, message_id):
                        deleted += 1
                    elif self.last_delete_outcome == "failed":
                        failures += 1
                    self._pause()

            if reached_floor or len(messages) < MESSAGE_PAGE_SIZE:
                complete = True
                break
            before = messages[-1].get("id") if isinstance(messages[-1], dict) else None
            if not before:
                complete = True
                break

        if complete and not failures and not self.stopped():
            self._ttl_checked[channel_id] = (start, cutoff)
        return deleted

    def _search_scope(self, channel_id, settings):
        # how to search this channel, or None to read history instead
        if not self.use_search or settings is None:
            return None
        if channel_id in self._scopes:
            return self._scopes[channel_id]
        scope = None
        if settings.get("guild_id"):
            scope = {"guild_id": str(settings["guild_id"]), "channel_id": channel_id}
        elif settings.get("kind") == "dm":
            scope = {"channel_id": channel_id}
        else:
            # monitors added before guild ids were saved: ask Discord once
            try:
                res = http_client.request(
                    self.session, "GET", f"{BASE_URL}/channels/{channel_id}",
                    headers=self.headers, timeout=TIMEOUT, attempts=1,
                    stop_event=self.stop_event)
            except http_client.TransportError:
                return None           # try again next time
            if res.status_code != 200:
                return None
            try:
                info = res.json()
            except ValueError:
                return None
            if not isinstance(info, dict):
                return None
            if info.get("guild_id"):
                scope = {"guild_id": str(info["guild_id"]), "channel_id": channel_id}
            elif info.get("type") in (1, 3):
                scope = {"channel_id": channel_id}
        self._scopes[channel_id] = scope
        return scope

    def _purge_via_search(self, channel_id, name, scope):
        # (deleted, resume_before, finished); finished False means fall back
        deleted = 0
        try:
            for message in message_search.iter_own_messages(
                    self.session, self.headers, self.user_id,
                    stop_event=self.stop_event, **scope):
                if self.stopped():
                    break
                if not message_search.is_deletable(message):
                    continue
                if self.delete_message(channel_id, message["id"]):
                    deleted += 1
                self._pause()
        except message_search.AuthFailed:
            self._fail_auth()
            return deleted, None, True
        except message_search.SearchUnavailable as error:
            self.on_log(f"Search unavailable for {name} ({error}); reading history.")
            return deleted, error.resume_before, False
        return deleted, None, True

    def purge_channel(self, channel_id, name, settings=None):
        # find every message of ours (search, or a walk back through history),
        # then catch up on anything posted while that ran
        deleted = 0
        before = None
        search_done = False
        scope = self._search_scope(channel_id, settings)
        if scope is not None and not self.stopped():
            # on fallback, `before` resumes the history walk below what
            # search already handled
            found, before, search_done = self._purge_via_search(channel_id, name, scope)
            deleted += found
        while not search_done and not self.stopped():
            messages = self.fetch_page(channel_id, name, before, limit=PURGE_PAGE_LIMIT)
            if messages is None or not messages:
                break
            for message in messages:
                if self.stopped():
                    break
                if not isinstance(message, dict) or not message.get("id"):
                    continue
                if (message.get("author") or {}).get("id") != self.user_id:
                    continue
                if not message_search.is_deletable(message):
                    continue
                if self.delete_message(channel_id, message["id"]):
                    deleted += 1
                self._pause()
            before = messages[-1].get("id") if isinstance(messages[-1], dict) else None
            if not before or len(messages) < PURGE_PAGE_LIMIT:
                break

        for _ in range(CATCH_UP_PASSES):
            if self.stopped():
                break
            messages = self.fetch_page(channel_id, name, limit=PURGE_PAGE_LIMIT)
            if not messages:
                break
            pass_deleted = 0
            for message in messages:
                if self.stopped():
                    break
                if not isinstance(message, dict) or not message.get("id"):
                    continue
                if (message.get("author") or {}).get("id") != self.user_id:
                    continue
                if not message_search.is_deletable(message):
                    continue
                if self.delete_message(channel_id, message["id"]):
                    deleted += 1
                    pass_deleted += 1
                self._pause()
            if pass_deleted == 0:
                break

        self.on_log(f"Purge of {name} finished: {deleted} message(s) deleted.")
        return deleted

    def delete_message(self, channel_id, message_id):
        # True if the message is gone; last_delete_outcome says why not
        self.last_delete_outcome = "stopped"
        if self.stopped():
            return False
        try:
            res = http_client.request(
                self.session, "DELETE",
                f"{BASE_URL}/channels/{channel_id}/messages/{message_id}",
                headers=self.headers, timeout=TIMEOUT,
                attempts=2, rate_limit_attempts=5, stop_event=self.stop_event)
        except http_client.TransportError:
            self.last_delete_outcome = "stopped" if self.stopped() else "failed"
            return False
        if res.status_code == 401:
            self._fail_auth()
            self.last_delete_outcome = "failed"
            return False
        if res.status_code in (204, 404):
            self.last_delete_outcome = "deleted"
            return True
        if message_search.is_undeletable_response(res):
            # a system message or an archived thread: nothing to retry
            self.last_delete_outcome = "skipped"
            return False
        self.last_delete_outcome = "stopped" if self.stopped() else "failed"
        return False

    def _pause(self):
        # interruptible, so a stop never waits out the delay
        self.stop_event.wait(DELETE_DELAY)