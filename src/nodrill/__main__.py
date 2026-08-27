"""Dispatch for python -m nodrill, which is the whole command line surface.

Not a console script, so nothing lands on a PATH and the package still declares
none, which leaves adding one later possible and removing one never necessary.
"""

from ._audit import main

if __name__ == "__main__":
    raise SystemExit(main())
