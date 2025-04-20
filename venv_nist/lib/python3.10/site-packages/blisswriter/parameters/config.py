"""Save options for a writer which relies on extra metadata published by Bliss"""

from typing import Dict, Any
from . import base

cli_saveoptions = dict(base.cli_saveoptions)
cli_saveoptions["stackmca"] = {
    "dest": "stack_mcas",
    "action": "store_true",
    "help": "Merged MCA datasets in application definition",
}


def default_saveoptions() -> Dict[str, Any]:
    return base.extract_default_saveoptions(cli_saveoptions)
