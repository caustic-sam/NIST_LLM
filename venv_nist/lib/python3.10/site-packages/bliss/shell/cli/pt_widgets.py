# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Module providing dialogs to interact with the user"""

from __future__ import annotations
from typing import Generic
from typing import TypeVar
from typing import Union
from typing import Literal

import functools
import subprocess
import gevent
import os
import contextlib

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app

# from prompt_toolkit.eventloop import run_in_executor
from prompt_toolkit.key_binding.bindings.focus import focus_next, focus_previous
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.key_binding.key_bindings import KeyBindings, merge_key_bindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout import containers
from prompt_toolkit.layout.containers import HSplit, VSplit, WindowAlign
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Dialog, Button, Label, Box, TextArea, Frame

from prompt_toolkit.mouse_events import MouseEventType

from prompt_toolkit.layout.containers import Float, FloatContainer, ConditionalContainer
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.styles import Style

from prompt_toolkit.completion import Completer, PathCompleter, WordCompleter

from prompt_toolkit.widgets import Checkbox as Checkbox_Orig
from prompt_toolkit.widgets import CheckboxList
from prompt_toolkit.widgets import RadioList  # as RadioList_Orig

from bliss.common.greenlet_utils import asyncio_gevent

from bliss.shell.cli import user_dialog
from bliss.common.deprecation import deprecated_warning
from bliss.shell.dialog.helpers import normalize_dialog


__all__ = [
    "yes_no_dialog",
    "button_dialog",
    "input_dialog",
    "message_dialog",
    "radiolist_dialog",
    "BlissDialog",
]


# ==== ESRF COLORS AND DEFAULT PROMPT TOOLKIT DIALOG COLORS ======

_ESRF_STYLE = Style.from_dict(
    {
        "warning-msg": "bg:#ec7404 #ffffff",  # ec7404 #aa0000
        "error_state": "bg:#ec7404 #ffffff",  # ec7404 #aa0000
        "dialog frame.label": "#ad007c bold",
        "dialog": "bg:#132577",
        "dialog.body text-area": "bg:#cfd1d2",
        "button.focused": "bg:#ad007c #ffffff",
        "helper": "bg:#ad007c #ffffff bold ",
    }
)


if 0:
    # ==== ESRF COLORS ======
    # PANTONE BLEU 2747C             = #132577

    # PANTONE COOL GRAY 1C (light)   = #f4f4f4
    # PANTONE COOL GRAY 4C (medium)  = #cfd1d2
    # PANTONE COOL GRAY 7C (darker)  = #b1b3b4

    # PANTONE 166C (orange 1)        = #ec7404
    # PANTONE 138C (orange 2)        = #f6a400
    # PANTONE 109C (yellow)          = #ffdd00
    # PANTONE 362C (green)           = #509f25
    # PANTONE 299C (light blue)      = #0097d3
    # PANTONE 2405C (purple)         = #ad007c

    # === DEFAULTPROMPT TOOLKIT DIALOG COLORS ===
    """
    WIDGETS_STYLE = [
        # Dialog windows.
        ('dialog',                                  'bg:#4444ff'),
        ('dialog.body',                             'bg:#ffffff #000000'),
        ('dialog.body text-area',                    'bg:#cccccc'),
        ('dialog.body text-area last-line',          'underline'),

        ('dialog frame.label',                      '#ff0000 bold'),

        # Scrollbars in dialogs.
        ('dialog.body scrollbar.background',        ''),
        ('dialog.body scrollbar.button',            'bg:#000000'),
        ('dialog.body scrollbar.arrow',             ''),
        ('dialog.body scrollbar.start',             'nounderline'),
        ('dialog.body scrollbar.end',               'nounderline'),

        # Buttons.
        ('button',                                  ''),
        ('button.arrow',                            'bold'),
        ('button.focused',                          'bg:#aa0000 #ffffff'),

        # Menu bars.
        ('menu-bar',                                'bg:#aaaaaa #000000'),
        ('menu-bar.selected-item',                  'bg:#ffffff #000000'),
        ('menu',                                    'bg:#888888 #ffffff'),
        ('menu.border',                             '#aaaaaa'),
        ('menu.border shadow',                      '#444444'),

        # Shadows.
        ('dialog shadow',                           'bg:#000088'),
        ('dialog.body shadow',                      'bg:#aaaaaa'),

        ('progress-bar',                             'bg:#000088'),
        ('progress-bar.used',                        'bg:#ff0000'),
    ]
    """


# ===================== PROMPT TOOLKIT PATCHING ==================


