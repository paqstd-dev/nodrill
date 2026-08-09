"""Sphinx configuration for the nodrill documentation."""

from __future__ import annotations

from importlib.metadata import version as package_version

project = "nodrill"
author = "Pavel Kutsenko"
copyright = "2026, Pavel Kutsenko"

# nodrill is installed alongside the docs group, so the built docs always
# report the version they were built from.
release = package_version("nodrill")
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.extlinks",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
    "sphinx_design",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", ".DS_Store", "Thumbs.db"]

# The 404 page is deliberately outside every toctree.
suppress_warnings = ["toc.not_included"]

# Single backticks mean inline code, the way they read in every other file
# in the repo; broken explicit roles still fail the build under nitpicky.
default_role = "literal"
nitpicky = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
}

extlinks = {
    "issue": ("https://github.com/paqstd-dev/nodrill/issues/%s", "issue #%s"),
    "src": ("https://github.com/paqstd-dev/nodrill/blob/main/%s", "%s"),
}

pygments_style = "github-light-default"
pygments_dark_style = "github-dark"

html_theme = "shibuya"
html_title = "nodrill"
html_static_path = ["_static"]
html_css_files = ["css/tokens.css", "css/theme.css", "css/landing.css"]
html_js_files = ["js/landing.js"]
html_favicon = "_static/img/favicon.svg"
html_logo = "_static/img/logo.svg"
html_copy_source = False

html_theme_options = {
    "accent_color": "teal",
    "color_mode": "auto",
    "dark_code": False,
    "page_layout": "default",
    "github_url": "https://github.com/paqstd-dev/nodrill",
    "globaltoc_expand_depth": 2,
    "toctree_collapse": False,
    "nav_links": [
        {"title": "PyPI", "url": "https://pypi.org/project/nodrill/", "external": True},
        {
            "title": "Issues",
            "url": "https://github.com/paqstd-dev/nodrill/issues",
            "external": True,
        },
    ],
}

# intersphinx already resolves every CPython target on each build, so linkcheck
# skips the host rather than re-requesting it and collecting HTTP 429s.
linkcheck_ignore = [r"https://docs\.python\.org/.*"]
