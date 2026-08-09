"""The upstream half again, with the downstream module importing it back."""

from dataclasses import dataclass

from .direct_models import owner_of


@dataclass
class RequestScope:
    user_id: int


def describe() -> str:
    """Report the open scope through the downstream module."""
    return f"user {owner_of()}"
