# nodrill

A dependency-free library giving a call tree a scoped context: `provider` sets values, `use()` reads them anywhere below, all on `contextvars`.
Python 3.10+.

## Layout

- `src/nodrill/__init__.py` — the public surface; `__all__` is the API contract, everything in the `_`-prefixed modules is private and free to move.
- `_core.py` — registry, `provider`, `use`, `set_default`, `active`, `Namespace`, `isolate`.
- `_frozen.py` — the `frozen=True` proxy; its forwarded protocols are generated from tables, so a new one is a table entry.
- `_ambient.py` — the ambient `context` namespace.
- `_inject.py` — `@inject`, `FromCtx`/`from_ctx`, the `injected` sentinel.
- `_concurrency.py` — `wrap`, `Executor`.
- `tests/` mirror the feature areas.
- `docs/` is a Sphinx site and part of the gate; a warning there fails the build.
- `benchmarks/bench.py` owns the README's Cost table: it writes the block between the `<!-- benchmarks -->` markers, so the numbers there are never edited by hand.

The guidance that only matters in one place lives in `.claude/rules/` and loads when a matching file is opened: `design-invariants.md` for `src/nodrill/`, `tests.md` for `tests/`, `docs.md` for `docs/`, `workflows.md` for `.github/workflows/`.

## Commands

Everything goes through the `Makefile`; `make help` lists the targets.

```bash
make install    # uv sync --locked plus the pre-commit hooks
make            # the full gate: lint, typecheck, testcov, docs, audit — the order CI runs
make -k         # the same, without stopping at the first failure
make test ARGS="-k inject -x"
make format     # ruff format plus the safe ruff fixes
make bench ARGS=--write   # re-time the hot paths and rewrite the README table
make audit      # zizmor over the GitHub Actions workflows
```

`/check` runs the same gate from here.
The underlying commands are still `uv run pytest`, `uv run mypy`, `uv run pyright`, `uv run ruff …`, `uv run coverage run -m pytest`.

## Conventions

- Ruff runs `select = ["ALL"]`.
  A new ignore goes in `pyproject.toml` with a comment saying why, never as a bare `# noqa` at the call site unless the case is genuinely local.
- Coverage is `fail_under = 100` on branch coverage.
  New code arrives with the tests that cover it; pragmas are not the fix.
- mypy is strict over `src` and `tests`; pyright checks `src`.
  Both must be clean — several API shapes exist only because the two checkers disagree.
- Typing imports stay real imports.
  `get_type_hints()` resolves annotations at runtime, so `TYPE_CHECKING` guards in the public modules would break `@inject` (hence the `TC001`–`TC003` ignores).
- Docstrings are plain PEP 257 prose: imperative first line, no Sphinx roles, no bullet lists.
  Comments explain why, not what.
- Prose is one sentence per line, in `.md` and `.rst` alike, however long the sentence runs.
  There is no column limit and nothing to re-wrap by hand; editing a sentence is a one-line diff.
  No linter checks this — `make lint-md` covers the rest of the Markdown, but the sentence rule is yours to hold.
- Public names are load-bearing.
  `provider`, `use`, `wrap`, `Executor`, `set_default`, `from_ctx` were all reviewed and are fixed; propose renames, do not perform them.

## Release

The version lives in `src/nodrill/__init__.py` and hatch reads it from there.
Pushing a `v*` tag runs `publish.yml`, which builds and uploads to PyPI over Trusted Publishing.
There is no changelog file: release notes go in the GitHub release.

`/release <version>` does the preparation — bump, full gate, local build — and stops before the tag is pushed, because an upload to PyPI cannot be taken back.
