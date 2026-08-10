"""The downstream half written with a plain import, which is the cycle."""

from nodrill import use

from .direct_context import RequestScope


def owner_of() -> int:
    """Return the user id of the request scope currently open."""
    return use(RequestScope).user_id
