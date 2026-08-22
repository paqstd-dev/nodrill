"""Dispatch for python -m nodrill, which is the whole command line surface.

Not a console script, so nothing lands on a PATH and the package still
declares none, which leaves adding one later possible and removing one never
necessary.  No __name__ guard, since a __main__ module is only ever run as
one and a guard would be a branch nothing can take the other way.
"""

from ._audit import main

raise SystemExit(main())
