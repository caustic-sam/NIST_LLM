# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Dialog handling.
"""

from __future__ import annotations
from typing import Literal

from bliss.shell.cli.user_dialog import Widget


def show_dialog(
    dialog: Widget | list[Widget] | list[list[Widget]],
    title: str = "Dialog",
    ok_text="Ok",
    cancel_text="Cancel",
) -> Literal[False] | dict[str | Widget, object]:
    """Execute a dialog description and returns it's result.

    Arguments:
        dialog: The dialog description
        title: The title of the dialog
        ok_text: The label of the ok button
        cancel_text: The label of the cancel button

    Result:
        A dictionaly, or False if the dialog was cancelled.
    """
    from bliss.shell.cli.pt_widgets import show_dialog as pt_show_dialog

    return pt_show_dialog(
        dialog=dialog, title=title, ok_text=ok_text, cancel_text=cancel_text
    )
