# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from prompt_toolkit.output import Output
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.data_structures import Size
from prompt_toolkit.styles import Attrs


class ProxyOutput(Output):
    """
    Proxy class to redirect the output interface for a
    :class:`~prompt_toolkit.renderer.Renderer` into another
    output.
    """

    def __init__(self, output: Output):
        self._output: Output = output

    def fileno(self) -> int:
        return self._output.fileno()

    def encoding(self) -> str:
        return self._output.encoding()

    def write(self, data: str) -> None:
        self._output.write(data)

    def write_raw(self, data: str) -> None:
        self._output.write_raw(data)

    def set_title(self, title: str) -> None:
        self._output.set_title(title)

    def clear_title(self) -> None:
        self._output.clear_title()

    def flush(self) -> None:
        self._output.flush()

    def erase_screen(self) -> None:
        self._output.erase_screen()

    def enter_alternate_screen(self) -> None:
        self._output.enter_alternate_screen()

    def quit_alternate_screen(self) -> None:
        self._output.quit_alternate_screen()

    def enable_mouse_support(self) -> None:
        self._output.enable_mouse_support()

    def disable_mouse_support(self) -> None:
        self._output.disable_mouse_support()

    def erase_end_of_line(self) -> None:
        self._output.erase_end_of_line()

    def erase_down(self) -> None:
        self._output.erase_down()

    def reset_attributes(self) -> None:
        self._output.reset_attributes()

    def set_attributes(self, attrs: Attrs, color_depth: ColorDepth) -> None:
        self._output.set_attributes(attrs, color_depth)

    def disable_autowrap(self) -> None:
        self._output.disable_autowrap()

    def enable_autowrap(self) -> None:
        self._output.enable_autowrap()

    def cursor_goto(self, row: int = 0, column: int = 0) -> None:
        self._output.cursor_goto(row, column)

    def cursor_up(self, amount: int) -> None:
        self._output.cursor_up(amount)

    def cursor_down(self, amount: int) -> None:
        self._output.cursor_down(amount)

    def cursor_forward(self, amount: int) -> None:
        self._output.cursor_forward(amount)

    def cursor_backward(self, amount: int) -> None:
        self._output.cursor_backward(amount)

    def hide_cursor(self) -> None:
        self._output.hide_cursor()

    def show_cursor(self) -> None:
        self._output.show_cursor()

    def set_cursor_shape(self, cursor_shape: CursorShape) -> None:
        self._output.set_cursor_shape(cursor_shape)

    def reset_cursor_shape(self) -> None:
        self._output.reset_cursor_shape()

    def ask_for_cpr(self) -> None:
        self._output.ask_for_cpr()

    @property
    def responds_to_cpr(self) -> bool:
        return self._output.responds_to_cpr

    def get_size(self) -> Size:
        return self._output.get_size()

    def bell(self) -> None:
        self._output.bell()

    def enable_bracketed_paste(self) -> None:
        self._output.enable_bracketed_paste()

    def disable_bracketed_paste(self) -> None:
        self._output.disable_bracketed_paste()

    def reset_cursor_key_mode(self) -> None:
        self._output.reset_cursor_key_mode()

    def scroll_buffer_to_prompt(self) -> None:
        self._output.scroll_buffer_to_prompt()

    def get_rows_below_cursor_position(self) -> int:
        return self._output.get_rows_below_cursor_position()

    def get_default_color_depth(self) -> ColorDepth:
        return self._output.get_default_color_depth()
