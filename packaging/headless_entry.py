"""Console entry point for the frozen headless runner.

PyInstaller needs a script to freeze, and ``python -m pingerplot.headless`` is
not one. This is that script and nothing else.
"""
from pingerplot.headless import main

if __name__ == "__main__":
    raise SystemExit(main())
