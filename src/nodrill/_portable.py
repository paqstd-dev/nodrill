"""Helpers for carrying a string namespace out of the process.

export() renders the providers you name as a plain dict that JSON can hold,
and adopt() opens them again wherever that dict arrives.  A codec registered
with set_codec() maps what JSON cannot hold into what it can, and its result
is checked like any other, so the envelope stays JSON whatever the codec does
inside.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager
from math import isfinite
from typing import Any

from ._core import Namespace, provider, use
from ._errors import EnvelopeVersionError, _describe_key
from ._refs import _is_ref
from ._report import _boundary_note

_VERSION = 1
_VERSION_KEY = "v"
_CTX_KEY = "ctx"

# Exact types, since a subclass round-trips as its base and would reach the consumer as one.
_SCALARS = frozenset({str, int, float, bool, type(None)})

# A data descriptor wins over __dict__, so a value under one of these names is never readable.
_RESERVED = frozenset(
    name for name, member in vars(Namespace).items() if hasattr(member, "__set__")
)

_REFUSED = (
    "does not survive a round trip through JSON. A portable value is a str, "
    "int, float, bool, None, or a list or dict of those"
)

_Hook = Callable[[dict[str, Any]], Mapping[str, Any]]


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
    rebuilt rather than referenced, so a write to a provider never reaches
    an envelope already handed on.  The result carries a version that
    adopt() checks.
    """
    # Read once, so a set_codec() part way through cannot build one envelope out of two codecs.
    dump = _codec.dump
    return {_VERSION_KEY: _VERSION, _CTX_KEY: {name: _exported(name, dump) for name in names}}


def adopt(
    payload: Mapping[str, Any],
    *,
    only: Iterable[str] | None = None,
    annotate: bool | None = None,
) -> AbstractContextManager[None]:
    """Open the providers an export() envelope carries, for the length of the block.

    Each namespace becomes an ordinary provider, shadowing any of the same
    name and unwinding on exit even if the block raises.  The payload is
    checked in the call rather than in the block, so a consumer can tell a
    malformed envelope from a failure in its own work.  Which namespaces
    open is the payload's choice unless only= names the ones this consumer
    expects, which is what to reach for when the producer is not yours.
    annotate decides for these blocks what it decides for a provider() block,
    and annotate=False keeps a payload somebody else wrote out of a traceback
    this process renders.  What no check can say is
    whether the values are true, and an adopted value is input with the same
    trust as any other request field.
    """
    return _adopting(_adopted(payload, only), annotate=annotate)


@contextmanager
def _adopting(namespaces: dict[str, dict[str, Any]], *, annotate: bool | None) -> Iterator[None]:
    """Open one provider per checked namespace, unwinding them on the way out."""
    with ExitStack() as scopes:
        for name, values in namespaces.items():
            # By key rather than as keywords, so an attribute named `frozen` stays data.
            scopes.enter_context(
                provider(Namespace._named(name, values), key=name, annotate=annotate)  # noqa: SLF001
            )
        yield


def set_codec(*, dump: _Hook | None = None, load: _Hook | None = None) -> None:
    """Register the pair that carries what JSON cannot, process wide.

    dump maps one namespace's values on the way out and load maps them back
    on the way in.  A dump result is checked the way any exported value is,
    so the envelope stays JSON and a codec that hands back an object of its
    own fails at the boundary that made it.  A dump reads what it is handed
    and never writes into it, since the containers below the top level are
    the exporting block's own.  A load runs after the payload has been
    checked, never before, so a malformed one is refused without reaching
    the codec at all.  Each call states the whole codec, and
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


def _exported(name: Any, dump: _Hook | None) -> dict[str, Any]:
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
    # Exact, since the name is a payload key and a str subclass would travel as its own type.
    if type(name) is not str:
        raise TypeError(f"export() takes string provider names, got {type(name).__name__}")
    namespace: Any = use(name)
    # Named through __class__, so a frozen namespace reports its own type.
    named = namespace.__class__
    if not isinstance(namespace, Namespace):
        raise TypeError(
            f"export({name!r}): only a string-named provider travels, and {name!r} provides "
            f"an instance of {named.__name__}. A namespace is what "
            f"provider({name!r}, ...) yields"
        )
    if named is not Namespace:
        raise TypeError(
            f"export({name!r}): {name!r} provides an instance of {named.__name__}, which "
            f"subclasses Namespace and would arrive on the other side as a plain one. "
            f"provider({name!r}, ...) yields what travels"
        )
    where = f"export({name!r})"
    # A copy, so a codec cannot add or drop attributes on the namespace the block still holds.
    values = dict(vars(namespace))
    return _values(values if dump is None else _hooked(dump, values, where), where)


def _wanted(only: Iterable[str]) -> frozenset[str]:
    """Return the names to open, having refused what is not a collection of names."""
    if isinstance(only, str):
        raise TypeError(
            f"adopt(only={only!r}) takes a collection of names, and a bare string reads as "
            f"one name per character. Pass ({only!r},) for the one name"
        )
    if not isinstance(only, Iterable):
        raise TypeError(f"adopt(only=...) takes a collection of names, got {type(only).__name__}")
    names = []
    # Checked before the set is built, so an unhashable name is refused here rather than by it.
    for name in only:
        if type(name) is not str:
            raise TypeError(f"adopt(only=...) takes provider names, and {name!r} is not a string")
        names.append(name)
    return frozenset(names)


def _adopted(payload: Any, only: Iterable[str] | None) -> dict[str, dict[str, Any]]:
    """Return the checked namespaces an envelope carries."""
    wanted = None if only is None else _wanted(only)
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
    # Read once, so a codec swapped part way through cannot open one payload through two.
    load = _codec.load
    for name, values in namespaces.items():
        # Skipped before any check, since a name this consumer did not ask for is not its business.
        if wanted is not None and name not in wanted:
            continue
        if type(name) is not str:
            raise TypeError(
                f"adopt(): 'ctx' is keyed by {type(name).__name__}, and a provider name is a string"
            )
        if not isinstance(values, Mapping):
            raise TypeError(
                f"adopt(): 'ctx' holds {type(values).__name__} under {name!r}, and a "
                f"namespace is a mapping of names to values"
            )
        where = f"adopt({name!r})"
        # Checked before the codec sees it, so a malformed payload never reaches user code.
        checked = _values(values, where)
        adopted[name] = checked if load is None else _hooked(load, checked, where)
    return adopted


def _hooked(hook: _Hook, values: dict[str, Any], where: str) -> dict[str, Any]:
    """Return what a codec half makes of a namespace, checked back into shape."""
    try:
        result = hook(values)
    except Exception as failure:
        # Its own message stays the useful one, so the note only says which call it ran under.
        _boundary_note(failure, where)
        raise
    if not isinstance(result, Mapping):
        raise TypeError(
            f"{where}: the codec returned {result.__class__.__name__} rather than a "
            f"mapping of names to values"
        )
    for attribute in result:
        if type(attribute) is not str:
            raise TypeError(
                f"{where}: the codec returned {attribute!r} as a name, and a namespace "
                f"is keyed by strings"
            )
        # Checked here as well as in the walk, since a load result never reaches the walk.
        if attribute in _RESERVED:
            raise TypeError(
                f"{where}: the codec returned {attribute!r}, which is a name the namespace "
                f"itself owns, so a value under it would never be readable"
            )
    return dict(result)


def _values(values: Mapping[Any, Any], where: str) -> dict[str, Any]:
    """Return one namespace's attributes, each checked and rebuilt."""
    built = {}
    for name, value in values.items():
        if type(name) is not str:
            raise TypeError(f"{where}: an attribute name has to be a string, got {name!r}")
        if name in _RESERVED:
            raise TypeError(
                f"{where}: {name!r} is a name the namespace itself owns, so a value under "
                f"it would never be readable"
            )
        # A scalar attribute skips the walk, which for one value is a call and two allocations.
        kind = type(value)
        if kind in _SCALARS and (kind is not float or isfinite(value)):
            built[name] = value
            continue
        built[name] = _checked(value, where, [name], set())
    return built


