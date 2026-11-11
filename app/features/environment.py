"""behave hooks. ONE line wires in the whole framework (result store, Allure, screenshots, sessions...).

Need app-specific setup? Define your own hook and call the framework's first, e.g.

    import pyassure.hooks as pa
    def before_all(context):
        pa.before_all(context)
        context.tenant = "acme"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # project root: makes `pyassure` and `app` importable

from pyassure.hooks import *  # noqa: E402,F401,F403
