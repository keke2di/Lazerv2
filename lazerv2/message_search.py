# find your own messages with Discord's search instead of reading everything
#
# Walking /messages reads every message in a channel, everyone's, 100 at a
# time. Search returns only messages you wrote, so a big purge in a busy
# channel takes a fraction of the requests. It also reaches threads and forum
# posts when a whole server is searched.
#
# Search is the endpoint the Discord client uses, not a documented bot API.
# It can answer 202 "index not ready", it can lag a few seconds behind new
# messages, and it can be unavailable. Every caller keeps the plain history
# walk as a fallback: SearchUnavailable says where to resume it.
#
# No Qt here; the purge window and the guard daemon both use it.

import time

from lazerv2 import http_client
BASE_URL = "https://discord.com/api/v9"
TIMEOUT = 15
MAX_INDEX_WAITS = 6          # 202 "still indexing" answers before giving up
MAX_INDEX_WAIT_S = 10.0

# message types Discord never lets anyone delete (call notices, "X added Y",
# channel renames, the starter message of a thread). Skip instead of failing.
UNDELETABLE_TYPES = frozenset({1, 2, 3, 4, 5, 21})

# JSON error codes for a delete that can never succeed, so it is skipped
# rather than counted as a failure
UNDELETABLE_ERROR_CODES = frozenset({
    50021,    # cannot execute action on a system message
    50083,    # thread is archived
    160005,   # thread is locked
})


class SearchUnavailable(Exception):
    # search could not be used; `resume_before` is the oldest message id
    # already returned (None if nothing was), for the history fallback
    def __init__(self, reason, resume_before=None):
        super().__init__(reason)
        self.resume_before = resume_before


class AuthFailed(Exception):
    pass


def is_deletable(message):
    try:
        return int(message.get("type") or 0) not in UNDELETABLE_TYPES
    except (TypeError, ValueError):
        return True


def is_undeletable_response(response):
    # a 400/403 that says "this can never be deleted"
    if response is None or response.status_code not in (400, 403):
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("code") in UNDELETABLE_ERROR_CODES


def search_url(channel_id=None, guild_id=None):
    # a guild channel (or a whole guild) searches the guild; a DM searches itself
    if guild_id:
        return f"{BASE_URL}/guilds/{guild_id}/messages/search"
    return f"{BASE_URL}/channels/{channel_id}/messages/search"


def _hits(payload):
    # "messages" is a list of groups; each group holds the hit, possibly with
    # context messages around it that are flagged hit=false or unflagged
    groups = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        return None
    hits = []
    for group in groups:
        if isinstance(group, dict):
            group = [group]
        if not isinstance(group, list):
            continue
        flagged = [m for m in group if isinstance(m, dict) and m.get("hit") is True]
        candidates = flagged or [m for m in group[:1] if isinstance(m, dict)]
        hits.extend(candidates)
    return hits


def iter_own_messages(session, headers, user_id, *, channel_id=None, guild_id=None,
                      min_id=None, stop_event=None, on_progress=None):
    """
    Yield the user's messages newest first, as message dicts.

    Scope: guild_id + channel_id is one guild channel, guild_id alone is the
    whole guild (threads and forum posts included), channel_id alone is a DM.
    Raises SearchUnavailable (with a resume point) or AuthFailed.
    """
    if not user_id or not (channel_id or guild_id):
        raise SearchUnavailable("nothing to search")

    url = search_url(channel_id, guild_id)
    base_params = {
        "author_id": str(user_id),
        "include_nsfw": "true",
        "sort_by": "timestamp",
        "sort_order": "desc",
    }
    if guild_id and channel_id:
        base_params["channel_id"] = str(channel_id)
    if min_id:
        base_params["min_id"] = str(min_id)

    seen = set()
    oldest = None
    found = 0
    index_waits = 0

    while not (stop_event is not None and stop_event.is_set()):
        params = dict(base_params)
        if oldest:
            # page by id, not offset: offset is capped at a few thousand
            params["max_id"] = str(oldest)
        try:
            res = http_client.request(
                session, "GET", url, headers=headers, params=params,
                timeout=TIMEOUT, attempts=2, stop_event=stop_event)
        except http_client.TransportError as error:
            raise SearchUnavailable(f"network: {error}", oldest) from error

        if res.status_code == 401:
            raise AuthFailed()
        if res.status_code == 202:
            # the index for this channel is still being built
            index_waits += 1
            if index_waits > MAX_INDEX_WAITS:
                raise SearchUnavailable("search index not ready", oldest)
            wait = min(http_client.retry_after_seconds(res, default=2.0), MAX_INDEX_WAIT_S)
            if stop_event is not None:
                if stop_event.wait(wait):
                    return
            else:
                time.sleep(wait)
            continue
        if res.status_code != 200:
            raise SearchUnavailable(f"HTTP {res.status_code}", oldest)

        try:
            payload = res.json()
        except ValueError as error:
            raise SearchUnavailable("malformed search response", oldest) from error
        hits = _hits(payload)
        if hits is None:
            raise SearchUnavailable("unexpected search response", oldest)

        new = 0
        for message in hits:
            message_id = message.get("id")
            if not message_id or message_id in seen:
                continue
            seen.add(message_id)
            new += 1
            try:
                if oldest is None or int(message_id) < int(oldest):
                    oldest = message_id
            except (TypeError, ValueError):
                continue
            # never trust the filter with someone else's message
            if str((message.get("author") or {}).get("id")) != str(user_id):
                continue
            found += 1
            yield message

        if on_progress is not None:
            total = payload.get("total_results") if isinstance(payload, dict) else None
            on_progress(found, total if isinstance(total, int) else None)

        if new == 0:
            return
