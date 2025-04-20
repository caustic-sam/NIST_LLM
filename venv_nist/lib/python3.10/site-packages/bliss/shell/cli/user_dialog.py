# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

""" Module providing dialogs to interact with the user """

from __future__ import annotations

import dataclasses
import typing


TextAlignment = typing.Literal["CENTER", "LEFT", "JUSTIFY", "RIGHT"]


@dataclasses.dataclass
class Widget:
    @property
    def wtype(self):
        ...

    def __hash__(self) -> int:
        return id(self)


_UserDlg = Widget


@dataclasses.dataclass()
class UserYesNo(Widget):
    """A simple question, expecting YES or NO as an answer"""

    name: str | None = None
    label: str = ""
    defval: bool = False

    @property
    def wtype(self):
        return "yesno"

    def __hash__(self) -> int:
        return id(self)


@dataclasses.dataclass()
class UserMsg(Widget):
    """A simple message (=label) to be displayed"""

    name: str | None = None
    label: str = ""
    text_align: TextAlignment = "LEFT"
    text_expand: bool = True

    @property
    def wtype(self):
        return "msg"

    def __hash__(self) -> int:
        return id(self)


@dataclasses.dataclass()
class UserInput(Widget):
    """Ask the user to enter/type a value (string, integer, float, ...)"""

    name: str | None = None
    label: str = ""
    defval: str = ""
    validator: Validator | None = None
    completer: list[str] | None = dataclasses.field(default=None, hash=False)
    text_align: TextAlignment = "LEFT"
    text_expand: bool = False

    @property
    def wtype(self):
        return "input"

    def __hash__(self) -> int:
        return id(self)


@dataclasses.dataclass()
class UserIntInput(Widget):
    """Ask the user to enter/type an integer value

    Arguments:
        defval: Actual value of the input
        minumum: Optional minimum value (included)
        maximum: Optional maximum value (included)
    """

    name: str | None = None
    label: str = ""
    defval: int = 0
    text_align: TextAlignment = "LEFT"
    text_expand: bool = False
    minimum: int | None = None
    maximum: int | None = None

    @property
    def wtype(self):
        return "int_input"

    def __hash__(self) -> int:
        return id(self)


@dataclasses.dataclass()
class UserFloatInput(Widget):
    """Ask the user to enter/type a float value

    Arguments:
        defval: Actual value of the input
        minimum: Optional minimum value (included)
        maximum: Optional maximum value (included)
    """

    name: str | None = None
    label: str = ""
    defval: float = 0.0
    text_align: TextAlignment = "LEFT"
    text_expand: bool = False
    minimum: float | None = None
    maximum: float | None = None

    @property
    def wtype(self):
        return "float_input"

    def __hash__(self) -> int:
        return id(self)


@dataclasses.dataclass()
class UserFileInput(Widget):
    """Ask the user to enter/type a value (string, integer, float, ...)"""

    name: str | None = None
    label: str = ""
    defval: str = ""
    validator: Validator | None = None
    completer = None
    text_align: TextAlignment = "LEFT"
    text_expand: bool = False

    @property
    def wtype(self):
        return "file_input"

    def __hash__(self) -> int:
        return id(self)


@dataclasses.dataclass()
class UserChoice(Widget):
    """Ask the user to select one value among values (radio list).

    This class is available for compatibility with old code.
    Prefer to use `UserChoice2` which behave in better way with
    `defval`.

    Arguments:
        label: a label on top of the radio list (optional).
        values: list of (value,label) tuples. ex: values = [(1,"choice1"), (2,"choice2"), (3,"choice3")]
        defval: the index of the value selected as default.
    """

    name: str | None = None
    label: str = ""
    values: list[tuple[typing.Any, str]] = dataclasses.field(
        default_factory=list, hash=False
    )
    defval: int = 0
    text_align: TextAlignment = "LEFT"
    text_expand: bool = True

    @property
    def wtype(self):
        return "choice"

    def __hash__(self) -> int:
        return id(self)


