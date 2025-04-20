"""Save options for a writer which does not rely on extra metadata published by Bliss"""

from typing import Dict, Any
import enum


@enum.unique
class PROFILE_PARAMETERS(enum.IntEnum):
    OFF = enum.auto()
    CPU30 = enum.auto()
    CPU50 = enum.auto()
    CPU100 = enum.auto()
    WALL30 = enum.auto()
    WALL50 = enum.auto()
    WALL100 = enum.auto()
    MEM30 = enum.auto()
    MEM50 = enum.auto()
    MEM100 = enum.auto()

    @property
    def arguments(self) -> dict:
        return _PROFILE_ARGUMENTS[self]


_PROFILE_ARGUMENTS = {
    PROFILE_PARAMETERS.OFF: {},
    PROFILE_PARAMETERS.CPU30: {
        "memory": False,
        "time": True,
        "clock": "cpu",
        "timelimit": 30,
    },
    PROFILE_PARAMETERS.CPU50: {
        "memory": False,
        "time": True,
        "clock": "cpu",
        "timelimit": 50,
    },
    PROFILE_PARAMETERS.CPU100: {
        "memory": False,
        "time": True,
        "clock": "cpu",
        "timelimit": 100,
    },
    PROFILE_PARAMETERS.WALL30: {
        "memory": False,
        "time": True,
        "clock": "wall",
        "timelimit": 30,
    },
    PROFILE_PARAMETERS.WALL50: {
        "memory": False,
        "time": True,
        "clock": "wall",
        "timelimit": 50,
    },
    PROFILE_PARAMETERS.WALL100: {
        "memory": False,
        "time": True,
        "clock": "wall",
        "timelimit": 100,
    },
    PROFILE_PARAMETERS.MEM30: {"memory": True, "time": False, "memlimit": 30},
    PROFILE_PARAMETERS.MEM50: {"memory": True, "time": False, "memlimit": 50},
    PROFILE_PARAMETERS.MEM100: {"memory": True, "time": False, "memlimit": 100},
}


def _resource_profiling_from_string(s):
    try:
        return PROFILE_PARAMETERS[s.upper()]
    except KeyError as e:
        raise ValueError(str(e))


cli_saveoptions = {
    "keepshape": {
        "dest": "flat",
        "action": "store_false",
        "help": "Keep shape of multi-dimensional grid scans",
    },
    "multivalue_positioners": {
        "dest": "multivalue_positioners",
        "action": "store_true",
        "help": "Group positioners values",
    },
    "enable_external_nonhdf5": {
        "dest": "allow_external_nonhdf5",
        "action": "store_true",
        "help": "Enable external non-hdf5 files like edf (ABSOLUTE LINK!)",
    },
    "disable_external_hdf5": {
        "dest": "allow_external_hdf5",
        "action": "store_false",
        "help": "Disable external hdf5 files (virtual datasets)",
    },
    "copy_non_external": {
        "dest": "copy_non_external",
        "action": "store_true",
        "help": "Copy data instead of saving the uri when external linking is disabled",
    },
    "resource_profiling": {
        "dest": "resource_profiling",
        "default": PROFILE_PARAMETERS.OFF,
        "type": _resource_profiling_from_string,
        "choices": list(PROFILE_PARAMETERS),
        "help": "Enable resource profiling",
    },
    "required_disk_space": {
        "dest": "required_disk_space",
        "default": 200,
        "type": int,
        "help": "Required disk space in MB",
    },
    "recommended_disk_space": {
        "dest": "recommended_disk_space",
        "default": 1024,
        "type": int,
        "help": "Recommended disk space in MB",
    },
}


def extract_default_saveoptions(cli_saveoptions: Dict[str, dict]) -> Dict[str, Any]:
    saveoptions = {}
    for options in cli_saveoptions.values():
        if "default" in options:
            v = options["default"]
        else:
            v = options["action"] == "store_false"
        saveoptions[options["dest"]] = v
    return saveoptions


def default_saveoptions() -> Dict[str, Any]:
    return extract_default_saveoptions(cli_saveoptions)
