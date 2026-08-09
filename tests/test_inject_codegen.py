"""The compiled wrapper as an artifact: its source, its names, its lifetime."""

import gc
import linecache
import weakref
from dataclasses import dataclass
from inspect import Parameter, Signature
from types import FunctionType
from typing import Any

import pytest

from nodrill import (
    FromCtx,
    FrozenContextError,
    NoProviderError,
    inject,
    injected,
    provider,
    set_default,
    use,
)


@dataclass
class Db:
    dsn: str = "pg://"


def make_wrapper() -> FunctionType:
    @inject
    def handler(db: FromCtx[Db] = injected) -> str:
        return db.dsn

    assert isinstance(handler, FunctionType)
    return handler


def generated_source(wrapper: FunctionType) -> str:
    return "".join(linecache.getlines(wrapper.__code__.co_filename))


class TestSourceLifetime:
    def test_filenames_stay_distinct_after_clearcache(self) -> None:
        first = make_wrapper()
        linecache.clearcache()
        second = make_wrapper()
        assert first.__code__.co_filename != second.__code__.co_filename

    def test_source_is_evicted_with_the_wrapper(self) -> None:
        handler = make_wrapper()
        filename = handler.__code__.co_filename
        assert filename in linecache.cache
        del handler
        gc.collect()
        assert filename not in linecache.cache

    def test_wrapper_is_freed_without_the_cycle_collector(self) -> None:
        handler = make_wrapper()
        ref = weakref.ref(handler)
        gc.disable()
        try:
            del handler
            assert ref() is None
        finally:
            gc.enable()


class TestGeneratedNames:
    def test_defaults_expose_the_injected_sentinel(self) -> None:
        @inject
        def handler(tag: str, db: FromCtx[Db] = injected) -> str:
            return db.dsn

        assert isinstance(handler, FunctionType)
        assert handler.__defaults__ == (injected,)

    def test_keyword_parameter_name_is_rejected(self) -> None:
        def base(*args: Any, **kwargs: Any) -> Any: ...

        base.__signature__ = Signature(  # type: ignore[attr-defined]
            [
                Parameter("from", Parameter.POSITIONAL_ONLY),
                Parameter(
                    "cfg", Parameter.POSITIONAL_OR_KEYWORD, default=injected, annotation=FromCtx[Db]
                ),
            ]
        )
        base.__annotations__ = {"cfg": FromCtx[Db]}
        with pytest.raises(TypeError, match="'from'"):
            inject(base)

    def test_debug_named_function_falls_back_to_the_mangled_name(self) -> None:
        def handler(db: FromCtx[Db] = injected) -> str:
            return db.dsn

        handler.__name__ = "__debug__"
        wrapped = inject(handler)
        with provider(Db()):
            assert wrapped() == "pg://"

    def test_prefix_named_function_does_not_shadow_the_helpers(self) -> None:
        def _nd_handler(db: FromCtx[Db] = injected) -> str:
            return db.dsn

        wrapped = inject(_nd_handler)
        with provider(Db()):
            assert wrapped() == "pg://"


class TestResolutionParity:
    """The wrapper's inlined lookup and use() must answer identically."""

    def test_hit_returns_the_same_object(self) -> None:
        handler = make_wrapper()
        with provider(Db()):
            assert handler() == use(Db).dsn

    def test_frozen_view_reaches_the_wrapper(self) -> None:
        @inject
        def grab(db: FromCtx[Db] = injected) -> Db:
            return db

        with provider(Db(), frozen=True):
            view = grab()
            assert view is use(Db)
            with pytest.raises(FrozenContextError):
                view.dsn = "x"

    def test_set_default_reaches_the_wrapper(self) -> None:
        set_default(Db, lambda: Db(dsn="fallback://"))

        @inject
        def grab(db: FromCtx[Db] = injected) -> Db:
            return db

        assert grab().dsn == "fallback://"
        assert use(Db).dsn == "fallback://"

    def test_miss_error_matches_use(self) -> None:
        handler = make_wrapper()
        with pytest.raises(NoProviderError) as via_wrapper:
            handler()
        with pytest.raises(NoProviderError) as via_use:
            use(Db)
        assert str(via_wrapper.value) == str(via_use.value)


class TestGoldenSource:
    """The generated text itself, since coverage cannot see inside it."""

    def test_marker_wrapper(self) -> None:
        @inject
        def handler(request: str, db: FromCtx[Db] = injected) -> str:
            return db.dsn

        assert isinstance(handler, FunctionType)
        assert generated_source(handler) == (
            "def handler(request, db=_nd_injected):\n"
            "    if db is _nd_injected:\n"
            "        db = _nd_registry().get(_nd_key_db, _nd_omitted)\n"
            "        if db is _nd_omitted:\n"
            "            db = _nd_miss(_nd_key_db)\n"
            "    return _nd_func(request, db)"
        )

    def test_missing_guard_precedes_resolution(self) -> None:
        @inject
        def handler(db: FromCtx[Db], tag: str) -> str:
            return tag

        assert isinstance(handler, FunctionType)
        assert generated_source(handler) == (
            "def handler(db=_nd_injected, tag=_nd_injected):\n"
            "    if tag is _nd_injected:\n"
            "        raise _nd_missing_error(_nd_label, (('tag', tag),))\n"
            "    if db is _nd_injected:\n"
            "        db = _nd_registry().get(_nd_key_db, _nd_omitted)\n"
            "        if db is _nd_omitted:\n"
            "            db = _nd_miss(_nd_key_db)\n"
            "    return _nd_func(db, tag)"
        )

    def test_by_name_wrapper(self) -> None:
        @inject(from_="app")
        def render(user: str, lang: str = "en") -> str:
            return f"{user}/{lang}"

        assert isinstance(render, FunctionType)
        assert generated_source(render) == (
            "def render(user=_nd_injected, lang=_nd_injected):\n"
            "    _nd_source = _nd_omitted\n"
            "    _nd_unmet = []\n"
            "    if user is _nd_injected:\n"
            "        if _nd_source is _nd_omitted:\n"
            "            _nd_source = _nd_registry().get(_nd_from_key, _nd_omitted)\n"
            "            if _nd_source is _nd_omitted:\n"
            "                _nd_source = _nd_miss(_nd_from_key)\n"
            "        user = _nd_getattr(_nd_source, 'user', _nd_omitted)\n"
            "        if user is _nd_omitted:\n"
            "            _nd_unmet.append('user')\n"
            "    if lang is _nd_injected:\n"
            "        if _nd_source is _nd_omitted:\n"
            "            _nd_source = _nd_registry().get(_nd_from_key, _nd_omitted)\n"
            "            if _nd_source is _nd_omitted:\n"
            "                _nd_source = _nd_miss(_nd_from_key)\n"
            "        lang = _nd_getattr(_nd_source, 'lang', _nd_omitted)\n"
            "        if lang is _nd_omitted:\n"
            "            lang = _nd_default_lang\n"
            "    if _nd_unmet:\n"
            "        raise _nd_unmet_error(_nd_label, _nd_from_key, _nd_unmet)\n"
            "    return _nd_func(user, lang)"
        )
