"""Make the helper modules in this directory importable from the test files.

pytest runs with ``--import-mode=importlib``, which deliberately does *not* put
a test file's own directory on sys.path — so ``import mock_router`` would fail
without this. conftest.py is always imported first, which makes it the right
place for it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
