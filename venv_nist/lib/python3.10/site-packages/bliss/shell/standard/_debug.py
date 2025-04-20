# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Standard functions provided to the BLISS shell.
"""

from __future__ import annotations

import gevent
import numpy
from time import perf_counter

# Expose this functions from this module
from bliss.common.profiling import time_profile  # noqa: E402,F401

from bliss.common.greenlet_utils.tree import main_greenlet_tree
from bliss.shell.standard import text_block


def metadata_profiling(scan=None, timing=None):
    """
    Report metadata profiling for previous scans.

    .. code-block:: python

        metadata_profiling()

    Arguments:
        scan: A scan to check
        timing: A collection timing to check (START, PREPARED, END)
    """
    from bliss.scanning.scan_meta import get_user_scan_meta, get_controllers_scan_meta
    from bliss.shell.formatters.table import IncrementalTable

    user_perf = get_user_scan_meta()._profile_metadata_gathering(
        scan=scan, timing=timing
    )
    ctrl_perf = get_controllers_scan_meta()._profile_metadata_gathering(
        scan=scan, timing=timing
    )

    head = [
        "name",
        "category",
        "metadata gathering time (ms)",
    ]

    def nan_sort_key(tpl):
        if numpy.isnan(tpl[2]):
            return -numpy.inf
        return tpl[2]

    for title, perf in [
        ("USER META DATA", user_perf),
        ("CONTROLLERS META DATA", ctrl_perf),
    ]:
        lmargin = "  "
        tab = IncrementalTable([head], col_sep="|", flag="", lmargin=lmargin)
        for (name, catname, dt) in sorted(perf, key=nan_sort_key, reverse=True):
            tab.add_line([name, catname, dt * 1000])
        tab.resize(16, 60)
        tab.add_separator("-", line_index=1)

        w = tab.full_width
        txt = f"\n{lmargin}{'='*w}\n{lmargin}{title:^{w}}\n{lmargin}{'='*w}\n\n"
        txt += f"{tab}\n\n"
        print(txt)


def greenlets_tree():
    main_greenlet_tree().show()


def greenlets_monitor():
    start = perf_counter()

    def render():
        txt = str(main_greenlet_tree())
        txt += f"\n{perf_counter() - start:.1f}s\n"
        return txt.count("\n"), txt

    with text_block(render):
        while True:
            gevent.sleep(1)  # not the refresh rate
