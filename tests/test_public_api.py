from pathlib import Path

import pytest

import nodrill

REFERENCE = Path(__file__).resolve().parent.parent / "docs" / "content" / "ref" / "index.rst"


def documented() -> list[str]:
    """Return the names the reference page's import block promises."""
    text = REFERENCE.read_text(encoding="utf-8")
    block = text.split("from nodrill import (", 1)[1].split(")", 1)[0]
    return sorted(line.strip().rstrip(",") for line in block.splitlines() if line.strip())


@pytest.mark.skipif(not REFERENCE.exists(), reason="no docs tree, as in an sdist")
def test_the_reference_page_lists_every_public_name() -> None:
    """A name added to __all__ without a reference entry fails here rather than shipping."""
    # __version__ has its own directive on that page, being data rather than an import.
    exported = sorted(name for name in nodrill.__all__ if name != "__version__")
    assert documented() == exported
