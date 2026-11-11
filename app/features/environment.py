"""behave hooks. ONE line wires in the whole framework (result store, Allure, screenshots, sessions...).

Need app-specific setup? Define your own hook and call the framework's first, e.g.

    import pyassure.hooks as pa
    def before_all(context):
        pa.before_all(context)
        context.tenant = "acme"
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PYASSURE_ROOT", Path(__file__).resolve().parents[2])).expanduser().absolute()
os.environ.setdefault("PYASSURE_APP_DIR", str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(PROJECT_ROOT))   # project root: makes `pyassure` and `app` importable

from pyassure.hooks import *  # noqa: E402,F401,F403
