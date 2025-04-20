# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Standard functions provided to the BLISS shell.
"""

from bliss.config import static  # noqa: F401

from bliss.common.soft_axis import SoftAxis  # noqa: F401
from bliss.common.counter import SoftCounter  # noqa: F401
from bliss.shell.getval import bliss_prompt

from bliss.common.cleanup import (  # noqa: F401
    cleanup,
    error_cleanup,
)

from bliss.scanning.scan_tools import (  # noqa: F401
    cen,
    com,
    peak,
    trough,
    where,
    find_position,
    fwhm,
)

from numpy import (  # noqa: F401
    sin,
    cos,
    tan,
    arcsin,
    arccos,
    arctan,
    arctan2,
    log,
    log10,
    sqrt,
    exp,
    power,
    deg2rad,
    rad2deg,
)

from numpy.random import rand  # noqa: F401
from time import asctime as date  # noqa: F401
from pprint import pprint  # noqa: F401
from gevent import sleep  # noqa: F401

from ._motion import (  # noqa: F401
    mv,
    umv,
    mvr,
    umvr,
    mvd,
    umvd,
    mvdr,
    umvdr,
    rockit,
    move,
    goto_cen,
    goto_peak,
    goto_click,
    goto_min,
    goto_com,
    goto_custom,
)

from ._datapolicy import (  # noqa: F401
    newproposal,
    endproposal,
    newsample,
    newcollection,
    newdataset,
    enddataset,
)

from ._logging import (  # noqa: F401
    lslog,
    lsdebug,
    debugon,
    debugoff,
    log_stdout,
    elog_print,
    elog_add,
    elog_prdef,
    elog_plot,
    elogbook,
)

from ._utils import (  # noqa: F401
    lsconfig,
    info,
    bench,
    clear,
    countdown,
    prdef,
    print_html,
    print_ansi,
    text_block,
    test_color_styles,
    feedback_info,
    feedback_info_str,
    last_error,
    pon,
    poff,
)

from ._plot import (  # noqa: F401
    plotinit,
    plotselect,
    replot,
    plot,
    flint,
    edit_roi_counters,
)

from ._menu import (  # noqa: F401
    menu,
    show_dialog,
)

from ._devices import (  # noqa: F401
    wa,
    wm,
    wu,
    sta,
    stm,
    lsmot,
    sync,
    interlock_show,
    interlock_state,
    lscnt,
    lsmg,
    lsobj,
    wid,
    reset_equipment,
)

from ._debug import (  # noqa: F401
    metadata_profiling,
    time_profile,
    greenlets_tree,
    greenlets_monitor,
)

from bliss.controllers.lima.limatools import (  # noqa: F401
    limastat,
    limatake,
)

from bliss.common.scans import *  # noqa: F401,F403

from ._external import (  # noqa: F401
    silx_view,
    pymca,
    tw,
)


# Patch built-in input function to avoid to block gevent loop in BLISS shell.
def _patched_input(message=""):
    return bliss_prompt(message)


__builtins__["input"] = _patched_input
