# the Conversation Guard watchlist, scoped per Discord account
#
# v3 layout:
#   {"__metadata__": {"version": 3, "account_id": "123"},
#    "accounts": {
#        "123": {"active": true, "channels": {"<channel id>": {...}}},
#        "456": {"active": false, "channels": {}}}}
#
# `active` is per account: arming account A does not arm account B. Older
# files migrate automatically - a v1 flat file under the first account that
# loads it, a v2 file under the account it was pointing at.

import os
import time

from lazerv2 import fileio
from lazerv2 import app_paths
from lazerv2.file_lock import FileLock, LockTimeout
from lazerv2.guard_utils import ensure_watchlist_armed_at, normalize_ttl, valid_armed_at

VERSION = 3
METADATA_KEY = "__metadata__"


class WatchlistError(Exception):
    pass


class WatchlistCorrupt(WatchlistError):
    # the file was read and is not a valid watchlist; safe to quarantine
    pass


def path():
    return app_paths.path("watchlist.json")


def _lock_path():
    return path() + ".lock"


def _key(account_id):
    return str(account_id) if account_id else None


def _sanitize_entry(value):
    if not isinstance(value, dict):
        return None
    entry = dict(value)
    entry["wipe_key"] = str(entry.get("wipe_key") or "")
    entry["name"] = str(entry.get("name") or "")
    entry["ttl"] = normalize_ttl(entry.get("ttl"))
    entry["match_words"] = bool(entry.get("match_words", True))
    if "armed_at" in entry:
        armed_at = valid_armed_at(entry.get("armed_at"))
        if armed_at is None:
            entry.pop("armed_at", None)
        else:
            entry["armed_at"] = armed_at
    return entry


def _sanitize_entries(raw):
    entries = {}
    if not isinstance(raw, dict):
        return entries
    for channel_id, value in raw.items():
        entry = _sanitize_entry(value)
        if entry is not None and str(channel_id):
            entries[str(channel_id)] = entry
    return entries


def _account_record(active=False, channels=None):
    return {"active": bool(active), "channels": channels or {}}


def _read_raw():
    state, data = fileio.read_json_checked(path())
    if state == "missing":
        return None
    if state == "corrupt":
        raise WatchlistCorrupt(f"{path()} is not valid JSON")
    if state == "unreadable":
        # transient; never quarantine a file we could not even read
        raise WatchlistError(f"{path()} could not be read right now")
    return data


def _normalize(raw, default_account):
    # (document, was_migrated)
    if raw is None:
        return {
            "version": VERSION,
            "active": False,
            "account_id": default_account,
            "accounts": {},
        }, False

    if not isinstance(raw, dict):
        raise WatchlistCorrupt("watchlist root is not an object")

    metadata = raw.get(METADATA_KEY)
    metadata = metadata if isinstance(metadata, dict) else {}
    active = bool(metadata.get("active", False))
    account_id = _key(metadata.get("account_id")) or default_account
    try:
        version = int(metadata.get("version") or 0)
    except (TypeError, ValueError):
        version = 0

    accounts_raw = raw.get("accounts")
    if not isinstance(accounts_raw, dict):
        # v1: channels live at the top level
        legacy_entries = {k: v for k, v in raw.items() if k != METADATA_KEY}
        accounts = {}
        if account_id:
            accounts[account_id] = _account_record(active, _sanitize_entries(legacy_entries))
        return {
            "version": VERSION,
            "active": active,
            "account_id": account_id,
            "accounts": accounts,
        }, True

    accounts = {}
    if version >= 3:
        for key, value in accounts_raw.items():
            account = _key(key)
            if not account:
                continue
            if isinstance(value, dict) and isinstance(value.get("channels"), dict):
                accounts[account] = _account_record(
                    value.get("active", False), _sanitize_entries(value.get("channels")))
            else:
                # tolerate a hand-edited v3 record
                accounts[account] = _account_record(False, _sanitize_entries(value))
    else:
        # v2: channels directly, and one global active switch
        for key, value in accounts_raw.items():
            account = _key(key)
            if not account:
                continue
            accounts[account] = _account_record(
                active and account == account_id, _sanitize_entries(value))

    return {
        "version": VERSION,
        "active": active,
        "account_id": account_id,
        "accounts": accounts,
    }, version < VERSION


def _entries(document, account):
    record = document["accounts"].get(account)
    if not isinstance(record, dict):
        return {}
    return record.get("channels", {})


def _active(document, account):
    record = document["accounts"].get(account)
    return bool(record.get("active", False)) if isinstance(record, dict) else False


def _serialize(document):
    accounts = {}
    for account, record in document.get("accounts", {}).items():
        accounts[account] = _account_record(
            record.get("active", False), record.get("channels", {}))
    return {
        METADATA_KEY: {
            "version": VERSION,
            "account_id": document.get("account_id"),
        },
        "accounts": accounts,
    }


