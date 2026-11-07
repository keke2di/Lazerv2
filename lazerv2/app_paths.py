# where the app keeps its state
#
# The code stays in the checkout; tokens, the watchlist, config and caches go
# to the per-user profile directory, with a one-time migration of anything an
# older install left beside the source.

import os
import shutil
import sys
import time

APP_NAME = "LazerV2"

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
# the checkout the package lives in; older installs kept their state here
_SOURCE_DIR = os.path.dirname(_PACKAGE_DIR)
_MARKER = ".migrated-v2"

# files and folders that used to live next to the source
_LEGACY_FILES = (
    "session.lzr",
    "session.key",
    "watchlist.json",
    "config.json",
    "daemon.heartbeat",
    "daemon_error.log",
)
_LEGACY_DIRS = ("avatar_cache", "image_cache")

_data_dir = None


def project_root():
    return _SOURCE_DIR


def _profile_dir():
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or os.getenv("LOCALAPPDATA")
        if base:
            return os.path.join(base, APP_NAME)
        return os.path.join(os.path.expanduser("~"), ".lazer-v2")
    base = os.getenv("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, APP_NAME)


def _write_marker(target):
    try:
        with open(os.path.join(target, _MARKER), "w", encoding="utf-8") as handle:
            handle.write(f"migrated {time.time():.0f}\n")
    except OSError:
        pass


def _copy_file(source, target):
    temporary = f"{target}.migrating"
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    except OSError:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _migrate(target):
    # already moved once, or the profile already has state
    if os.path.exists(os.path.join(target, _MARKER)):
        return
    if any(os.path.exists(os.path.join(target, name)) for name in _LEGACY_FILES):
        _write_marker(target)
        return

    migrated = []
    for name in _LEGACY_FILES:
        source = os.path.join(_SOURCE_DIR, name)
        if not os.path.exists(source):
            continue
        _copy_file(source, os.path.join(target, name))
        migrated.append(source)
    for name in _LEGACY_DIRS:
        source = os.path.join(_SOURCE_DIR, name)
        if not os.path.isdir(source):
            continue
        shutil.copytree(source, os.path.join(target, name), dirs_exist_ok=True)
        migrated.append(source)

    _write_marker(target)

    # the originals held live tokens, so leave only the profile copy behind
    for path in migrated:
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError:
            pass


def data_dir():
    global _data_dir
    if _data_dir is not None:
        return _data_dir

    override = os.getenv("LAZER_DATA_DIR")
    if override:
        path = os.path.abspath(os.path.expanduser(override))
        os.makedirs(path, exist_ok=True)
        _data_dir = path
        return path

    target = _profile_dir()
    try:
        os.makedirs(target, exist_ok=True)
        _migrate(target)
        _data_dir = target
    except OSError as error:
        # a read-only profile should not stop the app from starting
        print(f"[paths] cannot use {target}: {error}; staying next to the source")
        _data_dir = _SOURCE_DIR
    return _data_dir


def path(name):
    return os.path.join(data_dir(), name)


def ensure_dir(name):
    directory = path(name)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return None
    return directory


def reset():
    # tests point LAZER_DATA_DIR at a scratch folder and call this
    global _data_dir
    _data_dir = None