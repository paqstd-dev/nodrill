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

## The rest of the library

A key is a class or a string, and `use(Config)` is inferred as `Config` under both mypy and pyright.
`provider(instance, key=Repository)` registers under a protocol rather than under the class that happens to implement it, and `ref("myapp.context:Scope")` names a key that lives in a module importing yours.

How the value is handed out is the provider's decision.
`lazy(Cls, factory)` builds it on the first read, and not at all without one.
`frozen=True` gives consumers a read-only view while the block keeps writing, and `extend=True` lets a scope accumulate as the call descends, one layer per block, each unwound on exit.

Reading it is `use()` anywhere below, or `@inject` to put the dependency in the signature where a test can still pass it explicitly.
Tasks inherit the context, `wrap` and `Executor` carry it into threads, and `set_default(Config, factory)` answers a read that ran outside every provider.
Past the edge of the process, where nothing can be carried by reference, `export("trace")` renders the providers you name as a plain dict that JSON holds and `adopt(payload)` opens them again in the worker.

When it goes wrong, `debug()` turns a miss into a diagnosis naming the thread, the task and the line the provider is open on, and `annotate_exceptions()` puts the scope on the traceback.
`isolate()` gives a test fresh context state and rolls it back afterwards.
Everything above is importable from the top-level package, and there is nothing else to import.

## Overhead

A lookup is one dict read on a single `ContextVar`, and nothing is constructed, resolved or cached along the way.
Reading through `use()` costs a little over the parameter it replaces, and `@inject` a little more than that, because it fills the argument before the body runs.
`frozen=True` and `lazy` add a proxy hop to every attribute the consumer touches, and a `ref()` key pays a Python-level hash where a class hashes in C.
A request that reads a provided value a hundred times spends microseconds in nodrill, against hundreds of microseconds for one round trip to a database.

Entering a provider is the expensive end, because it copies the registry so that sibling tasks stay isolated.
That copy is proportional to how many providers are open, and it happens once per scope rather than once per lookup.
An extending layer copies the enclosing namespace on top of that, which is what keeps a sibling task from seeing a layer opened after it started.

The numbers, the machine that produced them and the script that timed them are on the [performance page](https://nodrill.readthedocs.io/en/latest/content/misc/performance.html), and `make bench ARGS=--write` runs it on yours.

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
