"""Pytest shared setup for the AML thesis test suite.

Auto-loads the repo-root .env file into os.environ before any test module is
collected, so the live tests (which gate on ANTHROPIC_API_KEY) and the
Anthropic SDK both see the key — without it ever being committed or exported
into the shell by hand. The .env file stays gitignored; this is the single
place the key needs to live.

Keys already present in the real environment are NOT overridden, so an
explicit `export` (or CI-injected secret) still wins over the file.

This module has no third-party dependency on purpose — it's a tiny KEY=VALUE
parser, not python-dotenv, so the test suite stays importable in a minimal
environment.
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _load_dotenv() -> None:
    """Populate os.environ from the repo-root .env (without overriding)."""
    if not _ENV_FILE.exists():
        return
    for raw in _ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Tolerate `export KEY=VALUE` style lines.
        if key.startswith("export "):
            key = key[len("export "):].strip()
        # Strip matching surrounding quotes from the value.
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # Real environment wins — never override an already-set key.
        if key and key not in os.environ:
            os.environ[key] = value


# Runs at conftest import time, before pytest collects any test module — so
# the @needs_api_key skipif markers see the loaded key.
_load_dotenv()
