# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Bliss REPL (Read Eval Print Loop)"""

from __future__ import annotations

import typing
import io
import os
import sys
import socket
import logging
import platform
from collections.abc import Callable
from datetime import datetime

import ptpython.layout
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.styles.pygments import style_from_pygments_cls
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound

from prompt_toolkit.utils import is_windows
from prompt_toolkit.application import current
from prompt_toolkit.keys import Keys
from prompt_toolkit.filters import has_focus
from prompt_toolkit.enums import DEFAULT_BUFFER

from bliss.shell.cli.prompt import BlissPrompt
from bliss.shell.cli.typing_helper import TypingHelper
from bliss.shell.cli.no_tmux_statusbar import no_tmux_statusbar
from bliss.shell.cli.tmux_statusbar import tmux_statusbar
from bliss.shell.cli.no_thread_repl import NoThreadPythonRepl
from bliss.shell.cli.bliss_output import BlissOutput

from bliss import current_session, global_log
from bliss.common import event
from bliss.common import constants
from bliss.common.session import Session
from bliss.common.session import DefaultSession
from bliss import release
from bliss.config import static
from bliss.config.conductor.client import get_default_connection
from bliss.shell.formatters.repl import format_repl
from bliss.common.logtools import elogbook
from bliss.config.settings import OrderedHashObjSetting
from bliss.shell.cli.initialize_session import setup_session
from bliss.shell.cli.bliss_app_session import (
    session_stream,
    BlissAppSession,
)
from bliss.shell.cli.ptpython_completer import BlissPythonCompleter
from .error_report import ErrorReport
from bliss.shell.pt.default_style import get_style as get_bliss_style


def _configure_repl(repl):

    # intended to be used for testing as ctrl+t can be send via stdin.write("\x14")
    # @repl.add_key_binding(Keys.ControlT)
    # def _(event):
    #    sys.stderr.write("<<BLISS REPL TEST>>")
    #    text = repl.default_buffer.text
    #    sys.stderr.write("<<BUFFER TEST>>")
    #    sys.stderr.write(text)
    #    sys.stderr.write("<<BUFFER TEST>>")
    #    sys.stderr.write("<<HISTORY>>")
    #    sys.stderr.write(repl.default_buffer.history._loaded_strings[-1])
    #    sys.stderr.write("<<HISTORY>>")
    #    sys.stderr.write("<<BLISS REPL TEST>>")

    @repl.add_key_binding(
        Keys.ControlSpace, filter=has_focus(DEFAULT_BUFFER), eager=True
    )
    def _(event):
        """
        Initialize autocompletion at cursor.
        If the autocompletion menu is not showing, display it with the
        appropriate completions for the context.
        If the menu is showing, select the next completion.
        """

        b = event.app.current_buffer
        if b.complete_state:
            b.complete_next()
        else:
            b.start_completion(select_first=False)


