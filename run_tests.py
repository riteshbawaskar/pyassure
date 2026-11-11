#!/usr/bin/env python
"""Entry point for parallel runs:  python run_tests.py --help   (implementation: pyassure/runner.py)"""
import sys

from pyassure.runner import main

if __name__ == "__main__":
    sys.exit(main())
