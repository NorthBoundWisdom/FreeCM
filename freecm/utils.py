"""Shared utilities and type definitions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    PathValue = str | Path
else:
    PathValue = Any