class Checkbox(Checkbox_Orig):
    """
    Modify prompt_toolkit Checkbox obj.

    Add a mouse handler to check ON or OFF the checkbox.
    """

    def _get_text_fragments(self):
        def mouse_handler(mouse_event):

            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                self.checked = not self.checked

        result = super()._get_text_fragments()

        # Add mouse handler to all fragments.
        for i in range(len(result)):
            result[i] = (result[i][0], result[i][1], mouse_handler)

        return result


# ================================================================


@contextlib.contextmanager
def _tmux_context(disable_tmux_mouse: bool = True):
    """
    Context to deal with tmux configuration.

    Arguments:
        disable_tmux_mouse: Disable the mouse in tmux, default is True
    """
    use_tmux = "TMUX" in os.environ
    try:
        if disable_tmux_mouse and use_tmux:
            subprocess.run(["tmux", "set-option", "-g", "mouse", "off"])
    except Exception:
        pass
    try:
        yield
    finally:
        try:
            if disable_tmux_mouse and use_tmux:
                subprocess.run(["tmux", "set-option", "-g", "mouse", "on"])
        except Exception:
            pass


def _run_dialog(
    dialog,
    style,
    extra_bindings=None,
    full_screen: bool = True,
    focus=None,
    disable_tmux_mouse: bool = True,
):
    """Turn the `Dialog` into an `Application` and run it.

    Arguments:
        focus: If defined, set the focus on a widget
        disable_tmux_mouse: Disable the mouse in tmux, default is True
    """
    with _tmux_context(disable_tmux_mouse=disable_tmux_mouse):
        application = _create_app(dialog, style, extra_bindings, full_screen)
        if focus is not None:
            application.layout.focus(focus)
        g_app = asyncio_gevent.future_to_greenlet(application.run_async())
        try:
            g_app.join()
        except BaseException:
            # Capture current greenlet exceptions (GreenletExit/timeout...)
            application.exit()
            g_app.join()
            raise
        return g_app.get()


def _create_app(dialog, style, extra_bindings=None, full_screen=True):
    # Key bindings.
    kb = load_key_bindings()
    merged_bindings = [kb]
    if extra_bindings is not None:
        merged_bindings.append(extra_bindings)

    return Application(
        layout=Layout(dialog),
        key_bindings=merge_key_bindings(merged_bindings),
        mouse_support=True,
        style=style,
        full_screen=full_screen,
    )


def _return_none():
    """Button handler that returns None."""
    get_app().exit()


# ==== BASIC DIALOGS =====================================================================


def yes_no_dialog(
    title="", text="", yes_text="Yes", no_text="No", style=None, full_screen=True
):
    """
    Display a Yes/No dialog.
    Return a boolean.
    """

    def yes_handler():
        get_app().exit(result=True)

    def no_handler():
        get_app().exit(result=False)

    dialog = Dialog(
        title=title,
        body=Label(text=text, dont_extend_height=True),
        buttons=[
            Button(text=yes_text, handler=yes_handler),
            Button(text=no_text, handler=no_handler),
        ],
        with_background=True,
    )

    return _run_dialog(dialog, style, full_screen=full_screen)


def button_dialog(title="", text="", buttons=[], style=None, full_screen=True):
    """
    Display a dialog with button choices (given as a list of tuples).
    Return the value associated with button.
    """

    def button_handler(v):
        get_app().exit(result=v)

    dialog = Dialog(
        title=title,
        body=Label(text=text, dont_extend_height=True),
        buttons=[
            Button(text=t, handler=functools.partial(button_handler, v))
            for t, v in buttons
        ],
        with_background=True,
    )

    return _run_dialog(dialog, style, full_screen=full_screen)


def input_dialog(
    title="",
    text="",
    ok_text="OK",
    cancel_text="Cancel",
    completer=None,
    password=False,
    style=None,
    full_screen=True,
):
    """
    Display a text input box.
    Return the given text, or None when cancelled.
    """

    def accept(buf):
        get_app().layout.focus(ok_button)
        return True  # Keep text.

    def ok_handler():
        get_app().exit(result=textfield.text)

    ok_button = Button(text=ok_text, handler=ok_handler)
    cancel_button = Button(text=cancel_text, handler=_return_none)

    textfield = TextArea(
        multiline=False,
        focus_on_click=True,
        password=password,
        completer=completer,
        accept_handler=accept,
    )

    dialog = Dialog(
        title=title,
        body=HSplit(
            [Label(text=text, dont_extend_height=True), textfield],
            padding=Dimension(preferred=1, max=1),
        ),
        buttons=[ok_button, cancel_button],
        with_background=True,
    )

    return _run_dialog(dialog, style, full_screen=full_screen)


