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
import os
import logging
from bliss import current_session
from bliss.common.utils import typecheck
from bliss.common import plot as plot_module
from bliss.common import logtools
from bliss import global_log
from bliss.scanning import scan_debug

# Expose this functions from this module
from bliss.common.logtools import elogbook  # noqa: E402,F401
from bliss.common.logtools import elog_print  # noqa: E402,F401

if typing.TYPE_CHECKING:
    from bliss.shell.cli.bliss_repl import BlissRepl

from ._utils import _get_source_code


def lslog(glob: str | None = None, debug_only: bool = False):
    """
    Search for loggers.

    It uses a pattern matching normally used by shells.
    Common operators are `*` for any number of characters
    and `?` for one character of any type.

    Args:
        glob: a logger name with optional glob matching
        debug_only: True to display only loggers at debug level
                    (equivalent to lslog)

    Examples:

    >>> lslog()  # prints all loggers

    >>> lslog('*motor?')  # prints loggers that finish with 'motor' + 1 char
                          # like motor1, motor2, motork

    >>> lslog('*Socket*')  # prints loggers that contains 'Socket'

    """
    if glob is None:
        loggers = {
            **global_log._find_loggers("bliss*"),
            **global_log._find_loggers("flint*"),
            **global_log._find_loggers("global*"),
        }
    else:
        loggers = global_log._find_loggers(glob)

    show_loggers(loggers, debug_only)


def show_loggers(loggers, debug_only: bool = False):
    if loggers.items():
        maxlen = max([len(name) for name, _ in loggers.items()])
    else:
        maxlen = 0
    msgfmt = "{0:{width}} {1:8} {2:8}"
    output = False

    for name in sorted(loggers.keys()):
        logger = loggers[name]
        try:
            has_debug = logger.getEffectiveLevel() == logging.DEBUG
        except AttributeError:
            has_debug = False
        if debug_only and not has_debug:
            continue
        if not output:
            output = True
            print(
                "\n" + msgfmt.format("logger name", "efflevel", "level", width=maxlen)
            )
            print(msgfmt.format("=" * maxlen, 8 * "=", 8 * "=", width=maxlen))
        efflevel = logging.getLevelName(logger.getEffectiveLevel())
        level = logging.getLevelName(logger.level)
        if logger.disabled:
            level = "%s [DISABLED]" % level
        print(msgfmt.format(name, efflevel, level, width=maxlen))

    if output:
        print("")
    else:
        print("No loggers found.\n")


def lsdebug(glob: str | None = None, debug_only: bool = False) -> None:
    """
    Display current Loggers at DEBUG level
    """
    lslog(glob, debug_only=True)


def debugon(glob_logger_pattern_or_obj, gm_filter: str | None = None) -> None:
    """
    Activate debug-level logging for a specifig logger or an object

    Args:
        glob_logger_pattern_or_obj: glob style pattern matching for logger name, or instance
        gm_filter: a filter string as a global map node under which logger pattern should be found

    Hints on glob: pattern matching normally used by shells
                   common operators are * for any number of characters
                   and ? for one character of any type

    Return:
        None

    Examples:
        >>> log.debugon(robz)         # passing an object
        >>> log.debugon('*rob?')      # using a pattern
    """

    if isinstance(glob_logger_pattern_or_obj, str):
        str_arg = glob_logger_pattern_or_obj.lower()
        if str_arg in ["com", "comm", "comms"]:
            glob_logger_pattern_or_obj = "*"
            gm_filter = "comms"
        elif str_arg in scan_debug.VALID_DEBUG_MODES:
            current_session.scan_debug_mode = str_arg
            glob_logger_pattern_or_obj = "bliss.scans.debugger"

    activated = global_log.debugon(glob_logger_pattern_or_obj, gm_filter)
    if activated:
        show_loggers(activated)
        if current_session:
            print(
                f"Hint: logging outputs to '/var/log/bliss/{current_session.name}.log'"
            )
    else:
        print(f"No loggers found for [{glob_logger_pattern_or_obj}]")


def debugoff(glob_logger_pattern_or_obj, gm_filter: str | None = None) -> None:

    if isinstance(glob_logger_pattern_or_obj, str):
        str_arg = glob_logger_pattern_or_obj.lower()
        if str_arg in ["com", "comm", "comms"]:
            glob_logger_pattern_or_obj = "*"
            gm_filter = "comms"
        elif str_arg in scan_debug.VALID_DEBUG_MODES:
            current_session.scan_debug_mode = None
            glob_logger_pattern_or_obj = "bliss.scans.debugger"

    deactivated = global_log.debugoff(glob_logger_pattern_or_obj, gm_filter)
    if deactivated:
        show_loggers(deactivated)
    else:
        print(f"No loggers found for [{glob_logger_pattern_or_obj}]")


