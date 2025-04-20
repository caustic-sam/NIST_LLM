# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Formatters for ANSI text"""


class ColorTags:
    PURPLE = "\033[95m"
    ORANGE = "\033[33m"
    CYAN = "\033[96m"
    DARKCYAN = "\033[36m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    WHITE = "\033[37m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    END = "\033[0m"


def __color_message(tag, msg):
    return "{0}{1}{2}".format(tag, msg, ColorTags.END)


def PURPLE(msg):
    return __color_message(ColorTags.PURPLE, msg)


def ORANGE(msg):
    return __color_message(ColorTags.ORANGE, msg)


def CYAN(msg):
    return __color_message(ColorTags.CYAN, msg)


def DARKCYAN(msg):
    return __color_message(ColorTags.DARKCYAN, msg)


def BLUE(msg):
    return __color_message(ColorTags.BLUE, msg)


def GREEN(msg):
    return __color_message(ColorTags.GREEN, msg)


def YELLOW(msg):
    return __color_message(ColorTags.YELLOW, msg)


def RED(msg):
    return __color_message(ColorTags.RED, msg)


def WHITE(msg):
    return __color_message(ColorTags.WHITE, msg)


def UNDERLINE(msg):
    return __color_message(ColorTags.UNDERLINE, msg)


def BOLD(msg):
    return __color_message(ColorTags.BOLD, msg)