def message_dialog(title="", text="", ok_text="Ok", style=None, full_screen=True):
    """
    Display a simple message box and wait until the user presses enter.
    """
    dialog = Dialog(
        title=title,
        body=Label(text=text, dont_extend_height=True),
        buttons=[Button(text=ok_text, handler=_return_none)],
        with_background=True,
    )

    return _run_dialog(dialog, style, full_screen=full_screen)


def radiolist_dialog(
    title="",
    text="",
    ok_text="Ok",
    cancel_text="Cancel",
    values=None,
    style=None,
    full_screen=True,
):
    """
    Display a simple list of element the user can choose amongst.

    Only one element can be selected at a time using Arrow keys and Enter.
    The focus can be moved between the list and the Ok/Cancel button with tab.
    """

    def ok_handler():
        get_app().exit(result=radio_list.current_value)

    radio_list = RadioList(values)

    dialog = Dialog(
        title=title,
        body=HSplit([Label(text=text, dont_extend_height=True), radio_list], padding=1),
        buttons=[
            Button(text=ok_text, handler=ok_handler),
            Button(text=cancel_text, handler=_return_none),
        ],
        with_background=True,
    )

    return _run_dialog(dialog, style, full_screen=full_screen)


def checkbox_dialog(
    title="", text="", ok_text="Ok", cancel_text="Cancel", style=None, full_screen=True
):
    """
    Display a checkbox.
    """

    def ok_handler():
        get_app().exit(result=cb.checked)

    cb = Checkbox(text)
    body = cb

    dialog = Dialog(
        title=title,
        body=body,
        buttons=[
            Button(text=ok_text, handler=ok_handler),
            Button(text=cancel_text, handler=_return_none),
        ],
        with_background=True,
    )

    return _run_dialog(dialog, style, full_screen=full_screen)


def checkboxlist_dialog(
    title="",
    text="",
    ok_text: str = "Ok",
    cancel_text: str = "Cancel",
    values=None,
    selection=None,
    style=None,
    full_screen=True,
):
    """
    Display a simple list of element the user can choose multiple values amongst.

    Several elements can be selected at a time using Arrow keys and Enter.
    The focus can be moved between the list and the Ok/Cancel button with tab.
    """
    if values is None:
        values = []

    def ok_handler() -> None:
        get_app().exit(result=cb_list.current_values)

    cb_list = CheckboxList(values)
    if selection is not None:
        cb_list.current_values.extend(selection)

    dialog = Dialog(
        title=title,
        body=HSplit([Label(text=text, dont_extend_height=True), cb_list], padding=1),
        buttons=[
            Button(text=ok_text, handler=ok_handler),
            Button(text=cancel_text, handler=_return_none),
        ],
        with_background=True,
    )

    return _run_dialog(dialog, style, full_screen=full_screen)


def select_dialog(
    values: list[object], title: str = "Select dialog", cancel_text: str = "Cancel"
):
    """
    Display a dialog with button choices (given as a list of object).

    Returns the selected value.

    Arguments:
        values: List of available object to select
        title: Title for the dialog

    Returns:
        The selected dialog class else None if the dialog was cancelled.
    """
    desc = [(v, str(v).capitalize()) for v in values]
    return display(user_dialog.UserSelect(name="selection", values=desc))


# ===========================================================================================


def display(user_dlg, title="", full_screen=True):
    """Display a single widget in a dialog application"""

    dlg = None

    if user_dlg.label is None:
        text = ""
    else:
        text = user_dlg.label

    if user_dlg.wtype == "msg":
        dlg = message_dialog(
            title, text, ok_text="Ok", style=_ESRF_STYLE, full_screen=full_screen
        )

    elif user_dlg.wtype == "yesno":
        dlg = yes_no_dialog(
            title,
            text,
            yes_text="Yes",
            no_text="No",
            style=_ESRF_STYLE,
            full_screen=full_screen,
        )

    elif user_dlg.wtype in ["input", "file_input"]:

        completer: Completer | None
        if user_dlg.wtype == "file_input":
            if user_dlg.completer is None:
                completer = PathCompleter()
            else:
                completer = WordCompleter(user_dlg.completer, ignore_case=True)

        elif user_dlg.completer is not None:
            completer = WordCompleter(user_dlg.completer, ignore_case=True)

        else:
            completer = None

        dlg = input_dialog(
            title,
            text,
            ok_text="OK",
            cancel_text="Cancel",
            completer=completer,
            password=False,
            style=_ESRF_STYLE,
            full_screen=full_screen,
        )

    elif user_dlg.wtype == "choice":
        values = user_dlg.values
        dlg = radiolist_dialog(
            title,
            text,
            ok_text="Ok",
            cancel_text="Cancel",
            values=values,
            style=_ESRF_STYLE,
            full_screen=full_screen,
        )

    elif user_dlg.wtype == "checkbox":
        dlg = checkbox_dialog(
            title,
            text,
            ok_text="Ok",
            cancel_text="Cancel",
            style=_ESRF_STYLE,
            full_screen=full_screen,
        )

    elif user_dlg.wtype == "checkboxlist":
        dlg = checkboxlist_dialog(
            title,
            text,
            ok_text="Ok",
            cancel_text="Cancel",
            style=_ESRF_STYLE,
            full_screen=full_screen,
        )

    elif user_dlg.wtype == "select":
        dialog = BlissDialog(
            [[user_dlg]],
            title=title,
            ok_text=None,
            cancel_text="Cancel",
            shortcut=True,
        )
        result = dialog.show()
        if result is False:
            return None
        return result["selection"]

    else:
        raise RuntimeError(f"No dialog found for wtype={user_dlg.wtype}")

    return dlg


