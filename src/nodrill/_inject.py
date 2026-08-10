"""The @inject decorator and the FromCtx parameter marker.

The injection plan is built once at decoration time and compiled into a
wrapper that mirrors the function's own signature.  Values are resolved
at call time, never at import.
"""

from __future__ import annotations

import inspect
import itertools
import keyword
import linecache
import types
import weakref
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

from ._core import _registry, _resolve_miss
from ._errors import _describe_key
from ._refs import _is_ref, _Key

_T = TypeVar("_T")
F = TypeVar("F", bound=Callable[..., Any])

# Internal miss sentinel for the generated lookups, never a parameter default.
# Defaults use the public injected sentinel, so introspection reports something readable.
_OMITTED = object()

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


# Special methods bypass __getattr__. A raising __eq__ would break containers, so it is omitted.
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
    "__aenter__",
    # Present only so the protocol check passes and the failure comes out of __enter__.
    "__exit__",
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


# Typed Any, so a marked parameter defaulting to it stays satisfiable when callers omit it.
injected: Any = _InjectedSentinel()


class _FromCtxMarker:
    """Mark a parameter for injection from the current context.

    FromCtx[AppCtx] injects the whole use(AppCtx) instance.
    Annotated[T, FromCtx("app")] pulls the attribute named after the
    parameter from use("app").  Passing attr= overrides the name.
    """

    __slots__ = ("attr", "key")

    def __init__(self, key: _Key | None = None, attr: str | None = None) -> None:
        if key is not None and not isinstance(key, str | type) and not _is_ref(key):
            raise TypeError(
                f"FromCtx key must be a string name, a class or a ref(), got {type(key).__name__}"
            )
        self.key = key
        self.attr = attr

    def __repr__(self) -> str:
        args = [repr(self.key)] if self.key is not None else []
        if self.attr is not None:
            args.append(f"attr={self.attr!r}")
        return f"FromCtx({', '.join(args)})"

    def __class_getitem__(cls, item: Any) -> Any:
        if _is_ref(item):
            # A ref is not a type, so it cannot be the annotated base.  It names
            # the key instead, which is all injection reads the annotation for.
            return Annotated[Any, cls(item)]
        return Annotated[item, cls()]


if TYPE_CHECKING:
    # Checkers see Annotated[X, ...], which is plain X, so parameters and arguments check as X.
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
class _Marker:
    """One parameter resolved through a FromCtx marker."""

    name: str
    key: _Key
    attr: str | None


@dataclass(frozen=True, slots=True)
class _Plan:
    """Injection plan built once at decoration time."""

    label: str
    markers: tuple[_Marker, ...]
    from_key: _Key | None
    from_names: tuple[str, ...]
    from_required: frozenset[str]

    @property
    def empty(self) -> bool:
        return not self.markers and self.from_key is None


# Two spellings of one concept, and literally one object from Python 3.14 on.
_UNION_ORIGINS = (Union, types.UnionType)


def _find_marker(annotation: Any) -> tuple[Any, _FromCtxMarker] | None:
    origin = get_origin(annotation)
    if origin in _UNION_ORIGINS:
        # Also FromCtx[Db] with a None default, which get_type_hints() makes Optional on 3.10.
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


def _marker_spec(param_name: str, base: Any, marker: _FromCtxMarker) -> _Marker:
    key = marker.key
    attr = marker.attr
    if key is None:
        # get_origin() catches list[str], which isinstance(..., type) wrongly accepts on 3.10.
        if not isinstance(base, type) or get_origin(base) is not None:
            raise TypeError(
                f"@inject: parameter {param_name!r} uses a bare FromCtx marker, but its "
                f"annotated type {base!r} is not a plain class. Use FromCtx(SomeClass) "
                f"or FromCtx('name') to name the context explicitly"
            )
        key = base
    if isinstance(key, str) and attr is None:
        # A ref naming a string reaches this same rule at the first call, since
        # nothing here is allowed to import it.
        attr = param_name
    return _Marker(param_name, key, attr)


