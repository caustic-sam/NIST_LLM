# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations


def number_widths(value: str) -> tuple[int, int]:
    """Returns the length of the integer and the decimal parts"""
    s = len(value)
    p = value.find(".")
    if p == -1:
        p = value.find("e")
        if p == -1:
            return s, 0
    return p, s - p