class ResultNotValid(Exception):
    """Returns when a dialog result is not valid"""

    pass


USER_DLG = TypeVar("USER_DLG")
WDATA = TypeVar("WDATA", bound=containers.AnyContainer)


class _PtWrapper(Generic[USER_DLG, WDATA]):
    """Wrapper to convert BLISS user dialog descriptions into PtPython widget"""

    def __init__(self, user_dlg: USER_DLG, boss: BlissDialog):
        self.dlg = user_dlg
        self.boss = boss
        self.error_state = False
        self.wdata: WDATA = self.build_widget(self.dlg)
        self.body: containers.AnyContainer = self.build_body(self.dlg)

    def get_result(self) -> object:
        """Returns the dialog result

        Raises:
            ResultNotValid
        """
        return None

    def defval_from_result(self, result):
        """Convert the result produced by this widget as a new defval for a user
        dialog"""
        return result

    def build_body(self, dlg: USER_DLG) -> containers.AnyContainer:
        """Build the pt python widget holding the whole widget"""
        return self.wdata

    def build_widget(self, dlg: USER_DLG) -> WDATA:
        """Build the pt python widget holding this dialog value"""
        raise NotImplementedError

    def has_result(self):
        """If true, the widget is a user input which returns a value"""
        return True


class _PtWrapperMsg(_PtWrapper):
    def build_widget(self, dlg: user_dialog.UserMsg):
        body = Label(
            text=dlg.label,
            dont_extend_height=True,
            dont_extend_width=not dlg.text_expand,
        )
        if dlg.text_align is not None:
            body.window.align = dlg.text_align
        return body

    def has_result(self):
        """If true, the widget is a user input which returns a value"""
        return False


USER_INPUT_DLG = TypeVar(
    "USER_INPUT_DLG",
    bound=Union[
        user_dialog.UserInput,
        user_dialog.UserFileInput,
        user_dialog.UserIntInput,
        user_dialog.UserFloatInput,
    ],
)


