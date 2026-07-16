"""Central logging configuration.

There was no logging setup before (modules called ``getLogger`` but nothing
configured handlers/levels, so output depended on uvicorn's defaults). This gives a
single, consistent, timestamped format and an env-tunable level — usable in a terminal
and ready to point at an exporter (Sentry/JSON) later. Called once at startup.
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging() -> None:
    """Install a root handler with a consistent format (idempotent).

    Level comes from ``LOG_LEVEL`` (default ``INFO``). Safe to call more than once.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format=_FORMAT)
    _CONFIGURED = True
