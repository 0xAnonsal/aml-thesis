"""Shared .env file loader — stdlib only, no python-dotenv dependency.

Used by both pytest's conftest and the CLI runners (run_campaign,
run_benign) so they all parse the file the same way. Replaces an
earlier split where conftest had its own parser and the CLIs imported
python-dotenv. Single implementation here keeps behaviour consistent
across entry points.

Public surface:
    load_dotenv_if_present(path=None) -> None
        Populate os.environ from a .env file. No-op if the file
        doesn't exist. Already-set env vars are NEVER overridden
        (explicit shell exports / CI secrets always win).

Syntax handled:
    - blank lines and `#` full-line comments
    - `KEY=value`
    - `export KEY=value` (bash-style)
    - quoted values (`KEY="value"` or `KEY='value'`)
    - INLINE comments after unquoted values: `KEY=value # explanation`
      → the trailing comment is stripped; KEY ends up as "value"

By design, NOT handled (would add complexity for negligible benefit):
    - multi-line values
    - `${OTHER_VAR}` substitution
    - backslash escapes inside values

If the file is missing or the path argument doesn't exist, this is a
silent no-op — callers can blindly invoke it without checking.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_if_present(path: Path | str | None = None) -> None:
    """Populate os.environ from a .env file (without overriding existing keys).

    `path`: explicit path to the .env file. If None, walks up from the
    current working directory looking for a `.env` file and uses the
    first one found.
    """
    if path is None:
        cwd = Path.cwd().resolve()
        path = None
        for parent in [cwd, *cwd.parents]:
            candidate = parent / ".env"
            if candidate.exists():
                path = candidate
                break
        if path is None:
            return
    else:
        path = Path(path).resolve()
        if not path.exists():
            return

    try:
        text = path.read_text()
    except OSError:
        return

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        # Tolerate `export KEY=value` style lines.
        if key.startswith("export "):
            key = key[len("export "):].strip()

        value = value.strip()
        # Strip inline comment (only when value isn't quoted — quoted
        # values may legitimately contain `#`). Require a space or tab
        # before the `#` so URLs with fragments don't get truncated.
        if value and value[0] not in ("'", '"'):
            for sep in (" #", "\t#"):
                idx = value.find(sep)
                if idx != -1:
                    value = value[:idx].rstrip()
                    break

        # Strip matching surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        # Real environment wins — never override an already-set key.
        if key and key not in os.environ:
            os.environ[key] = value
