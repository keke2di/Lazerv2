# checks a token against Discord off the UI thread
#
# A login used to block the window for up to 10 seconds on a slow or dead
# connection. This runs the request on a worker; the window applies the
# result on its own thread when `done` arrives.

from PyQt6.QtCore import QThread, pyqtSignal

from lazerv2.api import discord as api


class TokenCheck(QThread):
    # user data (dict) or None, error (str) or None, the normalized token
    done = pyqtSignal(object, object, str)

    def __init__(self, token):
        super().__init__()
        self.token = api.normalize_token(token)

    def run(self):
        user_data, error = api.validate(self.token)
        self.done.emit(user_data, error, self.token)