class _PtWrapperInput(_PtWrapper[USER_INPUT_DLG, TextArea], Generic[USER_INPUT_DLG]):
    """Implementation of a UserInput"""

    def __init__(self, user_dlg: USER_INPUT_DLG, boss=None):
        _PtWrapper.__init__(self, user_dlg, boss=boss)

    def build_widget(
        self,
        dlg: USER_INPUT_DLG,
    ):
        completer = self._build_completer(dlg)

        def get_style():
            if self.error_state:
                return "class:error_state"
            else:
                return "class:text-area"

        wdata = TextArea(
            multiline=False,
            focus_on_click=True,
            # password=password,
            completer=completer,
            complete_while_typing=True,
            accept_handler=self._accept,
        )

        wdata.window.style = get_style

        # set initial text
        wdata.text = str(dlg.defval)
        # and set cursor to end of line
        buff = wdata.buffer
        pos = buff.document.get_end_of_line_position()
        buff._set_cursor_position(buff.cursor_position + pos)
        return wdata

    def build_body(
        self,
        dlg: USER_INPUT_DLG,
    ):
        sub_body: list = []

        if dlg.label is not None:
            msg = dlg.label
            if dlg.label not in ["", " "]:
                msg += " "

            wlabel = Label(
                text=msg,
                dont_extend_height=True,
                dont_extend_width=not dlg.text_expand,
            )

            if dlg.text_align is not None:
                wlabel.window.align = dlg.text_align

            sub_body.append(wlabel)

        # === BINDINGS ==================================================
        def comp_next(event):
            "Initialize autocompletion, or select the next completion."
            buff = get_app().current_buffer
            if buff.complete_state:
                buff.complete_next()
            else:
                buff.start_completion(select_first=False)

        def comp_prev(event):
            "Initialize autocompletion, or select the next completion."
            buff = get_app().current_buffer
            if buff.complete_state:
                buff.complete_previous()
            else:
                buff.start_completion(select_first=False)

        kb = KeyBindings()
        kb.add("c-space")(comp_next)
        kb.add("up")(comp_prev)
        kb.add("down")(comp_next)

        # === ENABLE VALIDATOR CHECK WHEN LIVING A TEXT AREA FIELD ===

        def focus_next_wdg(event):
            if self.check_input(self.get_input()):
                get_app().layout.focus_next()
                # buff = get_app().current_buffer
                # pos = buff.document.get_end_of_line_position()
                # buff._set_cursor_position(buff.cursor_position + pos)

        def focus_previous_wdg(event):
            if self.check_input(self.get_input()):
                get_app().layout.focus_previous()
                # buff = get_app().current_buffer
                # pos = buff.document.get_end_of_line_position()
                # buff._set_cursor_position(buff.cursor_position + pos)

        kb.add("tab")(focus_next_wdg)
        kb.add("s-tab")(focus_previous_wdg)

        # === MAKE BODY LIST =========================================
        sub_body.append(self.wdata)
        body = VSplit(sub_body, key_bindings=kb)

        return body

    def _accept(self, buf):
        """Called while pressing enter in a TextArea field"""
        if self.check_input(buf.text):
            get_app().layout.focus_next()
        return True

    def _build_completer(self, dlg: USER_INPUT_DLG):
        return None

    def result_from_input(self, str_input: str):
        """Convert the user input stored inside the pt widget into a meaningful
        result"""
        raise NotImplementedError

    def get_input(self):
        return self.wdata.text

    def check_input(self, str_input: str) -> bool:
        """
        Check the validity of this text input and update the UI.

        Returns:
            True if the widget contains a valid result
        """
        try:
            self.result_from_input(str_input)
            self.error_state = False

            try:
                self.boss.clear_error(self)
            except Exception:
                pass

            return True

        except ValueError as e:
            self.error_state = True

            try:
                msg = f"!!! {type(e).__name__}: {e} !!!"  # {self.dlg.label}
                self.boss.set_error(msg, self)
            except Exception:
                pass

            return False

        else:
            # self.error_state = False
            return True

    def get_result(self) -> object:
        """Returns the dialog result

        Raises:
            ResultNotValid
        """
        res = self.get_input()
        if not self.check_input(res):
            raise ResultNotValid
        try:
            result = self.result_from_input(res)
        except Exception:
            raise ResultNotValid
        return result


class _PtWrapperTextInput(_PtWrapperInput):
    def result_from_input(self, str_input: str):
        """Convert the user input stored inside the pt widget into a meaningful
        result"""
        validator = self.dlg.validator
        if validator:
            validator.check(str_input)
        return str_input

    def _build_completer(self, dlg: user_dialog.UserInput):
        if dlg.completer is None:
            return None
        return WordCompleter(dlg.completer, ignore_case=True)


class _PtWrapperIntInput(_PtWrapperInput):
    def result_from_input(self, str_input: str):
        """
        Convert the text input into the widget result.

        Raises:
            ValueError: If the str_input is wrong
        """
        val = int(str_input)
        mini = self.dlg.minimum
        if mini is not None and val < mini:
            raise ValueError("value %s < %s (mini)" % (val, mini))
        maxi = self.dlg.maximum
        if maxi is not None and val > maxi:
            raise ValueError("value %s > %s (maxi)" % (val, maxi))
        return val


class _PtWrapperFloatInput(_PtWrapperInput):
    def result_from_input(self, str_input: str):
        """
        Convert the text input into the widget result.

        Raises:
            ValueError: If the str_input is wrong
        """
        val = float(str_input)
        mini = self.dlg.minimum
        if mini is not None and val < mini:
            raise ValueError("value %s < %s (mini)" % (val, mini))
        maxi = self.dlg.maximum
        if maxi is not None and val > maxi:
            raise ValueError("value %s > %s (maxi)" % (val, maxi))
        return val


class _PtWrapperFileInput(_PtWrapperInput):
    """Implementation of a UserMsg"""

    def result_from_input(self, str_input: str):
        """Convert the user input stored inside the pt widget into a meaningful
        result"""
        validator = self.dlg.validator
        if validator:
            validator.check(str_input)
        return str_input

    def _build_completer(self, dlg: user_dialog.UserFileInput):
        if dlg.completer is None:
            return PathCompleter()
        else:
            return WordCompleter(dlg.completer, ignore_case=True)