def _write(document):
    try:
        fileio.atomic_write_json(path(), _serialize(document))
    except OSError as error:
        raise WatchlistError(f"cannot write watchlist: {error}") from error


def _quarantine():
    target = path()
    backup = f"{target}.corrupt-{int(time.time())}"
    try:
        os.replace(target, backup)
    except OSError:
        return None
    return backup


def _read(default_account):
    # (document, was_migrated, was_missing)
    raw = _read_raw()
    document, migrated = _normalize(raw, default_account)
    return document, migrated, raw is None


def _update(account_id, change):
    # a mutation runs under the cross-process lock against a fresh read
    with FileLock(_lock_path()):
        try:
            document, migrated, missing = _read(account_id)
        except WatchlistCorrupt:
            _quarantine()
            document, migrated, missing = _read(account_id)
        dirty = change(document) or migrated or missing
        if dirty:
            _write(document)
        return document


def load_entries(account_id):
    # (entries, active, error)
    account = _key(account_id)
    try:
        if account is None:
            document, _, _ = _read(None)
            return {}, False, None

        document, migrated, missing = _read(account)
        entries = dict(_entries(document, account))
        needs_arm = ensure_watchlist_armed_at(entries)
        if not needs_arm and not migrated and not missing:
            return entries, _active(document, account), None

        def change(fresh):
            record = fresh["accounts"].setdefault(account, _account_record())
            target = dict(record.get("channels", {}))
            ensure_watchlist_armed_at(target)
            record["channels"] = target
            if not fresh.get("account_id"):
                fresh["account_id"] = account
            return True

        document = _update(account, change)
        return dict(_entries(document, account)), _active(document, account), None
    except (WatchlistError, LockTimeout) as error:
        return {}, False, str(error)


def save_entries(account_id, entries, active=None):
    # (ok, error)
    account = _key(account_id)
    if account is None:
        return False, "no account bound"

    clean = _sanitize_entries(entries)
    ensure_watchlist_armed_at(clean)

    def change(document):
        record = document["accounts"].setdefault(account, _account_record())
        record["channels"] = clean
        if active is not None:
            record["active"] = bool(active)
        document["account_id"] = account
        return True

    try:
        _update(account, change)
        return True, None
    except (WatchlistError, LockTimeout) as error:
        return False, str(error)


def mutate_entries(account_id, change, active=None):
    # (ok, error, entries); the callback edits a fresh copy under the lock
    account = _key(account_id)
    if account is None:
        return False, "no account bound", {}

    def apply(document):
        record = document["accounts"].setdefault(account, _account_record())
        entries = dict(record.get("channels", {}))
        change(entries)
        clean = _sanitize_entries(entries)
        ensure_watchlist_armed_at(clean)
        record["channels"] = clean
        if active is not None:
            record["active"] = bool(active)
        document["account_id"] = account
        return True

    try:
        document = _update(account, apply)
        return True, None, dict(_entries(document, account))
    except (WatchlistError, LockTimeout) as error:
        return False, str(error), {}


def set_active(account_id, active):
    account = _key(account_id)
    if account is None:
        return False, "no account bound"

    def change(document):
        record = document["accounts"].setdefault(account, _account_record())
        record["active"] = bool(active)
        if not document.get("account_id"):
            document["account_id"] = account
        return True

    try:
        _update(account, change)
        return True, None
    except (WatchlistError, LockTimeout) as error:
        return False, str(error)


def remove_channel(account_id, channel_id):
    account = _key(account_id)
    if account is None:
        return False, "no account bound"
    channel = str(channel_id)

    def change(document):
        record = document["accounts"].get(account)
        if not isinstance(record, dict):
            return False
        entries = record.get("channels", {})
        if channel not in entries:
            return False
        entries.pop(channel, None)
        return True

    try:
        _update(account, change)
        return True, None
    except (WatchlistError, LockTimeout) as error:
        return False, str(error)


def set_active_account(account_id):
    account = _key(account_id)
    if account is None:
        return False, "no account id"

    def change(document):
        document["account_id"] = account
        document["accounts"].setdefault(account, _account_record())
        return True

    try:
        _update(account, change)
        return True, None
    except (WatchlistError, LockTimeout) as error:
        return False, str(error)


def active_account_id():
    try:
        document, _, _ = _read(None)
        return document.get("account_id")
    except (WatchlistError, LockTimeout):
        return None


def is_active(account_id=None):
    account = _key(account_id)
    try:
        document, _, _ = _read(account)
        account = account or document.get("account_id")
        return _active(document, account) if account else False
    except (WatchlistError, LockTimeout):
        return False


def snapshot(account_id=None):
    # (account_id, active, entries, error) for the daemon's polling loop
    account = _key(account_id)
    try:
        document, _, _ = _read(account)
        resolved = account or document.get("account_id")
        if not resolved:
            return None, False, {}, None
        return resolved, _active(document, resolved), dict(_entries(document, resolved)), None
    except (WatchlistError, LockTimeout) as error:
        return account, False, {}, str(error)