"""Helpers for carrying a string namespace out of the process.

export() renders the providers you name as a plain dict that JSON can hold,
and adopt() opens them again wherever that dict arrives.  A codec registered
with set_codec() maps what JSON cannot hold into what it can, and its result
is checked like any other, so the envelope stays JSON whatever the codec does
inside.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from math import isfinite
from typing import Any

from ._core import Namespace, provider, use
from ._errors import EnvelopeVersionError, _describe_key
from ._refs import _is_ref

_VERSION = 1
_VERSION_KEY = "v"
_CTX_KEY = "ctx"

# Exact types, since a subclass round-trips as its base and would reach the consumer as one.
_SCALARS = frozenset({str, int, float, bool, type(None)})

_REFUSED = (
    "does not survive a round trip through JSON. A portable value is a str, "
    "int, float, bool, None, or a list or dict of those"
)

_Values = Mapping[str, Any]
_Hook = Callable[[dict[str, Any]], _Values]


class _Codec:
    """The process-wide codec, in one object so no function needs a global statement."""

    __slots__ = ("dump", "load")

    def __init__(self) -> None:
        self.dump: _Hook | None = None
        self.load: _Hook | None = None


_codec = _Codec()


def export(*names: str) -> dict[str, Any]:
    """Return the named providers as a plain dict, ready for JSON.

    Nothing travels unless it is named here, and every value has to be
    JSON-safe, meaning a str, int, float, bool, None, or a list or dict of
    those.  Anything else raises rather than being coerced, so a value
    arrives on the other side as itself or not at all.  Containers are
    copied, so the envelope is a snapshot of the moment it was taken.  The
    result carries a version that adopt() checks.
    """
    return {_VERSION_KEY: _VERSION, _CTX_KEY: {name: _exported(name) for name in names}}


@contextmanager
def adopt(payload: Mapping[str, Any]) -> Iterator[None]:
    """Open the providers an export() envelope carries, for the length of the block.

    Each namespace becomes an ordinary provider, shadowing any of the same
    name and unwinding on exit even if the block raises.  The payload is
    checked the way export() checks what it writes, so a malformed one fails
    here rather than three frames down.  What it cannot check is whether the
    values are true, and an adopted value is input with the same trust as
    any other request field.
    """
    with ExitStack() as scopes:
        for name, values in _adopted(payload).items():
            # By key rather than as keywords, so an attribute named `frozen` stays data.
            scopes.enter_context(provider(Namespace._named(name, values), key=name))  # noqa: SLF001
        yield


def set_codec(*, dump: _Hook | None = None, load: _Hook | None = None) -> None:
    """Register the pair that carries what JSON cannot, process wide.

    dump maps one namespace's values on the way out and load maps them back
    on the way in.  A dump result is checked the way any exported value is,
    so the envelope stays JSON and a codec that hands back an object of its
    own fails at the boundary that made it.  A load runs after the payload
    has been checked, never before, so a malformed one is refused without
    reaching the codec at all.  Each call states the whole codec, and
    set_codec() with no arguments clears both, while a service that only
    produces or only consumes registers the one half it needs.  Both ends of
    a boundary have to agree on the format, which is why this is startup
    configuration rather than something a scope decides.
    """
    for role, hook in (("dump", dump), ("load", load)):
        if hook is not None and not callable(hook):
            raise TypeError(
                f"set_codec({role}=...) takes a callable or None, got {type(hook).__name__}"
            )
    _codec.dump = dump
    _codec.load = load


def _exported(name: Any) -> dict[str, Any]:
    """Return one provider's values, checked, having checked the name first.

    Typed loosely, since this is where a name of the wrong kind is caught
    and an annotation cannot rule one out first.
    """
    # A ref is a late-bound name for a class key, so it gets the answer a class gets.
    if isinstance(name, type) or _is_ref(name):
        raise TypeError(
            f"export() takes string provider names, got {_describe_key(name)}. A class key "
            f"cannot be reopened on the other side without importing the class, so a "
            f"class-keyed provider does not travel"
        )
    if not isinstance(name, str):
        raise TypeError(f"export() takes string provider names, got {type(name).__name__}")
    namespace: Any = use(name)
    if not isinstance(namespace, Namespace):
        # Named through __class__, so a frozen namespace reports its own type.
        raise TypeError(
            f"export({name!r}): only a string-named provider travels, and {name!r} provides "
            f"an instance of {namespace.__class__.__name__}. A namespace is what "
            f"provider({name!r}, ...) yields"
        )
    where = f"export({name!r})"
    # A copy, since another thread holding the same namespace can write to it while this walks.
    values = dict(vars(namespace))
    dump = _codec.dump
    if dump is None:
        return _values(values, where)
    encoded = dump(values)
    if not isinstance(encoded, Mapping):
        raise TypeError(
            f"{where}: the codec returned {encoded.__class__.__name__} rather than a "
            f"mapping of names to values"
        )
    return _values(encoded, where)


def _adopted(payload: Any) -> dict[str, dict[str, Any]]:
    """Return the checked namespaces an envelope carries."""
    if not isinstance(payload, Mapping):
        raise TypeError(
            f"adopt() expects the mapping export() returned, got {type(payload).__name__}"
        )
    version = payload.get(_VERSION_KEY)
    # An exact check, since a payload carrying True would otherwise read as version 1.
    if type(version) is not int:
        raise TypeError(
            "adopt() expects the mapping export() returned, which carries its version under 'v'"
        )
    if version != _VERSION:
        raise EnvelopeVersionError(version, _VERSION)
    namespaces = payload.get(_CTX_KEY)
    if not isinstance(namespaces, Mapping):
        raise TypeError(
            "adopt() expects the mapping export() returned, which carries its "
            "namespaces under 'ctx'"
        )
    adopted = {}
    for name, values in namespaces.items():
        if not isinstance(name, str):
            raise TypeError(
                f"adopt(): 'ctx' is keyed by {type(name).__name__}, and a provider name is a string"
            )
        if not isinstance(values, Mapping):
            raise TypeError(
                f"adopt(): 'ctx' holds {type(values).__name__} under {name!r}, and a "
                f"namespace is a mapping of names to values"
            )
        # Checked before the codec sees it, so a malformed payload never reaches user code.
        adopted[name] = _loaded(_values(values, f"adopt({name!r})"), name)
    return adopted


def _loaded(values: dict[str, Any], name: str) -> dict[str, Any]:
    """Return what the codec makes of one checked namespace, checked back into shape."""
    load = _codec.load
    if load is None:
        return values
    decoded = load(values)
    if not isinstance(decoded, Mapping):
        raise TypeError(
            f"adopt({name!r}): the codec returned {decoded.__class__.__name__} rather than "
            f"a mapping of names to values"
        )
    for attribute in decoded:
        if not isinstance(attribute, str):
            raise TypeError(
                f"adopt({name!r}): the codec returned {attribute!r} as a name, and a "
                f"namespace is keyed by strings"
            )
    return dict(decoded)


def _values(values: Mapping[Any, Any], where: str) -> dict[str, Any]:
    """Return one namespace's attributes, each checked and rebuilt."""
    built = {}
    for name, value in values.items():
        if type(name) is not str:
            raise TypeError(f"{where}: an attribute name has to be a string, got {name!r}")
        built[name] = _checked(value, where, name, frozenset())
    return built


def _checked(value: Any, where: str, path: str, seen: frozenset[int]) -> Any:
    """Return value rebuilt as far as it is portable, or raise naming the path to it."""
    kind = type(value)
    if kind is dict or kind is list:
        if id(value) in seen:
            raise ValueError(f"{where}: {path} contains itself")
        seen = seen | {id(value)}
    if kind is dict:
        built = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(
                    f"{where}: {path} is keyed by {key.__class__.__name__}, and a JSON "
                    f"object is keyed by strings"
                )
            built[key] = _checked(item, where, f"{path}[{key!r}]", seen)
        return built
    if kind is list:
        return [_checked(item, where, f"{path}[{index}]", seen) for index, item in enumerate(value)]
    if kind is float and not isfinite(value):
        raise ValueError(f"{where}: {path} is {value!r}, which JSON cannot represent")
    if kind in _SCALARS:
        return value
    # Decided by type() and named by __class__, so a frozen value reports what it wraps.
    raise TypeError(f"{where}: {path} is of type {value.__class__.__name__}, which {_REFUSED}")


__all__ = ["adopt", "export", "set_codec"]
