"""The upstream half: it defines the key and imports the module that reads it."""

from dataclasses import dataclass

from .models import owner_of


@dataclass
class RequestScope:
    user_id: int


def describe() -> str:
    """Report the open scope through the downstream module."""
    return f"user {owner_of()}"
