"""Pytest shared setup for the AML thesis test suite.

Loads the repo-root .env file into os.environ before any test module
is collected, so live tests (which gate on ANTHROPIC_API_KEY) and the
Anthropic SDK both see the key — without it ever being committed or
exported into the shell by hand. The .env file stays gitignored; this
is the single place the key needs to live.

Keys already present in the real environment are NOT overridden, so
an explicit `export` (or CI-injected secret) still wins over the file.

Loading itself lives in aml.utils.env.load_dotenv_if_present — same
implementation used by the run_campaign and run_benign CLIs, so the
parser behaves identically across all entry points.
"""
from __future__ import annotations

from pathlib import Path

from aml.utils.env import load_dotenv_if_present


_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

# Runs at conftest import time, before pytest collects any test
# module — so @needs_api_key skipif markers see the loaded key.
load_dotenv_if_present(_ENV_FILE)
