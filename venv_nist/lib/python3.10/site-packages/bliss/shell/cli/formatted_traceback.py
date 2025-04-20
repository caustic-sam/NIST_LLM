# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


from __future__ import annotations

import os
import sys
import gevent
import importlib
import traceback
from datetime import datetime
from textwrap import indent

from prompt_toolkit.formatted_text import PygmentsTokens
from prompt_toolkit import print_formatted_text

from pygments import lex
from pygments.lexer import RegexLexer, bygroups, default
from pygments.token import Token


class BlissTracebackLexer(RegexLexer):
    """Inspired by pygments.PythonTracebackLexer to colorize tracebacks, but for BlissTraceback
    as well (it handles locals and timestamp)"""

    name = "BlissTraceback"
    tokens = {
        "root": [
            (r"\n", Token.Text),
            (r"\*[^\n]+\n", Token.Comment.Preproc),
            (r"^[0-9]+\/[0-9]+\/[0-9]+ [0-9]+:[0-9]+:[0-9]+\n", Token.Name.Property),
            (r"^Traceback \(most recent call last\):\n", Token.Text, "intb"),
            (
                r"^During handling of the above exception, another exception occurred:\n\n",
                Token.Heading,
            ),
            (
                r"^The above exception was the direct cause of the following exception:\n\n",
                Token.Heading,
            ),
            (r'^(?=  File "[^"]+", line \d+)', Token.Generic.Traceback, "intb"),
            (r"^.*\n", Token.Other),
        ],
        "intb": [
            (
                r'^(  File )("[^"]+")(, line )(\d+)(, in )(.+)(\n)',
                bygroups(
                    Token.Text,
                    Token.Name.Builtin,
                    Token.Text,
                    Token.Number,
                    Token.Text,
                    Token.Name,
                    Token.Text,
                ),
            ),
            (
                r'^(  File )("[^"]+")(, line )(\d+)(\n)',
                bygroups(
                    Token.Text, Token.Name.Builtin, Token.Text, Token.Number, Token.Text
                ),
            ),
            (r"^(?=    @.+\n)", Token.Generic.Traceback, "markers"),
            (
                r"^(    )(.+)(\n)",
                bygroups(Token.Text, Token.Other, Token.Text),
                "markers",
            ),
            (
                r"^([^:]+)(: )(.+)(\n)",
                bygroups(
                    Token.Generic.Error, Token.Text, Token.Name.Exception, Token.Text
                ),
                "#pop",
            ),
            (
                r"^([a-zA-Z_][\w.]*)(:?\n)",
                bygroups(Token.Generic.Error, Token.Text),
                "#pop",
            ),
        ],
        "markers": [
            (r"^(    )\.\.\. \(truncated\)\n", Token.Comment.Preproc),
            (
                r"^(    )(@[^:]+)(: )(\.\.\. \(truncated\)\n)",
                bygroups(
                    Token.Text,
                    Token.Name.Variable,
                    Token.Punctuation,
                    Token.Comment.Preproc,
                ),
            ),
            (
                r"^(    )(@[^:]+)(:)(.+)(\n)",
                bygroups(
                    Token.Text,
                    Token.Name.Variable,
                    Token.Punctuation,
                    Token.Literal.String.Other,
                    Token.Text,
                ),
            ),
            (r"^[\w]*\n", Token.Text, "#pop"),
            default("#pop"),
        ],
    }


def pprint_traceback(formatted_traceback, style, output=None):
    """Print a formatted traceback (generic Python traceback or BlissTraceback) with colors,
    using BlissTracebackLexer.
    """
    tokens = list(lex(formatted_traceback, lexer=BlissTracebackLexer()))
    print_formatted_text(PygmentsTokens(tokens), end="", style=style, output=output)


