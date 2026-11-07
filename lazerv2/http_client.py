# one place for retries, rate limits and transport errors
#
# Every worker funnels requests through here so a 429 or a dropped Wi-Fi link
# is handled the same way in the fetcher, the purge and the monitor.

import time

import requests

DEFAULT_TIMEOUT = 10
MAX_RETRY_AFTER = 60.0
RETRY_STATUSES = (500, 502, 503, 504)

# Discord flags a bare python-requests client; a browser-shaped UA lowers the
# noise floor. This is not an authentication mechanism.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class TransportError(Exception):
    # the request never produced an HTTP response
    pass


def retry_after_seconds(response, default=1.0):
    try:
        return min(max(float(response.json().get("retry_after", default)), 0.0), MAX_RETRY_AFTER)
    except (ValueError, TypeError, AttributeError):
        return default


def request(session, method, url, *, headers=None, params=None, timeout=DEFAULT_TIMEOUT,
            attempts=3, rate_limit_attempts=5, sleep=time.sleep, user_agent=True,
            stop_event=None):
    """
    Send one request, retrying transport errors, 5xx and 429.

    Returns the final requests.Response (which may be a 4xx the caller has to
    interpret). Raises TransportError once transport retries are exhausted.

    With a stop_event, every wait between retries ends as soon as it is set,
    and no further attempt is made: a 429 or 5xx comes back as-is and a
    transport failure raises TransportError. A rate-limit wait can be a
    minute, so this is what lets Stop and Exit return promptly.
    """
    merged = dict(headers or {})
    if user_agent and "User-Agent" not in merged:
        merged["User-Agent"] = USER_AGENT

    def wait(seconds):
        # True when the caller asked us to stop
        if stop_event is not None:
            return stop_event.wait(seconds)
        sleep(seconds)
        return False

    failures = 0
    rate_limited = 0
    while True:
        try:
            response = session.request(
                method, url, headers=merged, params=params, timeout=timeout)
        except requests.RequestException as error:
            failures += 1
            if failures >= attempts or wait(min(0.5 * (2 ** (failures - 1)), 8.0)):
                raise TransportError(str(error)) from error
            continue

        if response.status_code == 429:
            rate_limited += 1
            if rate_limited >= rate_limit_attempts or wait(retry_after_seconds(response)):
                return response
            continue

        if response.status_code in RETRY_STATUSES:
            failures += 1
            if failures >= attempts or wait(min(0.5 * (2 ** (failures - 1)), 8.0)):
                return response
            continue

        return response