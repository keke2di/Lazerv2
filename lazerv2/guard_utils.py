import re
import time
from functools import lru_cache


DISCORD_EPOCH_MS = 1420070400000
ARMED_AT_KEY = "armed_at"


def current_time_ms():
    return int(time.time() * 1000)


def normalize_ttl(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def valid_armed_at(value):
    try:
        armed_at = int(value)
    except (TypeError, ValueError):
        return None
    return armed_at if armed_at >= DISCORD_EPOCH_MS else None


def ensure_watchlist_armed_at(watchlist, armed_at=None):
    if not isinstance(watchlist, dict):
        return False

    armed_at = current_time_ms() if armed_at is None else int(armed_at)
    changed = False
    for settings in watchlist.values():
        if not isinstance(settings, dict):
            continue
        value = valid_armed_at(settings.get(ARMED_AT_KEY))
        if value is None:
            settings[ARMED_AT_KEY] = armed_at
            changed = True
        elif settings.get(ARMED_AT_KEY) != value:
            settings[ARMED_AT_KEY] = value
            changed = True
    return changed


def message_timestamp_ms(message_id):
    try:
        return (int(message_id) >> 22) + DISCORD_EPOCH_MS
    except (TypeError, ValueError):
        return None


def snowflake_at_timestamp_ms(timestamp_ms):
    try:
        timestamp_ms = int(timestamp_ms)
    except (TypeError, ValueError):
        return None
    if timestamp_ms < DISCORD_EPOCH_MS:
        return None
    return (timestamp_ms - DISCORD_EPOCH_MS) << 22


def is_message_after_guard_started(message_id, armed_at):
    timestamp = message_timestamp_ms(message_id)
    start = valid_armed_at(armed_at)
    return timestamp is not None and start is not None and timestamp >= start


def is_message_ttl_expired(message_id, ttl, armed_at, now=None):
    ttl = normalize_ttl(ttl)
    timestamp = message_timestamp_ms(message_id)
    start = valid_armed_at(armed_at)
    if not ttl or timestamp is None or start is None or timestamp < start:
        return False
    now = current_time_ms() if now is None else int(now)
    return now - timestamp >= ttl * 1000


@lru_cache(maxsize=256)
def _whole_word_pattern(key):
    # not \b: a trigger may start or end with punctuation
    return re.compile(
        r"(?<![A-Za-z0-9_])" + re.escape(key) + r"(?![A-Za-z0-9_])",
        re.IGNORECASE)


def trigger_matches(content, key, whole_word=True):
    key = (key or "").strip()
    if not key:
        return False
    content = content or ""
    if not whole_word:
        return key.lower() in content.lower()
    return _whole_word_pattern(key).search(content) is not None
