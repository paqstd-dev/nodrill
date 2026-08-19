"""What every view over a provided value shares.

Special methods are looked up on the type, never through __getattr__, so every
protocol a view forwards has to exist on its class.  The tables here are that
list, written once and generated into _FrozenProxy, _LazyCell and _SealedProxy
rather than into three hand-written sets, where a gap stays invisible until
something prints the wrapper out of an f-string.

_FORWARDED is what any view delegates.  _ITEM_WRITES and _INPLACE are the
writes a view forwards when it restricts when rather than what, which is why
_frozen refuses them and the other two do not.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable
from typing import Any


def _call(target: Any, *args: Any, **kwargs: Any) -> Any:
    return target(*args, **kwargs)


_FORWARDED: dict[str, Callable[..., Any]] = {
    "__str__": str,
    "__bytes__": bytes,
    "__format__": format,
    "__bool__": bool,
    "__int__": int,
    "__float__": float,
    "__complex__": complex,
    "__index__": operator.index,
    "__round__": round,
    "__floor__": math.floor,
    "__ceil__": math.ceil,
    "__trunc__": math.trunc,
    "__abs__": abs,
    "__neg__": operator.neg,
    "__pos__": operator.pos,
    "__invert__": operator.invert,
    "__len__": len,
    "__iter__": iter,
    "__reversed__": reversed,
    "__next__": next,
    "__contains__": operator.contains,
    "__getitem__": operator.getitem,
    "__call__": _call,
    # __eq__ and __ne__ sit in the class body, so a target's own answer is not coerced to bool.
    "__lt__": operator.lt,
    "__le__": operator.le,
    "__gt__": operator.gt,
    "__ge__": operator.ge,
    "__add__": operator.add,
    "__sub__": operator.sub,
    "__mul__": operator.mul,
    "__matmul__": operator.matmul,
    "__truediv__": operator.truediv,
    "__floordiv__": operator.floordiv,
    "__mod__": operator.mod,
    "__divmod__": divmod,
    "__pow__": pow,
    "__lshift__": operator.lshift,
    "__rshift__": operator.rshift,
    "__and__": operator.and_,
    "__or__": operator.or_,
    "__xor__": operator.xor,
}

_REFLECTED: dict[str, Callable[[Any, Any], Any]] = {
    "__radd__": operator.add,
    "__rsub__": operator.sub,
    "__rmul__": operator.mul,
    "__rmatmul__": operator.matmul,
    "__rtruediv__": operator.truediv,
    "__rfloordiv__": operator.floordiv,
    "__rmod__": operator.mod,
    "__rdivmod__": divmod,
    "__rpow__": pow,
    "__rlshift__": operator.lshift,
    "__rrshift__": operator.rshift,
    "__rand__": operator.and_,
    "__ror__": operator.or_,
    "__rxor__": operator.xor,
}

# The async ones hand back the target's own awaitable, so none of these needs async def.
_INVOKED = (
    "__enter__",
    "__exit__",
    "__aenter__",
    "__aexit__",
    "__await__",
    "__aiter__",
    "__anext__",
)

# The two _BLOCKED refuses, for the views that forward them instead.
_ITEM_WRITES: dict[str, Callable[..., Any]] = {
    "__setitem__": operator.setitem,
    "__delitem__": operator.delitem,
}

# Apart, since an in-place operator hands back what the caller's name is rebound to.
_INPLACE: dict[str, Callable[[Any, Any], Any]] = {
    "__iadd__": operator.iadd,
    "__isub__": operator.isub,
    "__imul__": operator.imul,
    "__imatmul__": operator.imatmul,
    "__itruediv__": operator.itruediv,
    "__ifloordiv__": operator.ifloordiv,
    "__imod__": operator.imod,
    "__ipow__": operator.ipow,
    "__ilshift__": operator.ilshift,
    "__irshift__": operator.irshift,
    "__iand__": operator.iand,
    "__ior__": operator.ior,
    "__ixor__": operator.ixor,
}


class _View:
    """Base of the views that wrap a target, so one can be recognised as one.

    Empty slots, since the target belongs to the concrete class and a second
    slotted base would conflict with the layout it already has.
    """

    __slots__ = ()

    _nodrill_target: Any


def _unwrapped(value: Any) -> Any:
    """Return what a view covers, for a check that has to reach the target.

    A lazy cell is not a view by this definition and is left alone, since
    reaching through it would resolve the value the caller is asking about.
    """
    while isinstance(value, _View):
        value = value._nodrill_target  # noqa: SLF001
    return value


__all__ = [
    "_FORWARDED",
    "_INPLACE",
    "_INVOKED",
    "_ITEM_WRITES",
    "_REFLECTED",
    "_View",
    "_unwrapped",
]
