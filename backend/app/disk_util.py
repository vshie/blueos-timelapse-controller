"""Disk-space helpers for the captures directory."""

from __future__ import annotations

import shutil
from pathlib import Path


def disk_free_bytes(path: Path | str) -> int:
    try:
        return int(shutil.disk_usage(str(path)).free)
    except OSError:
        return 0


def disk_total_bytes(path: Path | str) -> int:
    try:
        return int(shutil.disk_usage(str(path)).total)
    except OSError:
        return 0
