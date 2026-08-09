"""The @inject decorator and the FromCtx parameter marker.

The injection plan is built once at decoration time; values are resolved at
call time, never at import.
"""

from __future__ import annotations

import inspect
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from ._core import use

_T = TypeVar("_T")
F = TypeVar("F", bound=Callable[..., Any])

_MISSING = object()

_LEAK_MESSAGE = (
    "nodrill.injected reached the function body. "
    "Check that @inject is applied and a provider is active for the parameter."
)


class _InjectedSentinel:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<nodrill.injected>"

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"cannot read {name!r} from nodrill.injected. {_LEAK_MESSAGE}")

    def __bool__(self) -> bool:
        raise TypeError(_LEAK_MESSAGE)

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        raise TypeError(_LEAK_MESSAGE)


# Protocols a leaked sentinel is most likely to land in.  Special methods are
# looked up on the type, so __getattr__ above never sees them and the caller
# would get a bare "not iterable" instead of the message.  __eq__ stays at the
# default: nothing here needs it, since the plan compares with `is`, and a
# raising __eq__ would break any container the sentinel is put into.
_LEAKED_PROTOCOLS = (
    "__iter__",
    "__next__",
    "__len__",
    "__contains__",
    "__getitem__",
    "__int__",
    "__float__",
    "__index__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__add__",
    "__radd__",
    "__sub__",
    "__rsub__",
    "__mul__",
    "__rmul__",
    "__enter__",
    # __exit__ and __aexit__ exist only so the interpreter's protocol check
    # passes and the failure comes out of __enter__ with the message above.
    "__exit__",
    "__aenter__",
    "__aexit__",
    "__await__",
    "__aiter__",
    "__anext__",
)


def _make_leak_guard() -> Callable[..., Any]:
    def method(_self: _InjectedSentinel, *_args: Any, **_kwargs: Any) -> Any:
        raise TypeError(_LEAK_MESSAGE)

    return method


for _name in _LEAKED_PROTOCOLS:
    setattr(_InjectedSentinel, _name, _make_leak_guard())


# Default for injectable parameters.  Typed Any, so a signature like
# def f(db: FromCtx[Db] = injected) stays satisfiable for type checkers when
# callers omit db.  inject() treats a parameter bound to this sentinel like
# an omitted one; if it ever reaches a function body, it fails loudly.
injected: Any = _InjectedSentinel()


class _FromCtxMarker:
    """Mark a parameter for injection from the current context.

    FromCtx[AppCtx] injects the whole use(AppCtx) instance.
    Annotated[T, FromCtx("app")] pulls the attribute named after the
    parameter from use("app"); attr= overrides the name.
    """

    __slots__ = ("attr", "key")

    def __init__(self, key: str | type[Any] | None = None, attr: str | None = None) -> None:
        if key is not None and not isinstance(key, str | type):
            raise TypeError(
                f"FromCtx key must be a string name or a class, got {type(key).__name__}"
            )
        self.key = key
        self.attr = attr

    def __repr__(self) -> str:
        args = [repr(self.key)] if self.key is not None else []
        if self.attr is not None:
            args.append(f"attr={self.attr!r}")
        return f"FromCtx({', '.join(args)})"

    def __class_getitem__(cls, item: Any) -> Any:
        return Annotated[item, cls()]


if TYPE_CHECKING:
    # Type checkers see FromCtx[X] as Annotated[X, ...], which is plain X, so
    # the parameter and explicit arguments to it check as X.
    FromCtx = Annotated[_T, injected]
else:
    FromCtx = _FromCtxMarker


def from_ctx(key: str | type[Any] | None = None, attr: str | None = None) -> Any:
    """Build a FromCtx marker.

    Same as calling FromCtx, which pyright rejects because it statically
    sees an Annotated alias.
    """
    return _FromCtxMarker(key, attr)


@dataclass(frozen=True, slots=True)
class _Plan:
    """Injection plan built once at decoration time."""

    label: str
    markers: tuple[tuple[str, Callable[[], Any]], ...]
    from_key: str | type[Any] | None
    from_names: tuple[str, ...]
    from_required: frozenset[str]
    # Whether every injected parameter can be filled by keyword.
    kwargs_safe: bool
    # How many leading positional arguments cannot reach an injected
    # parameter.  At or below it, a call needs no signature binding at all.
    pos_guard: int

    @property
    def empty(self) -> bool:
        return not self.markers and self.from_key is None


# typing.Union covers Optional[X] and Union[X, Y]; types.UnionType covers the
# X | Y form (the two are the same object from Python 3.14 on).
_UNION_ORIGINS = (Union, types.UnionType)


