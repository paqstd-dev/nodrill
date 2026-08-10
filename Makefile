.DEFAULT_GOAL := all

UV ?= uv
UVX ?= uvx
RUN := $(UV) run
DOCS_OUT := docs/_build
ZIZMOR_VERSION := 1.29.0
PYMARKDOWN_VERSION := 0.9.39

# Everything git knows about, so a new file is linted before it is committed and
# nothing under .gitignore — .venv above all — is ever scanned.
MD_FILES := $(shell git ls-files --cached --others --exclude-standard '*.md')

# Extra arguments for the targets that take them, e.g. `make test ARGS="-k provider -x"`.
ARGS ?=

.PHONY: .uv  ## Check that uv is installed
.uv:
	@$(UV) --version >/dev/null || (echo 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'; exit 1)

.PHONY: install  ## Install the locked environment and the pre-commit hooks
install: .uv
	$(UV) sync --locked
	$(UVX) pre-commit install --install-hooks

.PHONY: sync  ## Install the locked environment
sync: .uv
	$(UV) sync --locked

.PHONY: lock  ## Rebuild the lockfile, upgrading every pinned dev tool
lock: .uv
	$(UV) lock --upgrade

.PHONY: format  ## Format the code and apply the safe ruff fixes
format: .uv
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

.PHONY: lint  ## Lint and check formatting, the way CI does
lint: .uv
	$(RUN) ruff format --check .
	$(RUN) ruff check $(ARGS) .

.PHONY: lint-md  ## Lint the Markdown prose
lint-md:
	$(UVX) pymarkdownlnt@$(PYMARKDOWN_VERSION) --strict-config scan $(MD_FILES)

.PHONY: typecheck  ## Run mypy and pyright
typecheck: .uv
	$(RUN) mypy
	$(RUN) pyright

.PHONY: test  ## Run the test suite
test: .uv
	$(RUN) pytest $(ARGS)

.PHONY: testcov  ## Run the tests under coverage and enforce the 100% gate
testcov: .uv
	$(RUN) coverage run -m pytest
	@$(RUN) coverage report

.PHONY: testcov-html  ## Write the HTML coverage report to htmlcov/
testcov-html: testcov
	$(RUN) coverage html

.PHONY: docs  ## Build the HTML docs with warnings as errors
docs: .uv
	$(UV) run --group docs sphinx-build -W --keep-going -b html docs $(DOCS_OUT)/html

.PHONY: docs-serve  ## Serve the docs with live reload
docs-serve: .uv
	$(UV) run --group docs --with sphinx-autobuild sphinx-autobuild docs $(DOCS_OUT)/html

.PHONY: linkcheck  ## Check the external links; a failure here is somebody else's outage
linkcheck: .uv
	-$(UV) run --group docs sphinx-build -b linkcheck docs $(DOCS_OUT)/linkcheck

.PHONY: audit  ## Audit the GitHub Actions workflows with zizmor
audit: .uv
	$(UVX) zizmor@$(ZIZMOR_VERSION) .

.PHONY: bench  ## Time the hot paths, and rewrite the performance page with ARGS="--write"
bench: .uv
	$(RUN) python benchmarks/bench.py $(ARGS)

.PHONY: build  ## Build the sdist and wheel, then check the metadata
build: .uv
	$(UV) build
	$(UVX) twine check --strict dist/*

.PHONY: pre-commit  ## Run every pre-commit hook over the whole tree
pre-commit: .uv
	$(UVX) pre-commit run --all-files

# `make -k all` keeps going after a failure, so one run reports everything CI would.
.PHONY: all  ## Run the full gate the way CI does
all: sync lint lint-md typecheck testcov docs audit

.PHONY: clean  ## Remove the build output, caches and coverage data
clean:
	rm -rf $(DOCS_OUT) dist htmlcov .coverage .coverage.* coverage.xml \
		.pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

.PHONY: help  ## List the targets
help:
	@grep -E '^\.PHONY: [a-z][a-z-]*  ## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ".PHONY: |  ## "}; {printf "\033[36m%-14s\033[0m %s\n", $$2, $$3}'
