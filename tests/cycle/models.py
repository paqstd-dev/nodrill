"""The downstream half: it reads the key without importing where it lives."""

from typing import TYPE_CHECKING

from nodrill import FromCtx, inject, injected, ref, use

if TYPE_CHECKING:
    from .context import RequestScope
else:
    RequestScope = ref("tests.cycle.context:RequestScope")


def owner_of() -> int:
    """Return the user id of the request scope currently open."""
    return use(RequestScope).user_id


@inject
def greet(scope: FromCtx[RequestScope] = injected) -> str:
    """Read the same key through an injected parameter instead."""
    return f"hello {scope.user_id}"
