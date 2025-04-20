# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from .diff_base import (  # noqa: F401
    Diffractometer,
    get_current_diffractometer,
    set_current_diffractometer,
    get_diffractometer_list,
    pprint_diff_settings,
    remove_diff_settings,
)
from .diff_fourc import DiffE4CH, DiffE4CV
from .diff_zaxis import DiffZAXIS
from .diff_k6c import DiffK6C

__CLASS_DIFF = {"E4CH": DiffE4CH, "E4CV": DiffE4CV, "ZAXIS": DiffZAXIS, "K6C": DiffK6C}


def get_diffractometer_class(geometry_name):
    klass = __CLASS_DIFF.get(geometry_name, Diffractometer)
    return klass
