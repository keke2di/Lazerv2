# atomic file writes

import json
import os
import tempfile
import time

_REPLACE_ATTEMPTS = 6
_REPLACE_DELAY = 0.05


def _replace(temporary, path):
    # Windows refuses to replace a file another process holds open; the reader
    # only keeps it open for a few milliseconds, so a short retry is enough.
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY * (attempt + 1))


def atomic_write_bytes(path, payload):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    # a unique temp name, or two processes writing the same file shred each other
    handle_fd, temporary = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def atomic_write_text(path, text, encoding="utf-8"):
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path, obj, indent=2):
    atomic_write_text(path, json.dumps(obj, indent=indent))


def read_json_checked(path):
    # ("missing" | "ok" | "corrupt" | "unreadable", data)
    #
    # "unreadable" is a transient I/O failure (a sharing violation while
    # another process replaces the file, an antivirus scan). It says nothing
    # about the contents, so callers must not treat it as corruption.
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return "ok", json.load(handle)
    except FileNotFoundError:
        return "missing", None
    except ValueError:
        return "corrupt", None
    except OSError:
        return "unreadable", None


def read_json(path, default=None):
    # returns default if the file is missing or corrupt
    state, data = read_json_checked(path)
    return data if state == "ok" else default