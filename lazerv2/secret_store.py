# seals the session key with Windows DPAPI when it is available
#
# DPAPI ties the blob to the current Windows user, so copying session.key to
# another machine or another account no longer helps. Other platforms fall
# back to the file-permission protection the app already had.

import ctypes
import sys

MAGIC = b"LZR2K1"

_ENTROPY = b"lazer-v2/session-key/v1"
_UI_FORBIDDEN = 0x1

if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]
else:
    _Blob = None


def available():
    if sys.platform != "win32" or _Blob is None:
        return False
    try:
        _ = ctypes.windll.crypt32
        return True
    except (AttributeError, OSError):
        return False


def _blob(data):
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _run(function, data):
    if not available():
        return None
    source, _keep_source = _blob(data)
    entropy, _keep_entropy = _blob(_ENTROPY)
    output = _Blob()
    try:
        ok = function(
            ctypes.byref(source), None, ctypes.byref(entropy), None, None,
            _UI_FORBIDDEN, ctypes.byref(output))
    except (AttributeError, OSError, ctypes.ArgumentError):
        return None
    if not ok:
        return None
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        try:
            ctypes.windll.kernel32.LocalFree(output.pbData)
        except (AttributeError, OSError):
            pass


def protect(data):
    # returns MAGIC + DPAPI blob, or None when DPAPI is unavailable
    if not data or not available():
        return None
    blob = _run(ctypes.windll.crypt32.CryptProtectData, data)
    return MAGIC + blob if blob else None


def unprotect(payload):
    # accepts the output of protect(); None if it is not ours or cannot open it
    if not payload or not payload.startswith(MAGIC) or not available():
        return None
    return _run(ctypes.windll.crypt32.CryptUnprotectData, payload[len(MAGIC):])