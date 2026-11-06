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


def log_block(log_file, marker, title):
    """Cut one raw device output block out of a real tool log in tests/samples/real/.

    The logs are FROZEN samples copied from the lab runs; the tests never
    regenerate them. The tool writes each block as
        ----- show vlan brief -----
        <raw output>
        ----- end -----
    marker is any text that comes before the wanted block; the first block
    called title after it is returned. Example:
        log_block("run_20261105_235328.log", "[6/9] Apply SW1 ...", "show vlan brief")
    gives the 'show vlan brief' that verify() read on SW1 in the real apply run.
    """
    after_marker = read_sample("real/" + log_file).split(marker, 1)[1]
    block = after_marker.split(f"----- {title} -----\n", 1)[1]
    return block.split("\n----- end -----", 1)[0]
