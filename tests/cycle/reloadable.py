"""A module built to be reloaded, so the reload cannot reach anything else.

Reloading rebinds the name below to a second class, which is the case a ref
that already resolved does not follow.  Nothing else imports this module.
"""


class Scope:
    """The class a ref resolves to, and a different one after the reload."""