def _build_plan(func: Callable[..., Any], sig: inspect.Signature, from_key: _Key | None) -> _Plan:
    params = list(sig.parameters.values())
    hints: dict[str, Any] = {}
    if any(p.annotation is not inspect.Parameter.empty for p in params):
        # May raise NameError on unresolved forward refs.
        hints = get_type_hints(func, include_extras=True)

    markers: list[_Marker] = []
    marker_names: set[str] = set()
    for param in params:
        found = _find_marker(hints.get(param.name))
        if found is None:
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError(f"@inject: cannot inject into variadic parameter *{param.name}")
        markers.append(_marker_spec(param.name, *found))
        marker_names.add(param.name)

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

    return _Plan(func.__qualname__, tuple(markers), from_key, from_names, from_required)


def _unmet_error(label: str, from_key: _Key, unmet: list[str]) -> TypeError:
    """Build the error for required by-name parameters the context could not fill."""
    names = ", ".join(repr(n) for n in unmet)
    return TypeError(
        f"{label}(): parameter(s) {names} were not passed explicitly and "
        f"use({_describe_key(from_key)}) has no matching attribute(s)"
    )


def _missing_error(label: str, values: tuple[tuple[str, Any], ...]) -> TypeError:
    """Build the missing-argument error in the interpreter's own wording.

    Count, plural and the serial-comma conjunction all match what CPython
    raises for an undecorated function, pinned by a parity test.
    """
    names = [name for name, value in values if value is injected]
    count = len(names)
    if count == 1:
        listed = repr(names[0])
    else:
        # Two names join with a bare "and". Three or more take the serial comma.
        separator = " and " if count == 2 else ", and "  # noqa: PLR2004
        listed = ", ".join(repr(n) for n in names[:-1]) + separator + repr(names[-1])
    plural = "s" if count > 1 else ""
    return TypeError(f"{label}() missing {count} required positional argument{plural}: {listed}")


def _reserved(name: str) -> bool:
    """Report whether a def statement cannot bind name.

    __debug__ is the one non-keyword identifier the compiler refuses.
    """
    return keyword.iskeyword(name) or name == "__debug__"


class _WrapperSpace:
    """The wrapper's future globals and the naming discipline over them.

    Attribute access mangles a suffix with the parameter-safe prefix, which
    is how a template spells a reference.  bind() is the single way a value
    enters the namespace under such a name.  Slots marked transient feed the
    def statement only and are dropped once the wrapper exists.
    """

    __slots__ = ("_transient", "prefix", "space")

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.space: dict[str, Any] = {}
        self._transient: list[str] = []

    def __getattr__(self, suffix: str) -> str:
        return self.prefix + suffix

    def bind(self, suffix: str, value: Any, *, transient: bool = False) -> str:
        """Enter value under the mangled suffix and return the generated name."""
        slot = self.prefix + suffix
        self.space[slot] = value
        if transient:
            self._transient.append(slot)
        return slot

    def prune(self) -> None:
        """Drop the slots only the def statement read."""
        for slot in self._transient:
            del self.space[slot]
        self._transient.clear()


def _parameter_list(
    params: list[inspect.Parameter],
    injectable: set[str],
    ns: _WrapperSpace,
) -> tuple[list[str], list[str], list[str]]:
    """Render the wrapper's parameter list and call expression in one pass.

    One kind dispatch produces both the mirrored header and the argument list
    forwarding every parameter to func, so the two cannot disagree.
    Injectable parameters default to the public injected sentinel, which is
    what introspection then reports.  A required positional that follows one
    needs a default too, syntactically.  Those names come back third, for
    the body to guard before anything resolves.
    """
    header: list[str] = []
    call: list[str] = []
    missing: list[str] = []
    defaulted = False
    starred = False
    last = len(params) - 1
    for index, param in enumerate(params):
        kind = param.kind
        if kind is inspect.Parameter.VAR_POSITIONAL:
            header.append(f"*{param.name}")
            call.append(f"*{param.name}")
            starred = True
            continue
        if kind is inspect.Parameter.VAR_KEYWORD:
            header.append(f"**{param.name}")
            call.append(f"**{param.name}")
            continue
        if kind is inspect.Parameter.KEYWORD_ONLY:
            if not starred:
                header.append("*")
                starred = True
            call.append(f"{param.name}={param.name}")
        else:
            call.append(param.name)
        if param.name in injectable:
            header.append(f"{param.name}={ns.injected}")
            defaulted = True
        elif param.default is not inspect.Parameter.empty:
            slot = ns.bind(f"default_{param.name}", param.default, transient=True)
            header.append(f"{param.name}={slot}")
            defaulted = True
        elif defaulted and kind is not inspect.Parameter.KEYWORD_ONLY:
            header.append(f"{param.name}={ns.injected}")
            missing.append(param.name)
        else:
            header.append(param.name)
        if kind is inspect.Parameter.POSITIONAL_ONLY and (
            index == last or params[index + 1].kind is not inspect.Parameter.POSITIONAL_ONLY
        ):
            header.append("/")
    return header, call, missing


