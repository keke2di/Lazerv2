# settings in config.json, plus the daemon heartbeat

import os
import time

from lazerv2 import app_paths
from lazerv2 import fileio
CONFIG_PATH = app_paths.path("config.json")
HEARTBEAT_PATH = app_paths.path("daemon.heartbeat")

# anything older than this means the daemon is gone
HEARTBEAT_TIMEOUT = 10

DEFAULTS = {
    "theme": "dark",
    "sidebar_pinned": False,
    "last_account": None,    # account id the daemon should resume
    "last_purge": None,      # {"count": int, "channel": str, "timestamp": float}
    "backup_before_purge": True,   # write a backup before a manual purge deletes
}


def load():
    data = fileio.read_json(CONFIG_PATH, default=None)
    config = dict(DEFAULTS)
    if isinstance(data, dict):
        config.update({k: v for k, v in data.items() if k in DEFAULTS})
    if config["theme"] not in ("dark", "light"):
        config["theme"] = DEFAULTS["theme"]
    return config


def save(config):
    try:
        fileio.atomic_write_json(CONFIG_PATH, {k: config.get(k, v) for k, v in DEFAULTS.items()})
    except (OSError, TypeError) as error:
        print(f"[config] save failed: {error}")
        return False
    return True


def get(key):
    return load().get(key, DEFAULTS.get(key))


def set_value(key, value):
    if key not in DEFAULTS:
        raise KeyError(f"unknown config key: {key}")
    config = load()
    config[key] = value
    return save(config)


def record_purge(count, channel):
    return set_value("last_purge", {
        "count": count,
        "channel": channel,
        "timestamp": time.time(),
    })


def _heartbeat_path(path=None):
    # resolved at call time, not as a default argument
    return path if path is not None else HEARTBEAT_PATH


def write_heartbeat(path=None):
    try:
        fileio.atomic_write_text(_heartbeat_path(path), str(time.time()))
    except OSError:
        pass


def clear_heartbeat(path=None):
    try:
        os.remove(_heartbeat_path(path))
    except OSError:
        pass


def daemon_heartbeat_age(path=None):
    try:
        with open(_heartbeat_path(path), "r", encoding="utf-8") as handle:
            return max(0.0, time.time() - float(handle.read().strip()))
    except (OSError, ValueError):
        return None


def daemon_running(path=None):
    age = daemon_heartbeat_age(path)
    return age is not None and age < HEARTBEAT_TIMEOUT