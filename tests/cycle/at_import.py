"""A module that reads a late-bound key while it is still being imported.

That is the mistake behind the third resolution error, so the failure is kept
here and the test asserts on it rather than on a description of it.
"""

from nodrill import KeyResolutionError, ref, use

FAILURE: KeyResolutionError | None = None

try:
    use(ref("tests.cycle.at_import:Scope"))
except KeyResolutionError as exc:
    FAILURE = exc


class Scope:
    """Defined below the lookup, which is why the lookup could not find it."""
