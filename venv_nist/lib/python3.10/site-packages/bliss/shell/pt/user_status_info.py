# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
User status based on prompt-toolkit.
"""

from __future__ import annotations
import typing
import contextlib
from bliss.common.user_status_info import UserStatusDisplay
from bliss.shell.standard import text_block
from prompt_toolkit.formatted_text import (
    to_formatted_text,
    merge_formatted_text,
)

if typing.TYPE_CHECKING:
    from bliss.shell.pt.text_block_app import TextBlock
    from prompt_toolkit.formatted_text import AnyFormattedText


class PtUserStatusDisplay(UserStatusDisplay):
    """Handle the display of user status info on the fly with
    prompt-toolkit."""

    def __init__(self):
        self._text_block: TextBlock | None = None

    @contextlib.contextmanager
    def use_display(self):
        """Context to setup the first use of the user status display"""
        try:
            with text_block() as tb:
                self._text_block = tb
                yield
        finally:
            self._text_block = None

    def trigger_callback(self, *values: typing.Any):
        """Called when the user state info was changed"""
        if self._text_block is not None:
            fragments: list[AnyFormattedText] = []
            for v in values:
                if len(fragments) != 0:
                    fragments.append(", ")
                fragments.append(to_formatted_text(v, auto_convert=True))
            text = merge_formatted_text(fragments)
            self._text_block.set_text(text, height=1)
