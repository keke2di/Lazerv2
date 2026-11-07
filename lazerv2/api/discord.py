from types import SimpleNamespace

import requests

from lazerv2 import app_config
from lazerv2.globals import context, BASE_URL
from lazerv2.session_store import normalize_token

__all__ = ["normalize_token", "validate", "apply_login", "login"]

TIMEOUT = 10


def validate(token):
    # (user_data, error). No side effects, so it is safe off the UI thread.
    token = normalize_token(token)
    if not token:
        return None, "No token provided."

    try:
        response = requests.get(
            f"{BASE_URL}/users/@me",
            headers={"Authorization": token},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        return None, f"Could not reach Discord: {e}"

    if response.status_code == 200:
        try:
            user_data = response.json()
        except ValueError:
            return None, "Discord returned a malformed response."
        if not isinstance(user_data, dict):
            return None, "Discord returned a malformed response."
        return user_data, None

    if response.status_code == 401:
        return None, "Invalid or expired token."
    if response.status_code == 429:
        return None, "Rate limited by Discord. Try again in a moment."
    return None, f"Discord rejected the request (HTTP {response.status_code})."


def apply_login(token, user_data):
    # make a validated account the signed-in one; GUI thread only
    context.token = normalize_token(token)
    context.user = SimpleNamespace(**user_data)
    # the daemon follows the account that was used last
    if user_data.get("id"):
        app_config.set_value("last_account", user_data["id"])


def login(token):
    # validate and apply in one go (blocking; the windows use TokenCheck)
    user_data, error = validate(token)
    if user_data:
        apply_login(token, user_data)
    return user_data, error
