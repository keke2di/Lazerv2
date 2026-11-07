# shutting down QThreads without terminate()
#
# QThread.terminate() can kill a thread while it holds the GIL or a lock,
# which deadlocks or crashes the process. Every worker here is bounded instead:
# requests have timeouts and every wait listens to a stop event. A thread that
# is still busy when its window goes away is "parked": we keep a reference so
# Python doesn't destroy the QThread while it runs (that aborts the process),
# and let it finish on its own.

import time

_parked = []


def _prune():
    # only ever called on the GUI thread; wait(0) is True once run() returned
    _parked[:] = [thread for thread in _parked if not thread.wait(0)]


def park(thread):
    _prune()
    if thread is not None and thread.isRunning() and thread not in _parked:
        _parked.append(thread)


def stop_and_park(thread, timeout_ms):
    # True if the thread is done; otherwise it is parked and still finishing
    if thread is None or not thread.isRunning():
        return True
    stop = getattr(thread, "stop", None)
    if callable(stop):
        stop()
    if thread.wait(timeout_ms):
        return True
    park(thread)
    return False


def wait_all(timeout_ms):
    # True when every parked thread finished within the timeout
    deadline = time.monotonic() + timeout_ms / 1000
    for thread in list(_parked):
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0 or not thread.wait(remaining):
            break
    _prune()
    return not _parked


def parked_count():
    _prune()
    return len(_parked)
