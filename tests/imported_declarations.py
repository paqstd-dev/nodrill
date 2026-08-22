"""A module that declares keys in its own body, imported by tests inside isolate()."""

from typing import Any

from nodrill import declare, ref


@declare(doc="declared while this module imported", provided_by=("test boundary",))
class ImportScope:
    """A key whose declaration belongs to this module rather than to any block."""


class LateScope:
    """The target of the ref declared below, never declared directly."""


PENDING_AT_IMPORT: Any = declare(
    ref("tests.imported_declarations:LateScope"), doc="pending since this module imported"
)