class BlissTraceback:
    """Extract traceback content for later formatting without keeping any reference on
    objects to avoid memory leaks. Then the format method can be used to produce various
    formatting of the same traceback.
    """

    _blacklist = [
        # gevent module path
        os.path.dirname(importlib.util.find_spec("gevent").origin),  # type: ignore
        # gevent compiled functions root path
        "src/gevent",
    ]

    def __init__(self, exc_type, exc_value, tb):
        self._datetime = datetime.now()

        # convert traceback to StackSummary to stringify references and avoid memory leak
        self._locals_capture_exc = ""
        try:
            with gevent.Timeout(0.5):
                traceback_exc = traceback.TracebackException(
                    exc_type, exc_value, tb, capture_locals=True
                )
        except Exception as e:
            # Capture_locals option fails as soon as one local's __repr__ fails.
            # This will be fixed in python 3.11 with the addition of format_locals option,
            # see https://github.com/python/cpython/pull/29299
            # For the moment we can only disable capture_locals
            self._locals_capture_exc = (
                traceback.format_tb(sys.exc_info()[2], -1)[0] + f"  {e}"
            )
            traceback_exc = traceback.TracebackException(
                exc_type, exc_value, tb, capture_locals=False
            )
        except gevent.Timeout:
            self._locals_capture_exc = "Timeout during locals collection, __repr__ methods are taking too long."
            traceback_exc = traceback.TracebackException(
                exc_type, exc_value, tb, capture_locals=False
            )

        self._exc_info: list[tuple[str, Exception, traceback.StackSummary, str]] = []
        iter: traceback.TracebackException | None = traceback_exc
        while iter is not None:
            exc_type = iter.exc_type
            exc_value = str(iter)
            stack = iter.stack
            msg = ""
            if iter.__cause__ is not None:
                msg = "\nThe above exception was the direct cause of the following exception:\n\n"
                iter = iter.__cause__
            elif iter.__context__ is not None:
                msg = "\nDuring handling of the above exception, another exception occurred:\n\n"
                iter = iter.__context__
            else:
                iter = None
            self._exc_info.insert(0, (exc_type, exc_value, stack, msg))

    def _is_file_blacklisted(self, filename):
        for black_path in BlissTraceback._blacklist:
            if filename.startswith(black_path):
                return True
        return False

    def _format_stack(
        self,
        exc_type,
        exc_value,
        stack,
        msg,
        disable_blacklist,
        max_nb_locals,
        max_local_len,
        show_locals,
    ):
        text = ""

        # trim the stack to not display ptpython "eval" part
        for index, frame in enumerate(stack):
            if frame.filename.endswith("ptpython/repl.py") and frame.name == "eval":
                # '+2' to skip the call to 'eval' itself and the bliss terminal context
                stack = stack[index + 2 :]
                break

        for frame in stack:
            if not disable_blacklist and self._is_file_blacklisted(frame.filename):
                continue

            text += f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}\n'
            if frame._line:
                text += f"    {frame._line}\n"

            if show_locals and frame.locals is not None:
                for i, (key, val) in enumerate(sorted(frame.locals.items())):
                    if not (max_nb_locals < 0) and i + 1 > max_nb_locals:
                        text += "    ... (truncated)\n"
                        break
                    if len(val) <= max_local_len or max_local_len < 0:
                        text += f"    @{key}: {val}\n"
                    else:
                        text += f"    @{key}: ... (truncated)\n"

        text += f"{exc_type.__name__}: {exc_value}\n"
        return msg + "Traceback (most recent call last):\n" + text

    def format(
        self,
        disable_blacklist=False,
        max_nb_locals=-1,
        max_local_len=-1,
        show_locals=False,
    ):
        text = ""

        if show_locals and self._locals_capture_exc:
            msg = "Can't display local variables along stack trace, "
            msg += "error occurred during recovery:\n"
            msg += self._locals_capture_exc + "\n\n"
            text += indent(msg, "* ")

        # Stack traces formatting
        for exc in self._exc_info:
            text += self._format_stack(
                *exc,
                disable_blacklist,
                max_nb_locals,
                max_local_len,
                show_locals,
            )
        return text
