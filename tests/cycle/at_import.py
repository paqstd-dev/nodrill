"""A module that reads a late-bound key while it is still being imported.

That is the mistake behind the third resolution error, so both failures are
kept here for the tests to assert on.  What is kept is the message rather than
the exception, since the traceback of an exception would keep the ref that
failed alive for the rest of the session.
"""

from nodrill import KeyResolutionError, ref, use

FAILURE = ""
NESTED_FAILURE = ""

try:
    use(ref("tests.cycle.at_import:Scope"))
except KeyResolutionError as exc:
    FAILURE = str(exc)


class Scope:
    """Defined below the lookup, which is why the lookup could not find it."""


try:
    # The module is still initialising here too, but the name that is missing is
    # missing from the class, which has nothing to do with the import.
    use(ref("tests.cycle.at_import:Scope.missing"))
except KeyResolutionError as exc:
    NESTED_FAILURE = str(exc)