def _find_marker(annotation: Any) -> tuple[Any, _FromCtxMarker] | None:
    origin = get_origin(annotation)
    if origin in _UNION_ORIGINS:
        # FromCtx[Db] | None, and on Python 3.10 also plain FromCtx[Db] with a
        # None default, which get_type_hints() wraps in Optional by itself.
        # The marker still decides what gets injected; the union only widens
        # what an explicit argument may be.
        for arg in get_args(annotation):
            found = _find_marker(arg)
            if found is not None:
                return found
        return None
    if origin is not Annotated:
        return None
    base, *metadata = get_args(annotation)
    for meta in metadata:
        if isinstance(meta, _FromCtxMarker):
            return base, meta
        if isinstance(meta, type) and issubclass(meta, _FromCtxMarker):
            return base, _FromCtxMarker()  # bare Annotated[X, FromCtx]
    return None


def _resolver_for(param_name: str, base: Any, marker: _FromCtxMarker) -> Callable[[], Any]:
    key = marker.key
    attr = marker.attr
    if key is None:
        # get_origin() catches parameterized generics like list[str], which
        # isinstance(..., type) wrongly accepts on Python 3.10.
        if not isinstance(base, type) or get_origin(base) is not None:
            raise TypeError(
                f"@inject: parameter {param_name!r} uses a bare FromCtx marker, but its "
                f"annotated type {base!r} is not a plain class; use FromCtx(SomeClass) "
                f"or FromCtx('name') to name the context explicitly"
            )
        key = base
    if isinstance(key, str) and attr is None:
        attr = param_name
    if attr is None:
        whole_key = key
        return lambda: use(whole_key)
    attr_key, attr_name = key, attr
    return lambda: getattr(use(attr_key), attr_name)


_KWARGS_SAFE_KINDS = (
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.KEYWORD_ONLY,
)

# pos_guard for a function no injected parameter can be passed positionally to.
_NO_POSITIONAL = sys.maxsize


def _build_plan(
    func: Callable[..., Any], sig: inspect.Signature, from_key: str | type[Any] | None
) -> _Plan:
    params = list(sig.parameters.values())
    hints: dict[str, Any] = {}
    if any(p.annotation is not inspect.Parameter.empty for p in params):
        # May raise NameError on unresolved forward refs; the caller then
        # defers plan building to the first call.
        hints = get_type_hints(func, include_extras=True)

    markers: list[tuple[str, Callable[[], Any]]] = []
    injected_params: list[inspect.Parameter] = []
    marker_names: set[str] = set()
    for param in params:
        found = _find_marker(hints.get(param.name))
        if found is None:
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError(f"@inject: cannot inject into variadic parameter *{param.name}")
        markers.append((param.name, _resolver_for(param.name, *found)))
        marker_names.add(param.name)
        injected_params.append(param)

    from_names: tuple[str, ...] = ()
    from_required: frozenset[str] = frozenset()
    if from_key is not None:
        eligible = [
            p
            for p in params
            if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
            and p.name not in marker_names
            and p.name not in ("self", "cls")
        ]
        from_names = tuple(p.name for p in eligible)
        from_required = frozenset(p.name for p in eligible if p.default is inspect.Parameter.empty)
        injected_params.extend(eligible)

    positions = {p.name: i for i, p in enumerate(params)}
    return _Plan(
        func.__qualname__,
        tuple(markers),
        from_key,
        from_names,
        from_required,
        kwargs_safe=all(p.kind in _KWARGS_SAFE_KINDS for p in injected_params),
        pos_guard=min(
            (
                positions[p.name]
                for p in injected_params
                if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
            ),
            default=_NO_POSITIONAL,
        ),
    )


def _fill(plan: _Plan, arguments: dict[str, Any]) -> None:
    """Resolve missing parameters into arguments; explicit values win."""
    for name, resolve in plan.markers:
        if name not in arguments or arguments[name] is injected:
            arguments[name] = resolve()
    if plan.from_key is None:
        return
    pending = [n for n in plan.from_names if n not in arguments or arguments[n] is injected]
    if pending:
        source = use(plan.from_key)
        for name in pending:
            value = getattr(source, name, _MISSING)
            if value is not _MISSING:
                arguments[name] = value
            elif name in arguments:
                # Passed as `injected`, with nothing in the context to fill it:
                # drop it so the parameter's own default applies, exactly as if
                # the caller had omitted the argument.
                del arguments[name]
    unmet = [n for n in plan.from_required if n not in arguments]
    if unmet:
        names = ", ".join(repr(n) for n in sorted(unmet))
        raise TypeError(
            f"{plan.label}(): parameter(s) {names} were not passed explicitly and "
            f"use({plan.from_key!r}) has no matching attribute(s)"
        )


# Enough to tell an annotation that asks for injection from one that is merely
# unresolvable.  A marker aliased under another name escapes it, which costs
# that caller the clear error below and nothing else.
_MARKER_TOKENS = ("FromCtx", "from_ctx")


