"""Compatibility entry point: prefer ``python -m ezer`` or the ``ezer`` command."""

from ezer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