def _at(path: list[Any]) -> str:
    """Render the path a refusal names, which is the only place it is needed."""
    return f"{path[0]}" + "".join(f"[{step!r}]" for step in path[1:])


def _checked(value: Any, where: str, path: list[Any], seen: set[int]) -> Any:
    """Return value rebuilt as far as it is portable, or raise naming the path to it.

    Reached only for what a caller's own scalar test refused, which is a
    container, a float that is not finite, or a value that does not travel at
    all.  A portable scalar is finished where it was found instead.
    """
    kind = type(value)
    if kind is dict or kind is list:
        if id(value) in seen:
            raise ValueError(f"{where}: {_at(path)} contains itself")
        # Dropped again below, so the set is the path back rather than every container seen.
        seen.add(id(value))
        # Each loop finishes a scalar itself, since the call is most of what a scalar element costs.
        if kind is dict:
            built: Any = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError(
                        f"{where}: {_at(path)} is keyed by {key.__class__.__name__}, and a "
                        f"JSON object is keyed by strings"
                    )
                item_kind = type(item)
                if item_kind in _SCALARS and (item_kind is not float or isfinite(item)):
                    built[key] = item
                    continue
                path.append(key)
                built[key] = _checked(item, where, path, seen)
                path.pop()
        else:
            built = []
            append = built.append
            for index, item in enumerate(value):
                item_kind = type(item)
                if item_kind in _SCALARS and (item_kind is not float or isfinite(item)):
                    append(item)
                    continue
                path.append(index)
                append(_checked(item, where, path, seen))
                path.pop()
        seen.discard(id(value))
        return built
    if kind is float and not isfinite(value):
        raise ValueError(f"{where}: {_at(path)} is {value!r}, which JSON cannot represent")
    raise _refused(where, _at(path), value)


def _remedy(where: str) -> str:
    """Name the way out, which differs by direction since a consumer cannot rewrite the value."""
    # Read off the caller's prefix, so the walk carries no extra argument down the hot path.
    if where.startswith("export("):
        return "A codec registered with set_codec() carries what JSON cannot hold"
    return "The producer has to send it as a portable value, or a load codec has to rebuild it"


def _refused(where: str, path: str, value: Any) -> TypeError:
    """Return the refusal for a value that cannot travel, naming the nearest repair."""
    # Decided by type() and named by __class__, so a proxied value reports what it wraps.
    named = value.__class__
    if named is dict or named is list or named in _SCALARS:
        return TypeError(
            f"{where}: {path} is a view of a {named.__name__}, and a view does "
            f"not travel. Export what it wraps"
        )
    for base in (str, int, float, list, dict):
        if isinstance(value, base):
            return TypeError(
                f"{where}: {path} is of type {named.__name__}, which subclasses "
                f"{base.__name__} and would arrive as a plain {base.__name__}. Write "
                f"{base.__name__}(value) to say so at the boundary"
            )
    if isinstance(value, tuple):
        return TypeError(
            f"{where}: {path} is of type {named.__name__}, and a JSON array is a list. "
            f"Write list(value) to say so at the boundary"
        )
    return TypeError(
        f"{where}: {path} is of type {named.__name__}, which {_REFUSED}. {_remedy(where)}"
    )


__all__ = ["adopt", "export", "set_codec"]
