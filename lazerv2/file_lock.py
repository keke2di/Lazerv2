# short cross-process lock around watchlist read-modify-write
#
# The lock file is separate from the data file: the writer replaces the data
# file, and replacing a locked file is a bad idea on Windows.

import os
import sys
import time

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class LockTimeout(TimeoutError):
    pass


class FileLock:
    def __init__(self, path, timeout=2.0, poll=0.02):
        self.path = path
        self.timeout = timeout
        self.poll = poll
        self._fd = None

    def acquire(self):
        deadline = time.monotonic() + self.timeout
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            # Windows byte-range locking needs a byte to lock
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
        except OSError:
            pass

        while True:
            try:
                self._lock(fd)
                self._fd = fd
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise LockTimeout(f"could not lock {self.path}") from None
                time.sleep(self.poll)

    @staticmethod
    def _lock(fd):
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(fd):
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)

    def release(self):
        if self._fd is None:
            return
        try:
            self._unlock(self._fd)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False