"""Vercel entrypoint for Kin's Python backend (see src/kin/server.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from kin.server import app  # noqa: E402

__all__ = ["app"]
