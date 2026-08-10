![nodrill](https://raw.githubusercontent.com/paqstd-dev/nodrill/main/docs/_static/img/nodrill-wordmark.svg)

[![PyPI version](https://img.shields.io/pypi/v/nodrill)](https://pypi.org/project/nodrill/)
[![PyPI Supported Python Versions](https://img.shields.io/pypi/pyversions/nodrill.svg)](https://pypi.org/project/nodrill/)
[![CI](https://github.com/paqstd-dev/nodrill/actions/workflows/ci.yml/badge.svg)](https://github.com/paqstd-dev/nodrill/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/paqstd-dev/nodrill/graph/badge.svg)](https://codecov.io/gh/paqstd-dev/nodrill)

nodrill gives a call tree a shared, scoped context. Values set in a `provider` block are visible to any function below it through `use()`, without being passed through the signatures in between.
It is built on `contextvars`, so lookups are thread-safe and asyncio-task-safe, and it has no dependencies.

```python
from dataclasses import dataclass

from nodrill import provider, use


@dataclass
class RequestScope:
    user_id: int
    db: str


def handle_request():
    with provider(RequestScope(user_id=42, db="postgres://...")):
        render_page()


def render_page():
    return render_sidebar()  # knows nothing about RequestScope


def render_sidebar():
    scope = use(RequestScope)  # inferred as RequestScope
    return f"{scope.user_id} @ {scope.db}"
```

String names work too, when a typed key is more than the case needs.

```python
with provider("app", db=engine) as ctx:
    ctx.user_id = 42
    handle()  # any callee reads use("app").db
```

## What you get

- Typed keys, so `use(Config)` is inferred as `Config` under both mypy and pyright.
- String namespaces for the values that do not deserve a class, as above.
- `@inject` to declare the dependency in the signature and still pass it explicitly in a test.
- `set_default(Config, factory)` for code that has to run outside any provider.
- Threads and asyncio, where tasks inherit the context and `wrap` and `Executor` carry it into threads.
- `frozen=True` hands consumers a read-only view while the block keeps a writable object.
- `extend=True` for a scope that accumulates as the call descends, one layer per `with` block, each unwound on exit.
- `lazy(Cls, factory)` for a value that costs something to build and only some requests read.
- `ref("myapp.context:RequestScope")` for a key that lives in a module you cannot import, because it imports yours.
- `isolate()` to give a test fresh context state and roll everything back after it.
- `debug()` to turn a miss into a diagnosis, naming the thread, the task and the line the provider is open on.
- `annotate_exceptions()` to put the scope on the traceback, so a failure five frames down says which request it was.
- No dependencies, Python 3.10 and up, and a small public API, all of it importable from the top-level package.

## Cost

A lookup is one dict read on a single `ContextVar`, and nothing is constructed, resolved or cached along the way.
Reading through `use()` costs a little over the parameter it replaces, and `@inject` costs a little more than that, because it fills the argument before the body runs.
`frozen=True` and `lazy` add a proxy hop to every attribute the consumer touches, and a `ref()` key pays for a Python-level hash where a class hashes in C.

Entering a provider is the expensive end, because it copies the registry so that sibling tasks stay isolated.
That copy is proportional to how many providers are open, and it happens once per scope rather than once per lookup.
An extending layer copies the enclosing namespace on top of that, which is what keeps a sibling task from seeing a layer opened after it started.

Debug mode is not for the hot path, and exception notes run nothing at all until an exception is already leaving a block.

Measured numbers, the machine they were measured on, and the script that produced them are on the [performance page](https://nodrill.readthedocs.io/en/latest/content/misc/performance.html).
`make bench ARGS=--write` measures on your own machine, which is the only measurement that answers whether this is fast enough for you.

## Install

```bash
pip install nodrill
```

Python 3.10 or newer.

## Documentation

Full documentation is at <https://nodrill.readthedocs.io/>.

Start with the [tutorial](https://nodrill.readthedocs.io/en/latest/content/intro/quickstart.html), which covers the whole library in about ten minutes, over typed class keys, fallbacks for a miss, `@inject`, threads and asyncio, frozen providers, and testing.

## Contributing

Bug reports and small focused pull requests are welcome. See [CONTRIBUTING.md](https://github.com/paqstd-dev/nodrill/blob/main/.github/CONTRIBUTING.md).
`make install` sets up the environment, and `make` runs the same gate CI does.

Security issues go through a [private advisory](https://github.com/paqstd-dev/nodrill/security/advisories/new) rather than the issue tracker.

## License

MIT
