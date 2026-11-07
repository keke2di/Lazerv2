import threading
import time

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from lazerv2 import http_client
from lazerv2 import message_search
from lazerv2 import purge_export
from lazerv2.globals import context, BASE_URL
from lazerv2.guard_utils import current_time_ms, snowflake_at_timestamp_ms, trigger_matches

TIMEOUT = 10
DELETE_DELAY = 0.35      # seconds between deletes, to stay under Discord's limits
MAX_RATE_LIMIT_RETRIES = 5
PROGRESS_INTERVAL = 0.15
PAGE_SIZE = 100
CATCH_UP_PASSES = 2

MODE_ONLY = "Only include these words"
MODE_EXCLUDE = "Exclude these words"
ATTACHMENTS_ONLY = "Only delete attachments"

# target types from the channel picker
TARGET_DM = "dm"
TARGET_CHANNEL = "channel"
TARGET_GUILD = "guild_all"     # everything in a server, threads and forum posts too


def matches_filters(message, filters):
    # pure, so the behavior can be tested without a QThread
    content = message.get("content") or ""
    keywords = filters.get("_keywords")
    if keywords is None:
        keywords = [k.strip().lower() for k in filters.get("keywords", [])
                    if isinstance(k, str) and k.strip()]
    mode = filters.get("keyword_mode", MODE_EXCLUDE)
    # whole-word matching is what the UI defaults to; a caller that doesn't
    # say keeps the older substring behaviour
    whole_word = bool(filters.get("keyword_whole_word", False))

    if keywords:
        has_keyword = any(trigger_matches(content, keyword, whole_word) for keyword in keywords)
        if mode == MODE_ONLY and not has_keyword:
            return False
        if mode == MODE_EXCLUDE and has_keyword:
            return False

    attachments = message.get("attachments") or []
    if filters.get("attachment_filter") == ATTACHMENTS_ONLY and not attachments:
        return False

    return True