def _missing_guard_lines(label: str, missing: list[str], ns: _WrapperSpace) -> list[str]:
    """Render the guard for required positionals the header had to default.

    Emitted before any resolution, so an under-supplied call fails the way
    the undecorated function would instead of surfacing a provider miss.
    """
    if not missing:
        return []
    ns.bind("label", label)
    ns.bind("missing_error", _missing_error)
    condition = " or ".join(f"{name} is {ns.injected}" for name in missing)
    pairs = ", ".join(f"({name!r}, {name})" for name in missing)
    return [
        f"if {condition}:",
        f"    raise {ns.missing_error}({ns.label}, ({pairs},))",
    ]


def _resolve_lines(target: str, key: str, ns: _WrapperSpace, indent: str) -> list[str]:
    """Render the one lookup template, an inlined registry hit with the miss path in _core."""
    return [
        f"{indent}{target} = {ns.registry}().get({key}, {ns.omitted})",
        f"{indent}if {target} is {ns.omitted}:",
        f"{indent}    {target} = {ns.miss}({key})",
    ]


def _ref_attr(key: Any, value: Any, name: str) -> Any:
    """Return the attribute a string key implies, or the value itself.

    A string key hands over a namespace and the parameter names the attribute
    to read from it, while a class key hands over the instance.  Which of the
    two a ref is becomes known only when it resolves, so the choice waits for
    the call the way the lookup does.
    """
    return getattr(value, name) if isinstance(key.resolve(), str) else value


def _marker_fill(marker: _Marker, key: str, ns: _WrapperSpace) -> str | None:
    """Render what turns the resolved value into the argument, if anything."""
    if marker.attr is None:
        if not _is_ref(marker.key):
            return None
        ns.bind("ref_attr", _ref_attr)
        return f"{ns.ref_attr}({key}, {ns.value}, {marker.name!r})"
    if marker.attr.isidentifier() and not keyword.iskeyword(marker.attr):
        return f"{ns.value}.{marker.attr}"
    attr = ns.bind(f"attr_{marker.name}", marker.attr)
    ns.bind("getattr", getattr)
    return f"{ns.getattr}({ns.value}, {attr})"


def _marker_lines(markers: tuple[_Marker, ...], ns: _WrapperSpace) -> list[str]:
    """Render the resolution block of each marker parameter."""
    lines: list[str] = []
    for marker in markers:
        key = ns.bind(f"key_{marker.name}", marker.key)
        fill = _marker_fill(marker, key, ns)
        target = marker.name if fill is None else ns.value
        lines.append(f"if {marker.name} is {ns.injected}:")
        lines += _resolve_lines(target, key, ns, "    ")
        if fill is not None:
            lines.append(f"    {marker.name} = {fill}")
    return lines


