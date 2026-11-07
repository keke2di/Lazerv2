# file logging that survives pythonw, where stdout is None

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from lazerv2 import app_paths
_FORMAT = "%(asctime)s %(levelname)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure(name, filename, level=logging.INFO, console=True):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(_FORMAT, _DATE_FORMAT)

    directory = app_paths.ensure_dir("logs")
    if directory:
        try:
            handler = RotatingFileHandler(
                os.path.join(directory, filename),
                maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        except OSError:
            pass

    if console and sys.stdout is not None:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger