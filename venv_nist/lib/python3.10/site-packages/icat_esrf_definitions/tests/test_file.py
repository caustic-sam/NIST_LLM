import os
from .. import DEFINITIONS_FILE


def test_file_exists():
    assert os.path.isfile(DEFINITIONS_FILE)
