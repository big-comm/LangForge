"""Tests for conftest: add src dir to path."""

import sys
from pathlib import Path

# Add source to path so tests can import modules directly
src_dir = Path(__file__).parent.parent / "usr" / "share" / "langforge"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
