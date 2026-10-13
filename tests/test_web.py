"""Static checks on the dashboard's JavaScript."""

import re
from pathlib import Path

VIEWS = Path(__file__).resolve().parent.parent / "web" / "js" / "views"


def test_views_use_null_safe_dom_helpers():
    """Raw append/replaceChildren print a skipped (null) section as the text "null"; views use add()/fill()/h()."""
    raw = re.compile(r"\.(append|prepend|replaceChildren)\(")
    offenders = [
        f"{path.name}:{n}"
        for path in VIEWS.glob("*.js")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if raw.search(line)
    ]
    assert offenders == []
