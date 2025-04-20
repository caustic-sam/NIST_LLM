# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) 2015-2022 Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import os
import re
from bliss.config.conductor.client import get_default_connection

PARSE_VAR = re.compile(r"\$\{[^{}]*\}|[^$]+|\$+?")


def expandvars(path: str) -> str:
    """Expands vars from Beacon keys or from environment variable.

    This can be used with such strings:

    >>> "AAAA ${BEACON:BEACON_KEY} BBBB ${TANGO_HOST}"

    The result will replace the variable syntax with the respective content of
    a key from Beacon or a key from environment variable.

    If the key is not found the string stay unchanged.
    """
    result = []
    beacon = None
    for m in PARSE_VAR.finditer(path):
        s = m.group()
        if s.startswith("${") and s.endswith("}"):
            name = s[2:-1]
            if name.startswith("BEACON:"):
                if beacon is None:
                    beacon = get_default_connection()
                name = name[7:]
                expanded = beacon.get_key(name, None)
            else:
                expanded = os.environ.get(name, None)
            if expanded is None:
                expanded = s
            result.append(expanded)
        else:
            result.append(s)
    return "".join(result)
