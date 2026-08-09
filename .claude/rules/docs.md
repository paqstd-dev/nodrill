---
paths:
  - "docs/**"
---

# Documentation

`make docs` runs `sphinx-build -W --keep-going`, so a warning is a failed build, and `nitpicky = True` turns an unresolvable cross-reference into one.
Every `:func:`, `:class:`, `:exc:`, `:doc:` and `:ref:` target has to exist before the change is done.

- The four sections under `content/` are Diátaxis, and a page belongs to exactly one: `intro/` gets somebody running, `topics/` explains one concept systematically, `howto/` is a task-shaped recipe, `ref/` is the API contract.
  `misc/` is project matter — scope, design, FAQ, contributing.
- A new page goes into the `toctree` of its section index.
  `docs/404.rst` is the only page outside every toctree, which is what `:orphan:` and `suppress_warnings = ["toc.not_included"]` cover.
- Pages open with a `.. _<section>-<page>:` label, so anything else can `:ref:` them.
- `default_role = "literal"`: single backticks are inline code, the same as in the README and the docstrings.
  There is no reason to write ``:code:`…` ``.
- One sentence per line, however long it runs, and no column limit.
  Structure still owns the indentation: a wrapped `:param:` body, a definition body and a directive's second signature line are not prose and stay where they are.
- How-to code is a whole program that runs as pasted against the current API; reference pages show fragments.
  Either way the snippet is checked against the real signatures, not remembered ones.
- Links back into the repository go through the `extlinks` roles, `:issue:` and `:src:`.
  `linkcheck` runs on its own and never fails the build — link rot is somebody else's outage.
- `docs/index.rst` is the landing page: its body is not rendered, so prose added there is invisible.
  The markup lives in `_templates/landing/` and `_templates/layout/landing.html`.
- CSS is split by job.
  `tokens.css` holds the `--nd-*` variables, `theme.css` layers over shibuya, `landing.css` is scoped under `.nd-landing`.
  Add a token before adding a hex value.
