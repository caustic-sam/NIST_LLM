# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.application import get_app

from prompt_toolkit.filters import is_done, renderer_height_is_known, Condition
from prompt_toolkit.layout.containers import Window, ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEvent
from ptpython.utils import if_mousedown
from ptpython.layout import get_inputmode_fragments

from bliss.shell.cli import config as cli_config


def tmux_statusbar(python_input):
    """
    Create the `Layout` for the status bar.
    """
    TB = "class:status-toolbar"

    @if_mousedown
    def toggle_paste_mode(mouse_event):
        python_input.paste_mode = not python_input.paste_mode

    @if_mousedown
    def enter_history(mouse_event):
        python_input.enter_history()

    def get_text_fragments():
        python_buffer = python_input.default_buffer

        result: list[
            tuple[str, str] | tuple[str, str, Callable[[MouseEvent], object]]
        ] = []

        result.append((TB, " "))
        result.extend(get_inputmode_fragments(python_input))
        result.append((TB, " "))

        # Position in history.
        result.append(
            (
                TB,
                "%i/%i "
                % (python_buffer.working_index + 1, len(python_buffer._working_lines)),
            )
        )

        # Shortcuts.
        app = get_app()
        if (
            not python_input.vi_mode
            and app.current_buffer == python_input.search_buffer
        ):
            result.append((TB, "[Ctrl-G] Cancel search [Enter] Go to this position."))
        elif bool(app.current_buffer.selection_state) and not python_input.vi_mode:
            # Emacs cut/copy keys.
            result.append(
                (TB, "[Ctrl-W] Cut [Meta-W] Copy [Ctrl-Y] Paste [Ctrl-G] Cancel")
            )
        else:
            result.extend(
                [
                    (TB + " class:key", "[F3]", enter_history),
                    (TB, " History ", enter_history),
                    (TB, "[F5] Scans "),
                    (TB + " class:key", "[F6]", toggle_paste_mode),
                    (TB, " ", toggle_paste_mode),
                ]
            )

            if python_input.paste_mode:
                result.append(
                    (TB + " class:paste-mode-on", "Paste mode (on) ", toggle_paste_mode)
                )
            else:
                result.append((TB, "Paste mode ", toggle_paste_mode))

            result.append((TB, "[F7]"))
            if cli_config.typing_helper_active:
                result.append((TB + " class:paste-mode-on", " Typing helper (on) "))
            else:
                result.append((TB, " Typing helper "))
        return result

    return ConditionalContainer(
        content=Window(content=FormattedTextControl(get_text_fragments), style=TB),
        filter=~is_done
        & renderer_height_is_known
        & Condition(
            lambda: python_input.show_status_bar
            and not python_input.show_exit_confirmation
        ),
    )
