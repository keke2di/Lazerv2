# saved account tokens, encrypted on disk

import hashlib
import json
import os
import secrets
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from lazerv2 import app_paths
from lazerv2 import fileio
from lazerv2 import secret_store
SESSION_PATH = app_paths.path("session.lzr")
KEY_PATH = app_paths.path("session.key")

_QUOTES = "\"'`"

_MAGIC = b"LZR2"
_NONCE_LEN = 12
_KEY_LEN = 32


def normalize_token(token):
    # pasted tokens arrive with stray whitespace, and a copy from the DevTools
    # console comes wrapped in quotes; neither is part of the token
    token = (token or "").strip()
    while len(token) >= 2 and token[0] == token[-1] and token[0] in _QUOTES:
        token = token[1:-1].strip()
    return token


def _read_key():
    try:
        with open(KEY_PATH, "rb") as handle:
            data = handle.read()
    except OSError:
        return None

    # binary formats are read as-is: a DPAPI blob or raw key can legitimately
    # end in a byte that .strip() treats as whitespace
    if data.startswith(secret_store.MAGIC):
        key = secret_store.unprotect(data)
        return key if key and len(key) == _KEY_LEN else None
    text = data.strip()
    if len(text) == _KEY_LEN * 2:
        try:
            key = bytes.fromhex(text.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return None
        return key if len(key) == _KEY_LEN else None
    if len(data) == _KEY_LEN:
        return data
    return None


def _write_key(key):
    sealed = secret_store.protect(key)
    payload = sealed if sealed else key.hex().encode("ascii")
    fileio.atomic_write_bytes(KEY_PATH, payload)
    try:
        os.chmod(KEY_PATH, 0o600)  # best effort, does nothing for Windows ACLs
    except OSError:
        pass


def _key_is_sealed():
    try:
        with open(KEY_PATH, "rb") as handle:
            return handle.read(len(secret_store.MAGIC)) == secret_store.MAGIC
    except OSError:
        return False


def _quarantine_session(reason):
    if not os.path.exists(SESSION_PATH):
        return None
    backup = f"{SESSION_PATH}.broken-{int(time.time())}"
    try:
        os.replace(SESSION_PATH, backup)
    except OSError:
        return None
    print(f"[session] {reason}; the old file was kept at {backup}")
    return backup


def _load_or_create_key():
    key = _read_key()
    if key is not None:
        if not _key_is_sealed():
            # upgrade a legacy raw key in place, the ciphertext stays valid
            try:
                _write_key(key)
            except OSError:
                pass
        return key

    # Never rotate over an existing session: that would make the saved
    # accounts unrecoverable without saying so.
    if os.path.exists(SESSION_PATH):
        _quarantine_session("session key is missing or unreadable")

    key = secrets.token_bytes(_KEY_LEN)
    _write_key(key)
    return key


def _atomic_write(path, payload):
    fileio.atomic_write_bytes(path, payload)


def load_accounts():
    # {} if there isn't one
    try:
        with open(SESSION_PATH, "rb") as handle:
            data = handle.read()
    except OSError:
        return {}

    if not data.startswith(_MAGIC):
        return {}

    nonce = data[len(_MAGIC):len(_MAGIC) + _NONCE_LEN]
    ciphertext = data[len(_MAGIC) + _NONCE_LEN:]
    if len(nonce) != _NONCE_LEN or not ciphertext:
        return {}

    try:
        key = _load_or_create_key()
    except OSError:
        return {}

    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    except (InvalidTag, ValueError):
        return {}

    try:
        accounts = json.loads(plaintext.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(accounts, dict):
        return {}
    # accounts saved before tokens were cleaned on login: a leading space is
    # an invalid header, so the daemon could never use them
    cleaned = {}
    for token, data in accounts.items():
        token = normalize_token(token)
        if token and isinstance(data, dict):
            cleaned[token] = data
    return cleaned


def avatar_cache_dir():
    return app_paths.ensure_dir("avatar_cache")


def _image_cache_dir():
    return app_paths.ensure_dir("image_cache")


def _media_name(key):
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:40] + ".img"


def _avatar_name(user_id, avatar_hash=None):
    if avatar_hash:
        return f"{user_id}-{avatar_hash}.img"
    return f"{user_id}.img"


def cache_avatar(user_id, data, avatar_hash=None):
    # so the account menu opens without hitting the network
    directory = avatar_cache_dir()
    if not directory or not user_id or not data:
        return False
    name = _avatar_name(user_id, avatar_hash)
    try:
        fileio.atomic_write_bytes(os.path.join(directory, name), data)
        # a changed avatar leaves the previous copy behind otherwise
        for entry in os.listdir(directory):
            if entry == name:
                continue
            if entry == f"{user_id}.img" or entry.startswith(f"{user_id}-"):
                try:
                    os.remove(os.path.join(directory, entry))
                except OSError:
                    pass
    except OSError:
        return False
    return True


def get_cached_avatar(user_id, avatar_hash=None):
    directory = avatar_cache_dir()
    if not directory or not user_id:
        return None
    # with a hash the match must be exact, or a stale avatar comes back
    name = _avatar_name(user_id, avatar_hash)
    try:
        with open(os.path.join(directory, name), "rb") as handle:
            return handle.read()
    except OSError:
        return None


def cache_image(key, data):
    # guild and DM icons, keyed by URL
    directory = _image_cache_dir()
    if not directory or not key or not data:
        return False
    try:
        fileio.atomic_write_bytes(os.path.join(directory, _media_name(key)), data)
    except OSError:
        return False
    return True


def get_cached_image(key):
    directory = _image_cache_dir()
    if not directory or not key:
        return None
    try:
        with open(os.path.join(directory, _media_name(key)), "rb") as handle:
            return handle.read()
    except OSError:
        return None


def save_accounts(accounts):
    try:
        key = _load_or_create_key()
        nonce = secrets.token_bytes(_NONCE_LEN)
        ciphertext = AESGCM(key).encrypt(nonce, json.dumps(accounts).encode(), None)
        _atomic_write(SESSION_PATH, _MAGIC + nonce + ciphertext)
    except OSError:
        return False
    try:
        os.chmod(SESSION_PATH, 0o600)
    except OSError:
        pass
    return True