def _mentions_marker(func: Callable[..., Any]) -> bool:
    """Report whether any raw annotation of func names a marker."""
    annotations: dict[str, Any] = getattr(func, "__annotations__", {})
    return any(
        isinstance(annotation, str) and any(token in annotation for token in _MARKER_TOKENS)
        for annotation in annotations.values()
    )


def _decorate(obj: Any, from_key: str | type[Any] | None) -> Any:
    if isinstance(obj, staticmethod):
        return staticmethod(_decorate(obj.__func__, from_key))
    if isinstance(obj, classmethod):
        return classmethod(_decorate(obj.__func__, from_key))
    if isinstance(obj, type):
        raise TypeError(
            f"@inject does not support classes: decorating {obj.__name__} would replace "
            f"the class with a function, breaking isinstance and subclassing. "
            f"Decorate {obj.__name__}.__init__ instead."
        )
    if inspect.isgeneratorfunction(obj) or inspect.isasyncgenfunction(obj):
        raise TypeError(
            "@inject does not support generator functions: injection resolves when the "
            "function is called, but a generator body runs later, possibly under "
            "different providers, and would silently capture stale context. "
            "Call use() inside the generator body instead."
        )
    if not callable(obj):
        raise TypeError(f"@inject expects a callable, got {type(obj).__name__}")

    func: Callable[..., Any] = obj
    sig = inspect.signature(func)
    plan: _Plan | None
    try:
        plan = _build_plan(func, sig, from_key)
    except NameError:
        # Unresolvable forward references (PEP 563 string annotations naming
        # things defined later).  Build lazily on the first call instead.
        plan = None

    if plan is not None and plan.empty:
        return func  # nothing to inject
    return _make_wrapper(func, sig, plan, from_key)


def _make_wrapper(
    func: Callable[..., Any],
    sig: inspect.Signature,
    plan: _Plan | None,
    from_key: str | type[Any] | None,
) -> Callable[..., Any]:
    def ensure_plan() -> _Plan:
        # Reached only while plan is None.  A racing first call may build the
        # plan twice; the results are identical.
        nonlocal plan
        try:
            built = _build_plan(func, sig, from_key)
        except NameError as exc:
            if from_key is None and not _mentions_marker(func):
                # Nothing here asked for injection: the unresolvable names sit
                # in annotations only a type checker ever reads, the shape
                # TYPE_CHECKING imports produce.  Pass the call through rather
                # than break a function over hints nodrill does not need.
                built = _Plan(
                    func.__qualname__,
                    (),
                    None,
                    (),
                    frozenset(),
                    kwargs_safe=True,
                    pos_guard=_NO_POSITIONAL,
                )
            else:
                raise NameError(
                    f"@inject: cannot resolve type hints for {func.__qualname__}: {exc}. "
                    f"Annotations on an injected function are read at runtime, so the names "
                    f"they use must exist then; a TYPE_CHECKING-only import never will."
                ) from exc
        plan = built
        return built

    # The two wrappers below are the same shape written twice rather than
    # shared, because at this size the extra call is about a third of the fast
    # path.  That path holds when no positional argument can reach an injected
    # parameter and every injected parameter accepts a keyword: the resolved
    # values go straight into kwargs, which is the wrapper's own dict, so
    # filling it in place is safe, and the signature is never bound.

    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            current = plan if plan is not None else ensure_plan()
            if current.kwargs_safe and len(args) <= current.pos_guard:
                _fill(current, kwargs)
                return await func(*args, **kwargs)
            bound = sig.bind_partial(*args, **kwargs)
            _fill(current, bound.arguments)
            return await func(*bound.args, **bound.kwargs)

        return async_wrapper

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        current = plan if plan is not None else ensure_plan()
        if current.kwargs_safe and len(args) <= current.pos_guard:
            _fill(current, kwargs)
            return func(*args, **kwargs)
        bound = sig.bind_partial(*args, **kwargs)
        _fill(current, bound.arguments)
        return func(*bound.args, **bound.kwargs)

    return wrapper


@overload
def inject(func: F, /) -> F: ...
@overload
def inject(*, from_: str | type[Any] = ...) -> Callable[[F], F]: ...
def inject(func: Any = None, /, *, from_: str | type[Any] | None = None) -> Any:
    """Fill missing parameters from the current context at call time.

    Marker style annotates parameters with FromCtx or from_ctx.  Name style,
    @inject(from_="app"), fills every parameter whose name matches an
    attribute of use("app"), defaults included; self and cls are skipped.
    Explicitly passed arguments always win, an explicit None included.

    Works on plain and async functions, methods, classmethods and
    staticmethods in either decorator order.  Generator functions are
    rejected: their bodies run after the call, possibly under different
    providers.
    """
    if from_ is not None and not isinstance(from_, str | type):
        raise TypeError(f"from_ must be a string name or a class, got {type(from_).__name__}")
    if func is None:
        return lambda f: _decorate(f, from_)
    return _decorate(func, from_)


__all__ = ["FromCtx", "from_ctx", "inject", "injected"]