class BlissRepl(NoThreadPythonRepl):
    """BlissRepl implementation.

    Arguments:
        use_tmux: If true, the repl was created with tmux.
                  The toolbar with be selected according to the available shortcuts
        exit_msg: If defined, a message is displayed when user try to use `exit()`
                  from the shell
        kwargs: Extra arguments passed to the ptpython PythonRepl constructor
    """

    def __init__(
        self,
        get_globals: Callable[[], dict[str, typing.Any]] | None = None,
        get_locals: Callable[[], dict[str, typing.Any]] | None = None,
        style: str | None = None,
        theme_mode: str = "light",
        prompt_label: str = "BLISS",
        title: str | None = None,
        use_tmux: bool = False,
        log_stdout_setting_cache=None,
        session_name: str | None = None,
        expert_error_report: bool = False,
        exit_msg: str | None = None,
        app_session=None,
        **kwargs,
    ):
        # Catch and remove additional kwargs
        if session_name is None:
            self.session_name = constants.DEFAULT_SESSION_NAME
        else:
            self.session_name = session_name
        self.use_tmux = use_tmux
        self.theme_mode: str = theme_mode
        self._app_session = app_session

        # patch ptpython statusbar
        # FIXME: would be better to use the statusbar instead of patching the statusbar
        if self.use_tmux and not is_windows():
            ptpython.layout.status_bar = tmux_statusbar
        else:
            ptpython.layout.status_bar = no_tmux_statusbar

        self._log_stdout_file_output_dict: dict[str, io.IO[typing.Any]] = {}
        self._log_stdout_buffer: list[str] = []

        empty_dict: dict[str, typing.Any] = {}
        completer = BlissPythonCompleter(
            get_globals or (lambda: empty_dict),
            get_locals or get_globals or (lambda: empty_dict),
            lambda: self.enable_dictionary_completion,
        )

        color_depth: ColorDepth | None = ColorDepth.from_env()
        super().__init__(
            get_globals=get_globals,
            get_locals=get_locals,
            _completer=completer,
            color_depth=color_depth,
            **kwargs,
        )

        self.bliss_session: Session | None = None
        self.bliss_prompt = BlissPrompt(self, prompt_label)
        self.all_prompt_styles["bliss"] = self.bliss_prompt
        self.prompt_style = "bliss"
        self.show_signature = True
        if title:
            self.terminal_title = title

        self.stdout_proxy = None
        app_session = current._current_app_session.get()
        if isinstance(app_session, BlissAppSession):
            self.stdout_proxy = app_session.stdout_proxy

        # set stdout stream once global_log has been initialized
        if self.stdout_proxy is not None:
            global_log.set_stdout_handler_stream(self.stdout_proxy)

        if exit_msg is not None:

            class ExitMsgInfo:
                def __info__(self):
                    return exit_msg

            exit_msg_info = ExitMsgInfo()
            self.get_globals()["exit"] = exit_msg_info

        self._stdout_settings: OrderedHashObjSetting | dict[str, typing.Any]
        # stdout files duplication (load settings)
        if log_stdout_setting_cache == "redis":
            self._stdout_settings = OrderedHashObjSetting(
                f"{self.session_name}_stdout_settings"
            )
        else:
            # in case BEACON/REDIS is not running, like in tests
            self._stdout_settings = {}
        event.connect(self.app.output, "output", self._log_stdout_append)
        event.connect(self.app.output, "flush", self._log_stdout_flush)

        try:
            theme = style_from_pygments_cls(get_style_by_name(style))
        except ClassNotFound:
            print(
                f"Unknown color style class: {style}. using default. (check your bliss.ini)."
            )
            theme = style_from_pygments_cls(get_style_by_name("default"))

        self.install_ui_colorscheme("bliss_ui", theme)
        self.use_ui_colorscheme("bliss_ui")
        self.install_code_colorscheme("bliss_code_ui", theme)
        self.use_code_colorscheme("bliss_code_ui")

        # PTPYTHON SHELL PREFERENCES
        self.enable_history_search = True
        self.show_status_bar = True
        self.confirm_exit = True
        self.enable_mouse_support = False

        if self.use_tmux:
            self.exit_message = (
                "Do you really want to close session? (CTRL-B D to detach)"
            )

        self.typing_helper = TypingHelper(self)

        self.error_report = ErrorReport(self._current_style, app_session=app_session)
        self.error_report.expert_mode = expert_error_report

        _configure_repl(self)

    def initialize_session(self, early_log_info=None):
        version = release.version
        hostname = platform.node()

        # Beacon host/port
        try:
            host = get_default_connection()._host
            port = str(get_default_connection()._port)
        except Exception:
            host = "UNKNOWN"
            port = "UNKNOWN"

        # Conda environment
        try:
            env_name = os.environ["CONDA_DEFAULT_ENV"]
            conda_env = "(in %s Conda environment)" % env_name
        except KeyError:
            conda_env = ""

        print("")
        print(f"Welcome to BLISS {version}")
        print("Copyright (c) Beamline Control Unit, ESRF")
        print(f"  Running on {hostname} {conda_env}")
        print(f"  Repository: {os.path.dirname(release.__file__)}")
        print(f"  Connected to Beacon server on {host} (port {port})")

        if early_log_info is not None and early_log_info.count > 0:
            print()
            print(
                f"During the import {early_log_info.count} warnings were ignored. Restart BLISS with --debug to display them."
            )

        config = static.get_config()
        if config.invalid_yaml_files:
            print()
            print(
                f"Ignored {len(config.invalid_yaml_files)} YAML file(s) due to parsing error(s), use config.parsing_report() for details.\n"
            )

        self.app.output.flush()

        # Setup(s)
        if self.session_name == constants.DEFAULT_SESSION_NAME:
            self.bliss_session = DefaultSession()
        else:
            # we will lock the session name
            # this will prevent to start serveral bliss shell
            # with the same session name
            # lock will only be released at the end of process
            default_cnx = get_default_connection()
            try:
                default_cnx.lock(self.session_name, timeout=1.0)
            except RuntimeError:
                try:
                    lock_dict = default_cnx.who_locked(self.session_name)
                except RuntimeError:  # Beacon is to old to answer
                    raise RuntimeError(f"{self.session_name} is already started")
                else:
                    raise RuntimeError(
                        f"{self.session_name} is already running on %s"
                        % lock_dict.get(self.session_name)
                    )

            # set the client name to something useful
            try:
                default_cnx.set_client_name(
                    f"host:{socket.gethostname()},pid:{os.getpid()} cmd: **bliss -s {self.session_name}**"
                )
            except RuntimeError:  # Beacon is too old
                pass

            print("%s: Loading config..." % self.session_name)
            self.bliss_session = config.get(self.session_name)

        if "rest" in self.bliss_session.local_config:
            try:
                from bliss.rest_service.rest_service import RestService
            except ImportError:
                print("blissterm not available. No REST API")
            else:
                rest_service = RestService(self.bliss_session)
                rest_service.start()
                # FIXME: Expose the rest_service from the session
                self.bliss_session.rest_service = rest_service

        assert self.bliss_session is not None
        self.bliss_session.active_session()

        self.bliss_session._set_bliss_repl(self)
        self.bliss_session._set_output_getter(session_stream)
        self.bliss_session.set_error_report(self.error_report)

        if setup_session(self.bliss_session, self.get_globals()):
            print("Done.")
        else:
            print("Warning: error(s) happened during setup, setup may not be complete.")
        print("")

        log = logging.getLogger("startup")
        log.info(
            f"Started BLISS version "
            f"{version} running on "
            f"{hostname} "
            f"{conda_env} "
            f"connected to Beacon server {host}"
        )

        # Flush after the initialization
        if self.stdout_proxy is not None:
            self.stdout_proxy.wait_flush()

        # init repl stdout duplication to file
        self.enable_stdout_file()

        # Flush after stdout enabling in case
        if self.stdout_proxy is not None:
            self.stdout_proxy.wait_flush()

    def exit(self):
        event.disconnect(self.app.output, "output", self._log_stdout_append)
        event.disconnect(self.app.output, "flush", self._log_stdout_flush)
        if self.app.is_running:
            self.app.exit()

    def raw_eval(self, text):
        """Delegate eval to base class

        Note: called from tests
        """
        prompt = "".join(x for _, x in self.bliss_prompt.in_prompt())

        # force reevaluation of stdout file path in case it is using
        # the default fname wich depends on proposal and today day
        if not self._stdout_settings.get("fname"):
            self.enable_stdout_file()

        # log cmd in stdout file
        for path, file in self._log_stdout_file_output_dict.items():
            if file:
                print(f"\n{prompt}{text}", file=file, flush=True)

        return super().eval(text)

    ##
    # NB: next methods are overloaded
    ##
    def eval(self, text):
        result = None
        output = self.app.output
        assert isinstance(output, BlissOutput)
        output.initialize_cell()
        try:
            logging.getLogger("user_input").info(text)
            elogbook.command(text)
            with output.capture_stdout:
                result = self.raw_eval(text)
                if self.stdout_proxy is not None:
                    self.stdout_proxy.wait_flush()
        except SystemExit:
            result = SystemExit  # this is a trick to let the finally code just pass
            raise
        except BaseException:
            # exception message is not captured, this is on purpose
            # (see test_elogbook_cmd_log_and_elog_add)
            with output.capture_stdout:
                self.error_report.display_exception(*sys.exc_info())
        finally:
            if result is None:
                # show_result will not be called, so we call it here
                output.finalize_cell(self.insert_blank_line_after_output)
        return result

    def eval_greenlet(self, text):
        eval_g = super().eval_greenlet(text)
        eval_g.spawn_tree_locals["eval_greenlet"] = eval_g
        app_session = current.get_app_session()
        eval_g.spawn_tree_locals["app_session"] = app_session
        return eval_g

    def show_result(self, result):
        """This is called when the return value of the command is not None."""
        try:
            result = format_repl(result)
        except BaseException:
            # display exception, but do not propagate and make shell to die
            self.error_report.display_exception(*sys.exc_info())
        else:
            output = self.app.output
            assert isinstance(output, BlissOutput)
            with output.capture_result:
                # Isolate the style of the result and the style of the interface
                old_style = self._current_style
                self._current_style = get_bliss_style()
                try:
                    super().show_result(result)
                finally:
                    self._current_style = old_style
                if self.stdout_proxy is not None:
                    self.stdout_proxy.wait_flush()
            output.finalize_cell(self.insert_blank_line_after_output)

    def _build_stdout_file_path(self, fdir, fname=None):
        if not fname:  # build a default file name
            now = datetime.now()
            fname = ""
            if hasattr(current_session.scan_saving, "beamline"):
                fname += f"{current_session.scan_saving.beamline}_"
            fname += f"{self.session_name}_{now.year}{now.month:02}{now.day:02}"
            if hasattr(current_session.scan_saving, "proposal_name"):
                fname += f"_{current_session.scan_saving.proposal_name}"
            fname += ".log"
        return os.path.join(fdir, fname)

    def _log_stdout_append(self, data: str):
        if self._log_stdout_file_output_dict:
            # buffering data for log_stdout file
            self._log_stdout_buffer.append(data)

    def _log_stdout_flush(self):
        if self._log_stdout_file_output_dict:
            data = "".join(self._log_stdout_buffer)
            data = data.replace("\r\n", "\n")
            self._log_stdout_buffer.clear()
            for _, file in self._log_stdout_file_output_dict.items():
                if file:
                    try:
                        file.write(data)
                        file.flush()
                    except Exception as e:
                        self._log_stdout_file_output_dict.clear()
                        print(f"Cannot flush stdout data to file: {e}")

    def enable_stdout_file(self, fdir: str | None = None, fname: str | None = None):
        """Enable stdout duplication to file"""
        if fdir is None:
            fdir = self._stdout_settings.get("fdir")

        if fname is None:
            fname = self._stdout_settings.get("fname")

        if fname and not fdir:
            raise RuntimeError(
                "Please specify a directory for the stdout log file first"
            )

        if fdir:
            abspath = self._build_stdout_file_path(fdir, fname)
            if abspath not in self._log_stdout_file_output_dict.keys():
                try:
                    file = open(abspath, "a")
                except Exception:
                    print(f"log_stdout: could not open file '{abspath}'")
                else:
                    self.disable_stdout_file()
                    self._stdout_settings["fdir"] = fdir
                    self._stdout_settings["fname"] = fname
                    self._log_stdout_file_output_dict[abspath] = file

    def disable_stdout_file(self):
        """Disable stdout duplication to file"""
        for stdoutfile in self._log_stdout_file_output_dict.values():
            stdoutfile.close()
        self._log_stdout_file_output_dict.clear()
        self._stdout_settings["fdir"] = None
        self._stdout_settings["fname"] = None

    def show_stdout_file(self):
        if not self._stdout_settings.get("fdir"):
            print("stdout logging is disabled")
        else:
            print(
                f"stdout is logged to {list(self._log_stdout_file_output_dict.keys())[0]}"
            )

    def get_cell_output(self, index: int = -1):
        """Returns the output of a repl execution cell.

        Arguments:
            index: Index of the cell as displayed in the prompt.
                   `-1` (default) is used for the last cell
        """
        output = self.app.output
        if isinstance(output, BlissOutput):
            return output[index]
        raise RuntimeError("No output storage available with this setup env")