class DeletionWorker(QThread):
    # not `finished`, QThread already has a signal by that name
    run_finished = pyqtSignal(int, int, int)     # deleted, already gone, failed
    total_messages_updated = pyqtSignal(int)
    message_progress = pyqtSignal(int, int)      # done, total
    channel_progress = pyqtSignal(str)
    # the scan is done and nothing has been deleted yet; carries the count.
    # The worker waits for confirm() or cancel() before going on.
    awaiting_confirmation = pyqtSignal(int)

    def __init__(self, channels, filters=None, token=None, user_id=None,
                 dry_run=False, catch_up=False, require_confirmation=False,
                 export_dir=None, use_search=True):
        super().__init__()
        self.channels = list(channels)
        self.dry_run = dry_run
        self.catch_up = catch_up
        self.require_confirmation = require_confirmation
        # where to write a backup before deleting; None for no backup
        self.export_dir = export_dir
        self.use_search = use_search
        self.filters = dict(filters or {})
        self.filters["_keywords"] = [
            keyword.strip().lower()
            for keyword in self.filters.get("keywords", [])
            if isinstance(keyword, str) and keyword.strip()
        ]
        # snapshot identity: an account switch mid-run must not retarget the purge
        self.token = token or context.token
        self.user_id = user_id or getattr(context.user, "id", None)
        self.running = True
        self.headers = {"Authorization": self.token}
        self.all_messages = []       # (message id, channel id, channel name), delete order
        self._records = {}           # message id -> message, for the backup
        self.deleted = 0
        self.gone = 0
        self.failed = 0
        self.undeletable = 0         # system messages, archived or locked threads
        self.skipped_channels = []   # names of channels that could not be read
        self.cancelled = False       # nothing deleted: declined, or the backup failed
        self.export_paths = None     # (json, html) once a backup is written
        self._searched = []          # (channel id, name) found via search, rechecked for lag
        self._scan_started = None    # snowflake at scan start; caps the lag recheck
        self.session = requests.Session()
        # set by stop(): ends every wait (delete pacing, rate-limit backoff)
        self._stop_event = threading.Event()
        # set once the user has answered the confirmation, either way
        self._decision = threading.Event()
        self._confirmed = False
        self._waiting_for_decision = False
        self._last_status = 0.0

    # ----------------------------------------------------------- control

    def stop(self):
        # finish after the current request, without waiting out any delay
        self.running = False
        self._stop_event.set()
        self._decision.set()

    def confirm(self):
        self._confirmed = True
        self._decision.set()

    def cancel(self):
        self.cancelled = True
        self.stop()

    def is_awaiting_confirmation(self):
        return (self.require_confirmation and self.isRunning()
                and self._waiting_for_decision)

    def _pause(self, seconds):
        self._stop_event.wait(seconds)

    def _status(self, text, throttle=False):
        now = time.monotonic()
        if throttle and now - self._last_status < PROGRESS_INTERVAL:
            return
        self._last_status = now
        self.channel_progress.emit(text)

    # --------------------------------------------------------------- run

    def run(self):
        try:
            if not self.user_id or not self.token:
                self.channel_progress.emit("Not logged in.")
                return

            self._scan_started = snowflake_at_timestamp_ms(current_time_ms())
            for target in self._plan_targets():
                if not self.running:
                    break
                outcome = self.collect_target(target)
                if outcome == "auth_failed":
                    self.channel_progress.emit("Stopped: Discord rejected the token.")
                    return
                if outcome in ("error", "rate_limited"):
                    self.skipped_channels.append(self._target_name(target))

            total = len(self.all_messages)
            self.total_messages_updated.emit(total)
            if self.dry_run:
                self.channel_progress.emit(
                    f"Preview: {total} message(s) match. Nothing was deleted."
                    + self._skipped_note())
                return
            if total and self.require_confirmation:
                if not self.running or not self._await_confirmation(total):
                    self.channel_progress.emit(
                        "Cancelled: nothing was deleted." if self.cancelled
                        else "Stopped: nothing was deleted.")
                    return
            if total and self.export_dir and not self._write_backup():
                return
            if total:
                self.channel_progress.emit("Deleting messages...")

            done = 0
            last_emit = 0.0
            for msg_id, ch_id, _name in self.all_messages:
                if not self.running:
                    break
                self.delete_message(msg_id, ch_id)
                done += 1
                now = time.monotonic()
                if now - last_emit >= PROGRESS_INTERVAL or done == total:
                    self.message_progress.emit(done, total)
                    last_emit = now
                self._pause(DELETE_DELAY)

            if self.running:
                if self.catch_up:
                    # a guard wipe includes whatever was posted while it ran
                    self.catch_up_new_messages(
                        [(c["id"], c.get("name", c["id"])) for c in self.channels])
                elif self._searched:
                    # search lags a few seconds; pick up what existed at scan
                    # start but wasn't indexed yet, and nothing newer
                    self.catch_up_new_messages(self._searched, newest=self._scan_started)

            self.channel_progress.emit(self._summary())
        finally:
            self.session.close()
            self.run_finished.emit(self.deleted, self.gone, self.failed)

    def _await_confirmation(self, total):
        # True to go ahead; False if cancelled or stopped
        self._waiting_for_decision = True
        try:
            self.channel_progress.emit(
                f"Found {total} message(s). Nothing is deleted until you confirm."
                + self._skipped_note())
            self.awaiting_confirmation.emit(total)
            self._decision.wait()
        finally:
            self._waiting_for_decision = False
        return self._confirmed and self.running

    def _write_backup(self):
        # False (and nothing deleted) if the backup can't be written
        self.channel_progress.emit("Saving a backup...")
        rows = [(self._records.get(msg_id, {"id": msg_id, "channel_id": ch_id}), name)
                for msg_id, ch_id, name in self.all_messages]
        try:
            self.export_paths = purge_export.write(self.export_dir, rows, self.user_id)
        except (OSError, ValueError) as error:
            self.cancelled = True
            self.channel_progress.emit(f"Backup failed ({error}). Nothing was deleted.")
            return False
        return True

    def _summary(self):
        verb = "Done" if self.running else "Stopped"
        parts = [f"deleted {self.deleted}"]
        if self.gone:
            parts.append(f"{self.gone} already gone")
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.undeletable:
            parts.append(f"{self.undeletable} can't be deleted (system messages, "
                         "archived or locked threads)")
        text = f"{verb}: " + ", ".join(parts) + "." + self._skipped_note()
        if self.export_paths:
            text += f" Backup: {self.export_paths[1]}"
        return text

    def _skipped_note(self):
        # a channel that could not be read must not look like "nothing to delete"
        if not self.skipped_channels:
            return ""
        names = ", ".join(self.skipped_channels[:3])
        if len(self.skipped_channels) > 3:
            names += f" +{len(self.skipped_channels) - 3} more"
        return f" Could not read: {names}."

    # ------------------------------------------------------------ targets

    def _plan_targets(self):
        # a whole-server target already covers that server's channels
        covered = {str(t.get("guild_id")) for t in self.channels
                   if t.get("type") == TARGET_GUILD and t.get("guild_id")}
        return [t for t in self.channels
                if t.get("type") == TARGET_GUILD
                or str(t.get("guild_id") or "") not in covered]

    @staticmethod
    def _target_name(target):
        return target.get("name") or str(target.get("id"))

    def _search_scope(self, target):
        # kwargs for message_search, or None when this target can't be searched
        if not self.use_search:
            return None
        kind = target.get("type")
        if kind == TARGET_GUILD and target.get("guild_id"):
            return {"guild_id": target["guild_id"]}
        if kind == TARGET_CHANNEL and target.get("guild_id"):
            return {"guild_id": target["guild_id"], "channel_id": target["id"]}
        if kind == TARGET_DM:
            return {"channel_id": target["id"]}
        return None

    def collect_target(self, target):
        # "complete" | "stopped" | "auth_failed" | "rate_limited" | "error"
        name = self._target_name(target)
        scope = self._search_scope(target)
        resume_before = None

        if scope is not None:
            self.channel_progress.emit(f"Searching {name}...")
            names = {str(c.get("id")): c.get("name") for c in target.get("channels") or []}
            try:
                for message in message_search.iter_own_messages(
                        self.session, self.headers, self.user_id,
                        min_id=self.filters.get("min_snowflake"),
                        stop_event=self._stop_event,
                        on_progress=lambda found, total: self._status(
                            f"Searching {name}... {found}"
                            + (f" of ~{total}" if total else "") + " found", throttle=True),
                        **scope):
                    channel_id = str(message.get("channel_id") or target["id"])
                    channel_name = names.get(channel_id) or (
                        name if target.get("type") != TARGET_GUILD else f"{name} (thread)")
                    self._consider(message, channel_id, channel_name)
            except message_search.AuthFailed:
                return "auth_failed"
            except message_search.SearchUnavailable as error:
                if not self.running:
                    return "stopped"
                print(f"DeletionWorker: search unavailable for {name}: {error}")
                self.channel_progress.emit(f"Search unavailable for {name}; reading history...")
                resume_before = error.resume_before
            else:
                if not self.running:
                    return "stopped"
                if target.get("type") != TARGET_GUILD:
                    self._searched.append((target["id"], name))
                return "complete"

        if target.get("type") == TARGET_GUILD:
            # history fallback: the server's channels one by one (no threads)
            worst = "complete"
            for channel in target.get("channels") or []:
                if not self.running:
                    return "stopped"
                outcome = self.collect_messages(channel["id"], channel.get("name") or name)
                if outcome == "auth_failed":
                    return outcome
                if outcome in ("error", "rate_limited"):
                    worst = outcome
            return worst

        self.channel_progress.emit(f"Scanning {name}...")
        return self.collect_messages(target["id"], name, before=resume_before)

    def _consider(self, message, channel_id, name):
        # add one message to the delete list if it is ours, deletable and wanted
        if not isinstance(message, dict):
            return
        message_id = message.get("id")
        try:
            numeric_id = int(message_id)
        except (TypeError, ValueError):
            return
        if message_id in self._records:
            return
        if str((message.get("author") or {}).get("id")) != str(self.user_id):
            return
        min_id = self.filters.get("min_snowflake")
        if min_id and numeric_id < min_id:
            return
        if not matches_filters(message, self.filters):
            return
        if not message_search.is_deletable(message):
            self.undeletable += 1
            return
        self._records[message_id] = message
        self.all_messages.append((message_id, str(channel_id), name))

    # ------------------------------------------------------------ history

    def catch_up_new_messages(self, channels, newest=None):
        # the newest page of each channel, for messages a scan could not see
        for channel_id, name in channels:
            if not self.running:
                return
            self.channel_progress.emit(f"Catching up on {name}...")
            for _pass in range(CATCH_UP_PASSES):
                if not self.running:
                    return
                try:
                    res = http_client.request(
                        self.session, "GET",
                        f"{BASE_URL}/channels/{channel_id}/messages",
                        headers=self.headers, params={"limit": PAGE_SIZE},
                        timeout=TIMEOUT, attempts=2, stop_event=self._stop_event)
                except http_client.TransportError:
                    return
                if res.status_code == 401:
                    self.running = False
                    return
                if res.status_code != 200:
                    break
                try:
                    messages = res.json()
                except ValueError:
                    break
                if not isinstance(messages, list) or not messages:
                    break

                pass_deleted = 0
                for message in messages:
                    if not self.running:
                        return
                    if not isinstance(message, dict) or not message.get("id"):
                        continue
                    if (message.get("author") or {}).get("id") != self.user_id:
                        continue
                    try:
                        if newest is not None and int(message["id"]) > newest:
                            continue
                        min_id = self.filters.get("min_snowflake")
                        if min_id and int(message["id"]) < min_id:
                            continue
                    except (TypeError, ValueError):
                        continue
                    if not matches_filters(message, self.filters):
                        continue
                    if not message_search.is_deletable(message):
                        continue
                    if self.delete_message(message["id"], channel_id) == "deleted":
                        pass_deleted += 1
                    self._pause(DELETE_DELAY)
                if pass_deleted == 0:
                    break

    def collect_messages(self, channel_id, name, before=None):
        # "complete" | "stopped" | "auth_failed" | "rate_limited" | "error"
        min_id = self.filters.get("min_snowflake")

        while self.running:
            url = f"{BASE_URL}/channels/{channel_id}/messages"
            params = {"limit": PAGE_SIZE}
            if before:
                params["before"] = before

            try:
                res = http_client.request(
                    self.session, "GET", url, headers=self.headers, params=params,
                    timeout=TIMEOUT, rate_limit_attempts=MAX_RATE_LIMIT_RETRIES,
                    stop_event=self._stop_event)
            except http_client.TransportError as error:
                if not self.running:
                    return "stopped"
                print(f"DeletionWorker: cannot read {name}: {error}")
                return "error"
            if not self.running:
                return "stopped"

            if res.status_code == 401:
                return "auth_failed"
            if res.status_code == 429:
                print(f"DeletionWorker: giving up on {name} after repeated rate limits")
                return "rate_limited"
            if res.status_code != 200:
                print(f"DeletionWorker: cannot read {name} (HTTP {res.status_code})")
                return "error"

            try:
                messages = res.json()
            except ValueError:
                return "error"
            if not isinstance(messages, list) or not messages:
                break

            for message in messages:
                if not isinstance(message, dict):
                    continue
                try:
                    message_id = int(message["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                if min_id and message_id < min_id:
                    return "complete"   # newest first, so everything after is older
                self._consider(message, channel_id, name)

            last = messages[-1]
            before = last.get("id") if isinstance(last, dict) else None
            if not before or len(messages) < PAGE_SIZE:
                break

        return "complete" if self.running else "stopped"

    def delete_message(self, msg_id, ch_id):
        # "deleted" | "gone" | "skipped" | "failed" | "stopped"
        try:
            res = http_client.request(
                self.session, "DELETE",
                f"{BASE_URL}/channels/{ch_id}/messages/{msg_id}",
                headers=self.headers, timeout=TIMEOUT,
                attempts=2, rate_limit_attempts=MAX_RATE_LIMIT_RETRIES,
                stop_event=self._stop_event)
        except http_client.TransportError as error:
            if self._stop_event.is_set():
                return "stopped"   # cut short by Stop, not a failure
            print(f"DeletionWorker.delete_message error: {error}")
            self.failed += 1
            return "failed"

        if res.status_code == 204:
            self.deleted += 1
            return "deleted"
        if res.status_code == 404:
            self.gone += 1
            return "gone"
        if message_search.is_undeletable_response(res):
            # a system message, or an archived / locked thread: never deletable
            self.undeletable += 1
            return "skipped"
        if res.status_code == 401:
            self.running = False
        elif self._stop_event.is_set() and res.status_code in (429,) + http_client.RETRY_STATUSES:
            return "stopped"   # a retry wait was interrupted, not a failure

        print(f"DeletionWorker: could not delete {msg_id} (HTTP {res.status_code})")
        self.failed += 1
        return "failed"
