"""Single entry point for logging configuration.

Keeps stdout clean and human-readable so an operator can watch `journalctl -fu
spectre` in the field without squinting at JSON.
"""

from __future__ import annotations

import logging
import os
import sys


def configure(level: str | int | None = None) -> None:
    if level is None:
        level = os.getenv("SPECTRE_LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    # Avoid double-handlers when main() is re-entered from tests.
    if root.handlers:
        root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    fmt = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
    handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(level)

    # Turn down the SDK's very chatty websockets logger.
    logging.getLogger("websockets").setLevel(logging.WARNING)