def _by_name_lines(plan: _Plan, params: list[inspect.Parameter], ns: _WrapperSpace) -> list[str]:
    """Render the by-name fills of from_ mode, in signature order.

    The source namespace is fetched once, lazily, so a call that passes every
    parameter explicitly needs no provider at all.  An attribute the context
    lacks falls back to the parameter's own default, or into the unmet error
    raised after every fill has had its chance.
    """
    if not plan.from_names:
        return []
    from_key = ns.bind("from_key", plan.from_key)
    ns.bind("getattr", getattr)
    by_name = {p.name: p for p in params}
    lines = [f"{ns.source} = {ns.omitted}"]
    if plan.from_required:
        lines.append(f"{ns.unmet} = []")
    for name in plan.from_names:
        lines += [
            f"if {name} is {ns.injected}:",
            f"    if {ns.source} is {ns.omitted}:",
            *_resolve_lines(ns.source, from_key, ns, "        "),
            f"    {name} = {ns.getattr}({ns.source}, {name!r}, {ns.omitted})",
            f"    if {name} is {ns.omitted}:",
        ]
        if name in plan.from_required:
            lines.append(f"        {ns.unmet}.append({name!r})")
        else:
            slot = ns.bind(f"default_{name}", by_name[name].default)
            lines.append(f"        {name} = {slot}")
    if plan.from_required:
        ns.bind("label", plan.label)
        ns.bind("unmet_error", _unmet_error)
        lines += [
            f"if {ns.unmet}:",
            f"    raise {ns.unmet_error}({ns.label}, {from_key}, {ns.unmet})",
        ]
    return lines


def _render_wrapper(
    func: Callable[..., Any], sig: inspect.Signature, plan: _Plan
) -> tuple[str, str, _WrapperSpace]:
    """Render the wrapper's def name, source text and seeded namespace.

    The wrapper mirrors func's signature, so the interpreter binds arguments
    natively and the body is one sentinel check per injectable parameter.
    Parameter names, identifiers by construction, are the only text embedded
    directly.  Every other constant goes through the namespace, and internal
    names carry a prefix no parameter shares.
    """
    params = list(sig.parameters.values())
    for param in params:
        # inspect.Parameter accepts keyword names for positional-only slots,
        # the way C signatures spell 'from', but a def statement cannot.
        if _reserved(param.name):
            raise TypeError(
                f"@inject cannot compile {plan.label}: parameter name "
                f"{param.name!r} cannot appear in generated source"
            )
    prefix = "_nd_"
    while any(p.name.startswith(prefix) for p in params):
        prefix += "_"
    ns = _WrapperSpace(prefix)
    ns.bind("func", func)
    ns.bind("omitted", _OMITTED)
    ns.bind("injected", injected)
    ns.bind("registry", _registry.get)
    ns.bind("miss", _resolve_miss)

    injectable = {m.name for m in plan.markers} | set(plan.from_names)
    header, call, missing = _parameter_list(params, injectable, ns)
    body = _missing_guard_lines(plan.label, missing, ns)
    body += _marker_lines(plan.markers, ns)
    body += _by_name_lines(plan, params, ns)

    # The function's own name keeps tracebacks readable.
    # A name no def can bind, or one that would shadow a helper, falls back to the mangled one.
    own = getattr(func, "__name__", "")
    name = (
        own
        if own.isidentifier() and not _reserved(own) and not own.startswith(prefix)
        else ns.wrapper
    )

    is_async = inspect.iscoroutinefunction(func)
    define = "async def" if is_async else "def"
    ret = "return await" if is_async else "return"
    lines = [f"{define} {name}({', '.join(header)}):"]
    lines.extend(f"    {line}" for line in body)
    lines.append(f"    {ret} {ns.func}({', '.join(call)})")
    return name, "\n".join(lines), ns


def _compile_wrapper(
    func: Callable[..., Any], sig: inspect.Signature, plan: _Plan
) -> Callable[..., Any]:
    """Materialize the rendered wrapper and tie the lifetimes together.

    The registered source lives exactly as long as the wrapper, and the
    wrapper is popped out of its own globals so nothing needs the cycle
    collector to die.
    """
    name, source, ns = _render_wrapper(func, sig, plan)
    filename = f"<@inject {plan.label}-{next(_SOURCE_IDS)}>"
    code = compile(source, filename, "exec")
    # The source is assembled from parameter names and mangled helpers, none of it user-typed.
    exec(code, ns.space)  # noqa: S102
    wrapper: Callable[..., Any] = ns.space.pop(name)
    ns.prune()
    # Registration waits for the wrapper, since only its finalizer below removes the entry.
    _register_source(filename, source)
    weakref.finalize(wrapper, linecache.cache.pop, filename, None)
    return wraps(func)(wrapper)


