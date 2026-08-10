"""A module that reads a late-bound key while it is still being imported.

That is the mistake behind the third resolution error, so both failures are
kept here for the tests to assert on.
"""

from nodrill import KeyResolutionError, ref, use

FAILURE: KeyResolutionError | None = None
NESTED_FAILURE: KeyResolutionError | None = None

try:
    use(ref("tests.cycle.at_import:Scope"))
except KeyResolutionError as exc:
    FAILURE = exc


class Scope:
    """Defined below the lookup, which is why the lookup could not find it."""


try:
    # The module is still initialising here too, but the name that is missing is
    # missing from the class, which has nothing to do with the import.
    use(ref("tests.cycle.at_import:Scope.missing"))
except KeyResolutionError as exc:
    NESTED_FAILURE = exc