def _safe_bliss_repl() -> BlissRepl:
    repl = current_session.bliss_repl
    if repl is None:
        raise RuntimeError(f"No repl defined for the session '{current_session.name}'")

    from bliss.shell.cli.bliss_repl import BlissRepl

    if not isinstance(repl, BlissRepl):
        raise RuntimeError(
            f"Unexpected repl type {type(repl)} in session {current_session.name}"
        )
    return typing.cast(BlissRepl, repl)


class _StandardLog:
    """Object to be used with BLISS loggers"""

    def __init__(self, name: str):
        self.name: str = name


_standard_obj = _StandardLog("standard")
"""Object to be used with BLISS loggers, like `log_warning`"""


@elogbook.disable_command_logging
@typecheck
def elog_add(index: int = -1, **kw):
    """
    Send to the logbook given cell output and the print that was
    performed during the elaboration.

    Only a fixed size of output are kept in memory (normally last 20).

    Args:
        index (int): Index of the cell to be sent to logbook, can
                     be positive reflecting the prompt index
                     or negative (relative to the current cell).
                     Default is -1 (previous cell)

    Example:
        BLISS [2]: diode
          Out [2]: 'diode` counter info:
                     counter type = sampling
                     sampling mode = MEAN
                     fullname = simulation_diode_sampling_controller:diode
                     unit = None
                     mode = MEAN (1)

        BLISS [3]: elog_add()  # sends last output from diode
    """
    bliss_repl = _safe_bliss_repl()
    try:
        comment = bliss_repl.get_cell_output(index)
    except IndexError as e:
        logtools.log_warning(_standard_obj, str(e))
    except TypeError:
        logtools.log_warning(
            _standard_obj,
            "elog_add should be called with a number, for example 'elog_add(42)'",
        )
    else:
        if comment is not None:
            kw.setdefault("formatted", True)
            elogbook.comment(comment, **kw)


def elog_prdef(obj_or_name):
    """
    Send the text of the source code for an object or the name of an object to the logbook.
    """
    header, lines = _get_source_code(obj_or_name)
    code = "".join(lines)
    message = f'{header}<pre class="language-python"><code>{code}</code></pre>'
    elog_print(message, mimetype="text/html")


def elog_plot(controller=None):
    """Export the actual curve plot to the logbook

    Arguments:
        controller: If specified, a Lima or MCA controller can be specified
                    to export the relative specific plot
    """
    from bliss.controllers.mca.base import BaseMCA
    from bliss.controllers.lima.lima_base import Lima

    flint = plot_module.get_flint(creation_allowed=False, mandatory=False)
    if flint is None:
        print("Flint is not available or not reachable")
        return
    flint.wait_end_of_scans()
    if controller is None:
        p = flint.get_live_plot(kind="default-curve")
    elif isinstance(controller, Lima):
        p = flint.get_live_plot(controller)
    elif isinstance(controller, BaseMCA):
        p = flint.get_live_plot(controller)
    else:
        raise RuntimeError(
            "Reaching plot from controller type {type(controller)} is not supported"
        )
    try:
        p.export_to_logbook()
    except RuntimeError as e:
        print(e.args[0])


def log_stdout(fdir=None, fname=None):
    """Duplicate BLISS shell output into specified file "<fdir>/<fname>".

    If 'fname' is not specified, a default file name is automatically generated as follow:
        * No data policy: "<session>_<date>.log"
        * ESRF data policy: "<beamline>_<session>_<date>_<proposal>.log"

        Note: during a session, if <date> or <proposal> change, the logging file path is updated.

    <fdir> and <fname> are stored as persitant settings, so that logging is automatically re-activated
    at next session if it has been enabled once.

    Usage examples:

        * log_stdout(): show current logging status and file path

        * log_stdout("/tmp/logging"): enable logging and set file directory to "/tmp/logging".
          The default file name will be used.

        * log_stdout(False): disable stdout logging (clear )

        * log_stdout(fname='log.txt'): specify a custom logging file name (a file directory must have been specified first)

        * log_stdout(fname=''): enable usage of the default file name

    """
    bliss_repl = _safe_bliss_repl()
    if fdir is None and fname is None:
        bliss_repl.show_stdout_file()
    elif fdir is False:
        bliss_repl.disable_stdout_file()
    else:
        if fdir is not None:
            if not os.path.isabs(fdir):
                raise ValueError("directory path must be absolute")
            if not os.path.isdir(fdir):
                raise ValueError(f"directory '{fdir}' does not exist")
        bliss_repl.enable_stdout_file(fdir, fname)
