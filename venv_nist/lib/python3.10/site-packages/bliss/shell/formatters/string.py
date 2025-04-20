# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import re

ANSI_ESCAPE = re.compile(r"\x1b[@-_][0-?]*[ -/]*[@-~]")


def removed_ansi_sequence(string: str) -> str:
    """Convert a string into the same one without ANSI character sequence"""
    return ANSI_ESCAPE.sub("", string)
