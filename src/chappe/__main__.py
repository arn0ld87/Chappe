"""Ermöglicht den Aufruf über `python3 -m chappe ...`."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