class _PtWrapperChoice(_PtWrapper):
    """Implementation of a UserChoice"""

    def __init__(self, user_dlg: USER_DLG, boss=None):
        _PtWrapper.__init__(self, user_dlg, boss=boss)

    def build_widget(self, dlg: user_dialog.UserChoice):
        wdata = RadioList(dlg.values)
        if dlg.defval >= 0 and dlg.defval < len(dlg.values):
            wdata.current_value = dlg.values[dlg.defval][0]
            wdata._selected_index = dlg.defval
        return wdata

    def build_body(self, dlg: user_dialog.UserChoice):
        sub_body: list = []
        if dlg.label is not None:
            wlabel = Label(
                text=dlg.label + " ",
                dont_extend_height=True,
                dont_extend_width=not dlg.text_expand,
            )

            if dlg.text_align is not None:
                wlabel.window.align = dlg.text_align

            sub_body.append(wlabel)

        sub_body.append(self.wdata)
        body = HSplit(sub_body)
        return body

    def get_result(self):
        return self.wdata.current_value

    def defval_from_result(self, result):
        """
        The defval for a UserChoice is the index, not the value
        """
        return [i[0] for i in self.dlg.values if i[0] is result][0]


class _PtWrapperChoice2(_PtWrapper):
    """Implementation of a UserChoice2"""

    def __init__(self, user_dlg: USER_DLG, boss=None):
        _PtWrapper.__init__(self, user_dlg, boss=boss)

    def build_widget(self, dlg: user_dialog.UserChoice2):
        wdata = RadioList(dlg.values)
        if dlg.defval is not None:
            keys = [i[0] for i in dlg.values]
            try:
                index = keys.index(dlg.defval)
            except ValueError:
                pass
            else:
                wdata.current_value = dlg.defval
                wdata._selected_index = index
        return wdata

    def build_body(self, dlg: user_dialog.UserChoice2):
        sub_body: list = []
        if dlg.label is not None:
            wlabel = Label(
                text=dlg.label + " ",
                dont_extend_height=True,
                dont_extend_width=not dlg.text_expand,
            )

            if dlg.text_align is not None:
                wlabel.window.align = dlg.text_align

            sub_body.append(wlabel)

        sub_body.append(self.wdata)
        body = HSplit(sub_body)
        return body

    def get_result(self):
        return self.wdata.current_value


class _PtWrapperSelect(_PtWrapper):
    """Implementation of a UserSelect"""

    def __init__(self, user_dlg: user_dialog.UserSelect, boss=None):
        _PtWrapper.__init__(self, user_dlg, boss=boss)
        self._result = None

    def build_widget(self, dlg: user_dialog.UserSelect):
        key_bindings = KeyBindings()
        key_bindings.add("up")(focus_previous)
        key_bindings.add("down")(focus_next)

        if len(dlg.values) == 0:
            max_size = 0
        else:
            max_size = max([len(str(d[1])) for d in dlg.values])

        rows: list = []

        if dlg.label is not None:
            wlabel = Label(
                text=dlg.label + " ",
                dont_extend_height=True,
                dont_extend_width=not dlg.text_expand,
            )
            if dlg.text_align is not None:
                wlabel.window.align = dlg.text_align
            rows.append(wlabel)

        def button_handler(key, event=None):
            self._result = key
            self.boss.accept()

        first_shortcut = self.boss._alloc_shortcut()

        focus = None
        for nb, (key, label) in enumerate(dlg.values):
            on_activate = functools.partial(button_handler, key)

            row: object
            if label is not None:
                label_button = str(label)

                if first_shortcut is not None:
                    shortcut = (
                        first_shortcut if nb == 0 else self.boss._alloc_shortcut()
                    )
                    if shortcut is not None:
                        self.boss.key_bindings.add(
                            shortcut, filter=_not_on_an_active_input_control
                        )(on_activate)
                        label = f"{shortcut}."
                    else:
                        label = "#."
                else:
                    label = ""

                button = Button(
                    text=label_button,
                    handler=on_activate,
                    width=len(label_button) + 4,
                )
                if key is dlg.defval:
                    focus = button

                empty = containers.Window(
                    style="class:frame.border",
                    width=max_size - len(label_button),
                    height=1,
                )
                num_label = Label(label)
                row = VSplit(
                    [num_label, button, empty],
                    align=containers.HorizontalAlign.LEFT,
                )
            else:
                row = Label("")

            rows.append(row)

        if focus is not None:
            self.boss._request_initial_focus(focus)

        body = HSplit(rows, key_bindings=key_bindings)
        return Box(body)

    def get_result(self):
        return self._result


class _PtWrapperCheckbox(_PtWrapper):
    """Implementation of a UserCheckBox"""

    def build_widget(self, dlg: user_dialog.UserCheckBox):
        body = Checkbox(dlg.label)
        body.checked = bool(dlg.defval)
        return body

    def get_result(self):
        return self.wdata.checked