# Registered sources need process-unique filenames, and qualnames repeat.
# Probing linecache.cache instead would hand out a live wrapper's filename after clearcache().
_SOURCE_IDS = itertools.count(1)


def _register_source(filename: str, source: str) -> None:
    """File the generated source under filename.

    Registration in linecache is what lets tracebacks through a wrapper and
    pdb show the generated lines instead of blanks, the same trick attrs
    uses.  Entries with a None mtime survive linecache.checkcache().  The
    matching removal runs when the wrapper itself is collected.
    """
    linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)


def _make_deferred(
    func: Callable[..., Any], sig: inspect.Signature, from_key: _Key | None
) -> Callable[..., Any]:
    """Wrap func so the plan builds and compiles on the first call.

    Needed when decoration hits unresolvable forward references.  Callers
    hold this wrapper, so the dispatch has to stay after the build, which
    costs one indirection per call on this rare shape only.
    """
    impl: Callable[..., Any] | None = None

    def build() -> Callable[..., Any]:
        # A racing first call may build twice, harmlessly, because plans are deterministic.
        nonlocal impl
        plan: _Plan | None
        try:
            plan = _build_plan(func, sig, from_key)
        except NameError as exc:
            if from_key is not None or _mentions_marker(func):
                raise NameError(
                    f"@inject: cannot resolve type hints for {func.__qualname__}: {exc}. "
                    f"Annotations on an injected function are read at runtime, so the names "
                    f"they use must exist then. A TYPE_CHECKING-only import never will."
                ) from exc
            # Nothing asked for injection, so pass the call through rather than break it.
            plan = None
        built = func if plan is None or plan.empty else _compile_wrapper(func, sig, plan)
        impl = built
        return built

    @wraps(func)
    def deferred(*args: Any, **kwargs: Any) -> Any:
        target = impl if impl is not None else build()
        return target(*args, **kwargs)

    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_deferred(*args: Any, **kwargs: Any) -> Any:
            return await deferred(*args, **kwargs)

        return async_deferred

    return deferred


# A marker aliased under another name escapes this and only loses the clear error.
_MARKER_TOKENS = ("FromCtx", "from_ctx")


def _mentions_marker(func: Callable[..., Any]) -> bool:
    """Report whether any raw annotation of func names a marker."""
    annotations: dict[str, Any] = getattr(func, "__annotations__", {})
    return any(
        isinstance(annotation, str) and any(token in annotation for token in _MARKER_TOKENS)
        for annotation in annotations.values()
    )


def _decorate(obj: Any, from_key: _Key | None) -> Any:
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
    try:
        plan = _build_plan(func, sig, from_key)
    except NameError:
        # Unresolvable forward references, from annotations naming things defined later.
        return _make_deferred(func, sig, from_key)
    if plan.empty:
        return func
    return _compile_wrapper(func, sig, plan)


@overload
def inject(func: F, /) -> F: ...
@overload
def inject(*, from_: str | type[Any] = ...) -> Callable[[F], F]: ...
def inject(func: Any = None, /, *, from_: _Key | None = None) -> Any:
    """Fill missing parameters from the current context at call time.

    Marker style annotates parameters with FromCtx or from_ctx.  Name style,
    @inject(from_="app"), fills every parameter whose name matches an
    attribute of use("app"), defaults included, and skips self and cls.
    Explicitly passed arguments always win, an explicit None included.

    Works on plain and async functions, methods, classmethods and
    staticmethods in either decorator order.  Generator functions are
    rejected, because their bodies run after the call, possibly under
    different providers.
    """
    if from_ is not None and not isinstance(from_, str | type) and not _is_ref(from_):
        raise TypeError(
            f"from_ must be a string name, a class or a ref(), got {type(from_).__name__}"
        )
    if func is None:
        return lambda f: _decorate(f, from_)
    return _decorate(func, from_)


__all__ = ["FromCtx", "from_ctx", "inject", "injected"]
