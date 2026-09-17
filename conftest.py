"""Put the repository root on `sys.path` so `tests.fixtures` is importable.

The fixture helpers are shared between test modules, so they have to be
importable as `tests.fixtures` rather than copied. pytest inserts the *rootdir*
only under some layouts, and this repo is `src/`-based, so it does not here --
the failure is a bare `ModuleNotFoundError: No module named 'tests'` at
collection, which looks like a missing dependency and is not one.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