class _PtWrapperCheckboxList(_PtWrapper):
    """Implementation of a UserCheckBoxList"""

    def build_widget(self, dlg: user_dialog.UserCheckBoxList):
        body = CheckboxList(dlg.values)
        body.current_values.extend(dlg.defval)
        return body

    def get_result(self):
        return self.wdata.current_values


class _PtWrapperContainer(_PtWrapper):
    def build_widget(self, dlg: user_dialog.Container):
        z_list = []
        for subdlg in dlg.dlgs:
            subbody = self.boss.create_pt_widget(subdlg)
            z_list.append(subbody)

        body: containers.AnyContainer
        if dlg.splitting == "h":
            body = HSplit(z_list, padding=dlg.padding)
        else:
            body = VSplit(z_list, padding=dlg.padding)

        if dlg.border:
            body = Box(body, padding=dlg.border)

        if dlg.title is not None:
            body = Frame(body, dlg.title)
        return body

    def has_result(self):
        """If true, the widget is a user input which returns a value"""
        return False


@Condition
def _not_on_an_active_input_control() -> bool:
    """
    True only if the active control is not a text input.
    """
    current = get_app().layout.current_control
    try:
        # Implementation specific of the TextArea widget
        return "text-area" not in current.input_processors[2].style
    except Exception:
        return True


