# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import os
import pathlib

from bliss import current_session
from blisswriter.io.os_utils import has_required_disk_space  # noqa F401
from blisswriter.io.os_utils import has_write_permissions  # noqa F401


def makedirs(path: str, exist_ok: bool = True, **kwargs) -> None:
    """When possible use the Bliss session's writer object to
    create the directory. Otherwise use `os.makedirs` but beware
    the current process user will be the owner.
    """
    if current_session and exist_ok and not kwargs:
        if current_session.scan_saving.create_path(path):
            return
        # We are here because the writer is the null writer
        # or the writer has no access to the directory we
        # are trying to create.
    os.makedirs(path, exist_ok=exist_ok, **kwargs)


def is_subdir(child, parent, strict: bool = False) -> bool:
    parent = pathlib.Path(parent).resolve()
    child = pathlib.Path(child).resolve()
    if strict:
        return parent in child.parents
    return child == parent or parent in child.parents
