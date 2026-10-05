"""Logging configuration with file rotation."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(logs_dir: Path, level=logging.INFO) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("vid2cards")
    logger.setLevel(level)
    if logger.handlers:
        return logger  # already configured

    log_format = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = RotatingFileHandler(
        logs_dir / "vid2cards.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(log_format)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(log_format)
    logger.addHandler(console)

    return logger
