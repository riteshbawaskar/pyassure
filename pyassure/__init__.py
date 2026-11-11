"""PyAssure - a Quality Assurance framework in Python for UI, API and Database Testing.

Layout contract
  pyassure/  FRAMEWORK code. Generic and reusable; you should never need to edit it.
  app/       APPLICATION code: page objects, API clients, login/session providers, scenarios,
             datasets and config. This is the ONLY place a project team changes.
"""
from pyassure.branding import NAME, TAGLINE, VERSION

__version__ = VERSION
__all__ = ["NAME", "TAGLINE", "VERSION", "__version__"]
