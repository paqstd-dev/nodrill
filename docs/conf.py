"""Sphinx configuration for the nodrill documentation."""

from __future__ import annotations

import os
from importlib.metadata import version as package_version

project = "nodrill"
author = "Pavel Kutsenko"
copyright = "2026, Pavel Kutsenko"

# Installed alongside the docs group, so a build reports the version it was built from.
release = package_version("nodrill")
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.extlinks",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", ".DS_Store", "Thumbs.db"]

# Single backticks are inline code here, as they read everywhere else in the repo.
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
html_copy_source = False

# From Read the Docs, since a URL hardcoded here would point every version at latest.
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")

html_theme_options = {
    "accent_color": "teal",
    "color_mode": "auto",
    # Absolute, since a crawler fetches it, and index.rst repeats it for the large preview.
    "og_image_url": (
        "https://raw.githubusercontent.com/paqstd-dev/nodrill/main"
        "/.github/assets/social-preview.png"
    ),
    "light_logo": "_static/img/nodrill-wordmark-light.svg",
    "dark_logo": "_static/img/nodrill-wordmark-dark.svg",
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

# Resolved by intersphinx on every build already, so re-requesting it only collects 429s.
linkcheck_ignore = [r"https://docs\.python\.org/.*"]
