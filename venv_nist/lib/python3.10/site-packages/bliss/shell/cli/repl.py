# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Bliss REPL (Read Eval Print Loop)"""

from __future__ import annotations

import typing
import asyncio
import os
import sys
import gevent
import signal
import logging
from datetime import datetime

from prompt_toolkit.utils import is_windows

from bliss.shell import log_utils
from bliss.shell.pt import patch_for_repl
from bliss.shell.cli.bliss_output import BlissOutput
from bliss.shell import standard
from bliss.shell.cli.bliss_repl import BlissRepl

from bliss import set_bliss_shell_mode
from bliss.common.protected_dict import ProtectedDict
from bliss.common import constants
from bliss.shell.data.display import StepScanProgress
from bliss.common.logtools import elogbook
from bliss.scanning import scan as scan_module
from bliss.shell.cli.bliss_app_session import (
    bliss_app_session,
)
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.output.defaults import create_output
from bliss.common.user_status_info import set_display_callback
from bliss.shell.pt.user_status_info import PtUserStatusDisplay

if typing.TYPE_CHECKING:
    from prompt_toolkit.output import Output
    from prompt_toolkit.application import AppSession


logger = logging.getLogger(__name__)


def _archive_history(
    history_filename, file_size_thresh=10**6, keep_active_entries=1000
):
    if (
        os.path.exists(history_filename)
        and os.stat(history_filename).st_size > file_size_thresh
    ):
        with open(history_filename, "r") as f:
            lines = f.readlines()

        # history is handled as a list of entries (block of lines) to avoid splitting them while archiving
        entries = []
        entry = []
        for line in lines:
            if not line.isspace():
                entry.append(line)
            elif entry:
                entries.append(entry)
                entry = []
        if entry:
            entries.append(entry)

        now = datetime.now()
        archive_filename = f"{history_filename}_{now.year}{now.month:02}{now.day:02}"
        with open(archive_filename, "a") as f:
            for entry in entries[:-keep_active_entries]:
                f.write("".join(entry) + "\n")

        with open(history_filename, "w") as f:
            for entry in entries[-keep_active_entries:]:
                f.write("".join(entry) + "\n")


def cli(
    repl_class=BlissRepl,
    locals=None,
    session_name=None,
    vi_mode=False,
    startup_paths=None,
    use_tmux=False,
    expert_error_report=False,
    theme_mode: str = "light",
    style="default",
    early_log_info=None,
    log_stdout_setting_cache="redis",
    app_session: AppSession | None = None,
    output: Output | None = None,
    plain_output: bool = False,
    **kwargs,
) -> BlissRepl:
    """
    Create a command line interface

    Args:
        session_name : session to initialize (default: None)
        vi_mode (bool): Use Vi instead of Emacs key bindings.
        output: A prompt toolkit output, if None one will be crated for you
        plain_output: If true the output will be setup with a plain text output
        kwargs: Extra arguments passed to the repl
    """
    if plain_output and output is not None:
        raise ValueError(
            "Both `plain_output` and `output` arguments are defined. You have to choose one or the other"
        )

    set_bliss_shell_mode(True)

    # Activate scan progress display
    scan_module._scan_progress_class = StepScanProgress

    user_status_display = PtUserStatusDisplay()
    set_display_callback(user_status_display)

    # Enable loggers
    elogbook.enable()  # destination: electronic logbook

    # user namespace
    user_ns = {"__builtins__": __builtins__}

    if session_name and not session_name.startswith(constants.DEFAULT_SESSION_NAME):
        session_id = session_name
        session_title = "Bliss shell ({0})".format(session_name)
        prompt_label = session_name.upper()
    else:
        session_id = "default"
        session_title = "Bliss shell"
        prompt_label = "BLISS"

    history_filename = ".bliss_%s_history" % (session_id)
    if is_windows():
        history_filename = os.path.join(os.environ["USERPROFILE"], history_filename)
    else:
        history_filename = os.path.join(os.environ["HOME"], history_filename)

    _archive_history(history_filename)

    protected_user_ns = ProtectedDict(user_ns)
    protected_user_ns["protect"] = protected_user_ns.protect
    protected_user_ns["unprotect"] = protected_user_ns.unprotect
    protected_user_ns.update(standard.__dict__)
    protected_user_ns["history"] = lambda: print("Please press F3-key to view history!")
    protected_user_ns._protect(protected_user_ns)

    if output is None:
        if app_session:
            output = app_session.output
        else:
            if plain_output:
                output = PlainTextOutput(sys.stdout)
            else:
                # This supports Vt100, win32, and no tty stdout
                output = create_output(stdout=sys.stdout)
    if not isinstance(output, BlissOutput):
        output = BlissOutput(output)

    # Create REPL.
    repl = repl_class(
        get_globals=lambda: protected_user_ns,
        app_session=app_session,
        session_name=session_name,
        vi_mode=vi_mode,
        prompt_label=prompt_label,
        title=session_title,
        history_filename=history_filename,
        startup_paths=startup_paths,
        use_tmux=use_tmux,
        style=style,
        theme_mode=theme_mode,
        expert_error_report=expert_error_report,
        log_stdout_setting_cache=log_stdout_setting_cache,
        output=output,
        **kwargs,
    )

    repl.initialize_session(early_log_info)

    return repl


def embed(handle_sigint: bool = True, **kwargs):
    """
    Call this to embed bliss shell at the current point in your program

    Arguments:
        handle_sigint: If true, SIGINT is registered to kill
        kwargs: Extra arguments passed to the repl
    """
    patch_for_repl.patch()
    with log_utils.filter_warnings():
        with bliss_app_session() as app_session:
            repl = cli(repl_class=BlissRepl, app_session=app_session, **kwargs)

            if handle_sigint:
                # Disable python SIGINT handler to tell to matplotlib to not play with it.
                signal.signal(signal.SIGINT, signal.SIG_IGN)

                # In any case we use the gevent handler, thus disabling the
                # python one is invisible for us.
                sigint_handler = gevent.signal_handler(
                    signal.SIGINT, repl.kill_current_eval
                )
            try:
                asyncio.run(repl.run_async())
            finally:
                if handle_sigint:
                    sigint_handler.cancel()