@dataclasses.dataclass()
class UserChoice2(Widget):
    """Ask the user to select one value among values (radio list).

    Arguments:
        label: a label on top of the radio list (optional).
        values: list of (value,label) tuples. ex: values = [(1,"choice1"), (2,"choice2"), (3,"choice3")]
        defval: the value to select as default. None to select nothing.
    """

    name: str | None = None
    label: str = ""
    values: list[tuple[typing.Any, str]] = dataclasses.field(
        default_factory=list, hash=False
    )
    defval: typing.Any = None
    text_align: TextAlignment = "LEFT"
    text_expand: bool = True

    @property
    def wtype(self):
        return "choice2"

    def __hash__(self) -> int:
        return id(self)


@dataclasses.dataclass()
class UserSelect(Widget):
    """Ask the user to select one value among values (button list).

    Arguments:
        label: a label on top of the widget (optional).
        values: list of (value,label) tuples. ex: values = [(1,"choice1"), (2,"choice2"), (3,"choice3")]
        defval: the value selected as default.
    """

    name: str | None = None
    label: str = ""
    values: list[tuple[typing.Any, str]] = dataclasses.field(
        default_factory=list, hash=False
    )
    defval: typing.Any | None = dataclasses.field(default=None, hash=False)
    text_align: TextAlignment = "LEFT"
    text_expand: bool = True

    @property
    def wtype(self):
        return "select"

    def __hash__(self) -> int:
        return id(self)


@dataclasses.dataclass()
class UserCheckBox(Widget):
    """Ask the user to enable or disable an option.

    Arguments:
        defval: the default values for the option (True=checked, False=unchecked).
    """

    name: str | None = None
    label: str = ""
    defval: bool = False

    @property
    def wtype(self):
        return "checkbox"

    def __hash__(self) -> int:
        return id(self)


@dataclasses.dataclass()
class UserCheckBoxList(Widget):
    """Ask the user to enable or disable a set of options.

    Arguments:
        values: The available values with label for each options: `[("id1", "Coffee")]`.
        defval: The default selected values
    """

    name: str | None = None
    label: str = ""
    values: list[tuple[typing.Any, str]] = dataclasses.field(
        default_factory=list, hash=False
    )
    defval: list[typing.Any] = dataclasses.field(default_factory=list, hash=False)

    @property
    def wtype(self):
        return "checkboxlist"

    def __hash__(self) -> int:
        return id(self)


ContainerDirection = typing.Literal["h", "v"]


@dataclasses.dataclass
class Container(Widget):
    dlgs: list[Widget] = dataclasses.field(hash=False)
    title: str | None = None
    border: int = 0
    padding: int = 0
    splitting: ContainerDirection = "h"

    @property
    def wtype(self):
        return "container"

    def __hash__(self) -> int:
        return id(self)


class Validator:
    def __init__(self, func, *args):
        self.func = func
        self.args = args

    def check(self, str_input):
        if self.args is not None:
            return self.func(str_input, *self.args)
        else:
            return self.func(str_input)


def is_int(str_input):
    return int(str_input)


def is_float(str_input):
    return float(str_input)


def in_frange(str_input, mini, maxi):
    val = float(str_input)

    if val < mini:
        raise ValueError("value %s < %s (mini)" % (val, mini))

    if val > maxi:
        raise ValueError("value %s > %s (maxi)" % (val, maxi))

    return val


check = {
    "int": Validator(is_int),
    "float": Validator(is_float),
    "frange": Validator(in_frange, 5, 10),
}


class BlissWizard:
    """
    Class to create users interactive configuration helpers.
    """

    def __init__(self, bliss_dlgs):
        self.dlgs = bliss_dlgs

    def show(self):
        allres = []

        for dlg in self.dlgs:
            ans = dlg.show()
            if ans is False:
                return False
            else:
                allres.append(ans)

        return allres
