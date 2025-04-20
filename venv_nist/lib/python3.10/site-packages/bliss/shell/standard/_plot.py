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

import typing
from bliss.common.utils import typecheck
from bliss.shell import iter_common as _iter_common
from bliss.common import plot as plot_module
from bliss.common.types import _providing_channel
from bliss.controllers.lima.lima_base import Lima
from bliss.controllers.lima2.device import Lima2
from ._utils import print_html

# Expose this functions from this module
from bliss.common.plot import meshselect  # noqa: E402,F401
from bliss.common.plot import plot  # noqa: E402,F401


@typecheck
def plotinit(*counters: _providing_channel):
    """
    Select counters to plot and to use only with the next scan command.

    User-level function built on top of bliss.common.scans.plotinit()
    """

    # If called without arguments, prints help.
    if not counters:
        print(
            """
plotinit usage:
    plotinit(<counters>*)                  - Select a set of counters

example:
    plotinit(counter1, counter2)
    plotinit('*')                          - Select everything
    plotinit('beamviewer:roi_counters:*')  - Select all the ROIs from a beamviewer
    plotinit('beamviewer:*_sum')           - Select any sum ROIs from a beamviewer
"""
        )
    else:
        plot_module.plotinit(*counters)
    print("")

    names = plot_module.get_next_plotted_counters()
    if names:
        print("Plotted counter(s) for the next scan:")
        for cnt_name in names:
            print(f"- {cnt_name}")
    else:
        print("No specific counter(s) for the next scan")
    print("")


@typecheck
def plotselect(*counters: _providing_channel):
    """
    Select counters to plot and used by alignment functions (cen, peak, etc).

    User-level function built on top of bliss.common.plot.plotselect()
    """

    all_counters_names = [x.name for x in _iter_common.iter_counters()] + [
        x.fullname for x in _iter_common.iter_counters()
    ]

    # If called without arguments, prints help.
    if not counters:
        print(
            """
plotselect usage:
    plotselect(<counters>*)                  - Select a set of counters

example:
    plotselect(counter1, counter2)
    plotselect('*')                          - Select everything
    plotselect('beamviewer:roi_counters:*')  - Select all the ROIs from a beamviewer
    plotselect('beamviewer:*_sum')           - Select any sum ROIs from a beamviewer
"""
        )
    else:
        if len(counters) == 1 and counters[0] is None:
            counters = []

        # If counter is provided as a string, warn if it does not exist.
        for counter in counters:
            if isinstance(counter, str):
                if counter not in all_counters_names:
                    print(f"WARNING: '{counter}' is not a valid counter")

        plot_module.plotselect(*counters)
    print("")
    print(
        "Plotted counter(s) last selected with plotselect (could be different from the current display):"
    )
    for cnt_name in plot_module.get_plotted_counters():
        if cnt_name in all_counters_names:
            print(f"- {cnt_name}")
        else:
            print_html(f"- <danger>{cnt_name}</danger>")
    print("")


def replot():
    """Clear any user marker from the default curve plot"""
    plot_module.replot()


def flint():
    """
    Return a proxy to the running Flint application used by BLISS, else create
    one.

    If there is problem to create or to connect to Flint, an exception is
    raised.

        # This can be used to start Flint
        BLISS [1]: flint()

        # This can be used to close Flint
        BLISS [1]: f = flint()
        BLISS [2]: f.close()

        # This can be used to kill Flint
        BLISS [1]: f = flint()
        BLISS [2]: f.kill9()
    """
    proxy = plot_module.get_flint(creation_allowed=True, mandatory=True)
    assert proxy is not None
    print("Current Flint PID: ", proxy.pid)
    print("")
    return proxy


@typecheck
def edit_roi_counters(detector: (Lima, Lima2), acq_time: typing.Optional[float] = None):
    """
    Edit the given detector ROI counters.

    When called without arguments, it will use the image from specified detector
    from the last scan/ct as a reference. If `acq_time` is specified,
    it will do a `ct()` with the given count time to acquire a new image.


    .. code-block:: python

        # Flint will be open if it is not yet the case
        edit_roi_counters(pilatus1, 0.1)

        # Flint must already be open
        ct(0.1, pilatus1)
        edit_roi_counters(pilatus1)
    """
    if isinstance(detector, Lima):
        return detector.edit_rois(acq_time)
    else:
        return detector.processing.edit_rois()