class BlissDialog(Dialog):
    """Bliss user dialog with prompt-toolkit implementation

    Arguments:
        shortcut: If true, some widgets will be displayed with keyboard shortcut
    """

    WRAPPERS = {
        "msg": _PtWrapperMsg,
        "input": _PtWrapperTextInput,
        "int_input": _PtWrapperIntInput,
        "float_input": _PtWrapperFloatInput,
        "file_input": _PtWrapperFileInput,
        "choice": _PtWrapperChoice,
        "choice2": _PtWrapperChoice2,
        "select": _PtWrapperSelect,
        "checkbox": _PtWrapperCheckbox,
        "checkboxlist": _PtWrapperCheckboxList,
        "container": _PtWrapperContainer,
    }

    def __init__(
        self,
        user_dlg_list,
        title="BlissDialog",
        ok_text: str | None = "OK",
        cancel_text="Cancel",
        style=_ESRF_STYLE,
        paddings=(1, 1),
        show_help=False,
        disable_tmux_mouse=True,
        shortcut: bool = True,
    ):
        self.user_dlg: user_dialog.Widget = normalize_dialog(user_dlg_list)
        self.style = style
        self.paddings = paddings
        self.show_error = False
        self.show_help = show_help
        self.disable_tmux_mouse = disable_tmux_mouse
        self._use_shortcut = shortcut
        self._last_shortcut = 0

        self._requested_focus = None
        """Store the widget while will have the initial focus, if one"""

        self.flatten_wdlg_list: list[_PtWrapper] = []

        buttons = []
        ok_but = None
        if ok_text is not None:
            ok_but = Button(text=ok_text, handler=self.accept)
            buttons.append(ok_but)
        if self._use_shortcut:
            cancel_text = "0. " + cancel_text
        cancel_but = Button(text=cancel_text, handler=self.reject)
        buttons.append(cancel_but)

        self.extra_bindings = KeyBindings()

        body = self._build()

        super().__init__(title=title, body=body, buttons=buttons, with_background=True)

        def focus_buttons(event):
            focus = None
            if ok_but is not None:
                focus = ok_but
            elif cancel_but is not None:
                focus = cancel_but
            if focus is not None:
                get_app().layout.focus(focus.window)

        def reject_handle(event):
            self.reject()

        self.extra_bindings.add("end")(focus_buttons)
        self.extra_bindings.add("c-c")(reject_handle)
        self.extra_bindings.add("down", filter=~has_completions)(focus_next)
        self.extra_bindings.add("up", filter=~has_completions)(focus_previous)
        self.extra_bindings.add("escape")(reject_handle)
        if self._use_shortcut:
            self.extra_bindings.add("0", filter=_not_on_an_active_input_control)(
                reject_handle
            )

    @property
    def key_bindings(self):
        return self.extra_bindings

    def _request_initial_focus(self, widget):
        """Request the focus on a pt widget at start.

        It can be called by the wrappers.

        If a widget was already set, this call is ignored
        """
        if self._requested_focus is None:
            self._requested_focus = widget

    def _alloc_shortcut(self) -> str | None:
        """Allocate and returns a keyboard shortcut, else None"""
        if not self._use_shortcut:
            return None
        self._last_shortcut += 1
        if self._last_shortcut > 9:
            return None
        return f"{self._last_shortcut}"

    def _return_and_close(self, results=False):
        get_app().exit(result=results)

    def _get_results(self) -> dict | None:
        """
        Construct a result dict from the user menues.

        Raises:
            ResultNotValid: when one of the widgets contains non valid result
        """
        results = {}

        for wdlg in self.flatten_wdlg_list:
            if not wdlg.has_result():
                continue

            # NOTE: this can raise ResultNotValid and abort validation of the dialog
            res = wdlg.get_result()

            # store the results in a dict
            results[wdlg.dlg] = res
            results[wdlg.dlg.name] = res

        # SECOND LOOP, NOW WE ARE SURE THAT ALL VALUES ARE OK
        # WE SET DEFVAL TO RES VALUE
        for wdlg in self.flatten_wdlg_list:
            if wdlg.dlg in results:
                res = results[wdlg.dlg]
                wdlg.dlg.defval = wdlg.defval_from_result(res)

        return results

    def accept(self):
        """Try to close the dialog as accepted.

        The result value is a dict containing values for each user dialogs.

        If the validation fails, the close is cancelled.
        """
        try:
            results = self._get_results()
        except ResultNotValid:
            return
        self._return_and_close(results)

    def reject(self):
        """Close the dialog as rejected.

        The result value is `False`.
        """
        self._return_and_close(False)

    def ok_handler(self):
        deprecated_warning(
            kind="method",
            name="ok_handler",
            replacement="accept",
            since_version="2.0",
        )
        self.accept()

    def cancel_handler(self):
        deprecated_warning(
            kind="method",
            name="cancel_handler",
            replacement="reject",
            since_version="2.0",
        )
        self.reject()

    def set_error(self, msg, wdlg):
        self.error_label.text = msg
        self.show_error = True
        wdlg.error_state = True

    def clear_error(self, wdlg):
        self.error_label.text = ""
        self.show_error = False
        wdlg.error_state = False

    def create_pt_widget(self, dlg: user_dialog._UserDlg):
        """Create a pt widget from a user dialog."""
        wrapper_class = self.WRAPPERS.get(dlg.wtype)
        if wrapper_class is None:
            raise NotImplementedError(f"Dialog {type(dlg)} have no implementation")

        widget = wrapper_class(dlg, boss=self)
        zbody = widget.body

        if widget.has_result():
            self.flatten_wdlg_list.append(widget)

        return zbody

    def _build(self):
        y_list: list = []

        # === Add bindings helper ====
        if self.show_help:
            bh = Label(
                text=" >>> Next/Prev: tab/s-tab | Completion: up/down | Validate: enter | OK: end | Cancel: c-c <<< ",
                dont_extend_height=True,
                dont_extend_width=True,
                style="class:helper",
            )
            bh.window.align = WindowAlign.LEFT

            y_list.append(bh)

        content = self.create_pt_widget(self.user_dlg)
        y_list.append(content)

        self.error_label = Label(
            text="Error",
            dont_extend_height=True,
            dont_extend_width=True,
            style="class:warning-msg",
        )

        self.error_label.window.align = WindowAlign.LEFT

        cc = ConditionalContainer(
            content=self.error_label, filter=Condition(lambda: self.show_error)
        )

        y_list.append(cc)

        body = HSplit(y_list, padding=self.paddings[0])

        fbody = FloatContainer(
            content=body,
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=16, scroll_offset=1),
                )
            ],
        )

        return fbody

    def show(
        self, full_screen=True
    ) -> Literal[False] | dict[str | user_dialog.Widget, object]:
        """Show the dialog and then returns its result

        If the dialog is accepted, each user dialog `defval` are also updated
        with each user dialog
        selection.

        Results:
            A dictionary mapping user dialog instance, and user dialog name with
            the user dialog selection.
        """
        gevent.spawn(self._after_launch, 0.1)

        ans = _run_dialog(
            self,
            self.style,
            extra_bindings=self.extra_bindings,
            full_screen=full_screen,
            focus=self._requested_focus,
            disable_tmux_mouse=self.disable_tmux_mouse,
        )

        return ans

    def _after_launch(self, delay=0.1):
        gevent.sleep(delay)
        if self._requested_focus is None:
            # Put the focus at an initial unknown place
            get_app().layout.focus_next()
            get_app().layout.focus_last()


def show_dialog(
    dialog: user_dialog.Widget
    | list[user_dialog.Widget]
    | list[list[user_dialog.Widget]],
    title: str = "Dialog",
    ok_text="Ok",
    cancel_text="Cancel",
) -> Literal[False] | dict[str | user_dialog.Widget, object]:
    if not isinstance(dialog, (user_dialog.Container, list)):
        return display(dialog, title=title)

    dlg = BlissDialog(
        dialog,
        title=title,
        ok_text=ok_text,
        cancel_text=cancel_text,
        shortcut=True,
    )

    return dlg.show()
