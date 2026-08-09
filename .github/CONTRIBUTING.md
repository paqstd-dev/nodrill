# Contributing to nodrill

Thanks for taking the time.
Bug reports, documentation fixes and small focused pull requests are all welcome.

For anything larger than a bug fix, please open an issue first and describe the problem you are solving. nodrill is deliberately small, and the [scope page](https://nodrill.readthedocs.io/en/latest/content/misc/scope.html) says what it will and will not grow into.

## Setup

The project uses [uv](https://docs.astral.sh/uv/) with a committed lockfile.

```bash
git clone https://github.com/paqstd-dev/nodrill
cd nodrill
make install
```

`make install` syncs the locked environment and installs the pre-commit hooks.
Everything else runs through the `Makefile`; `make help` lists the targets.

## The gate

```bash
make          # lint, typecheck, coverage, docs, workflow audit — the same order CI runs
make -k       # same, but keep going after a failure so one run reports everything
```

Individual pieces, when you want a faster loop:

| Command | What it does |
| --- | --- |
| `make format` | ruff format plus the safe ruff fixes |
| `make lint` | ruff format `--check` and `ruff check` |
| `make typecheck` | mypy and pyright |
| `make test` | pytest; `make test ARGS="-k inject -x"` to narrow it |
| `make testcov` | pytest under coverage with the 100 percent gate |
| `make docs` | Sphinx with warnings as errors |
| `make audit` | zizmor over the GitHub Actions workflows |

A pull request is expected to pass all of it.

## What the gate enforces

- **Coverage is 100 percent on branches.**
  New code arrives with the tests that cover it.
  A `# pragma: no cover` is not the fix.
- **Two type checkers.** mypy runs strict over `src` and `tests`; pyright checks `src`.
  Both must be clean — a few API shapes exist only because the two disagree.
- **Ruff with `select = ["ALL"]`.**
  A new ignore goes in `pyproject.toml` with a comment saying why, rather than a bare `# noqa` at the call site.
- **Docs build with `-W`.**
  A Sphinx warning fails the build here and on Read the Docs.

## House style

Docstrings are plain PEP 257 prose: an imperative first line, no reStructuredText roles, no bullet lists.
The reference pages carry the detailed descriptions; docstrings stay terse.
Comments explain why, not what.

Prose in `.md` and `.rst` files uses semantic line breaks: a new sentence starts a new line, so rewording a paragraph shows up as a one-line diff.
There is no column limit: a sentence stays on one line however long it runs, and nothing is re-wrapped by hand.
`make lint-md` checks the rest of the Markdown — headings, lists, blank lines — but no tool can check the sentence rule, so that one rides on review.

Public names are load-bearing: `provider`, `use`, `wrap`, `Executor`, `set_default` and `from_ctx` were reviewed and are fixed.
Propose a rename in an issue; please do not perform one in a pull request.

## Reporting a bug

Open an issue with the smallest program that reproduces it, the Python version, and whether threads or asyncio are involved.
Context bugs are almost always about which context a piece of code ran in, so say where the provider was entered and where the lookup happened.

For a security issue, follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## AI-assisted contributions

Using an assistant to write a patch is fine.
Submitting one you have not read, run and understood is not: you are the author of the pull request, and review time is the scarce resource here.
Say so in the description if a change was largely machine-generated, and make sure the tests genuinely exercise the behaviour rather than restating the implementation.
