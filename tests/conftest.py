"""Shared pytest setup.

Lets the tests import parsers.py and planning.py from the project folder,
and gives them a helper to read files from tests/samples/.
"""

import os
import sys

TESTS_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(TESTS_DIR))


def read_sample(filename):
    """Return the text of tests/samples/<filename>."""
    with open(os.path.join(TESTS_DIR, "samples", filename)) as f:
        return f.read()
