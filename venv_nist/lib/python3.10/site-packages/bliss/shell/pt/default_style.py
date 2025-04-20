# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Exposes the default styles used by the BLISS shell applications.
"""

from __future__ import annotations

from prompt_toolkit.styles import Style, BaseStyle, merge_styles


SHARED_STYLE = {
    # bliss.shell.getval
    "getval question": "bold",
    "getval valid_input": "",
    "getval prompt_char": "",
    "getval separator": "",
    "getval description": "",
    # bootstrap-like styles
    "bg-primary": "white bg:#0d6efd",
    "bg-secondary": "white bg:#6c757d",
    "bg-info": "black bg:#0dcaf0",
    "bg-success": "white bg:#198754",
    "bg-warning": "black bg:#ffc107",
    "bg-danger": "white bg:#dc3545",
    "bg-fatal": "white bg:#6610f2",
    "bg-light": "black bg:#f8f9fa",
    "bg-dark": "white bg:#212529",
    # table
    "header": "bold",
    # textblock
    "bottom-toolbar aborting": "bg:orange fg:black",
    "aborting bottom-toolbar.text": "bg:orange fg:black noreverse",
    "bottom-toolbar aborting2": "bg:red fg:black",
    "aborting2 bottom-toolbar.text": "bg:red fg:black noreverse",
}


DARK_STYLE = {
    # common html
    "h1": "white bold underline",
    "h2": "white bold",
    "h3": "white",
    "mark": "bg:#664d03",
    # bootstrap-like styles
    "primary": "ansibrightblue",
    "secondary": "#6c757d",
    "info": "#0dcaf0",
    "success": "#8AE234",
    "warning": "#ffc107",
    "danger": "#FF0000",
    "fatal": "#FF00FF",
    # abstract palette
    "color1": "ansibrightblue",
    "color2": "ansibrightyellow",
    "color3": "ansibrightgreen",
    "color4": "ansibrightmagenta",
    "color5": "ansibrightcyan",
    "color6": "ansibrightred",
}


LIGHT_STYLE = {
    # common html
    "h1": "black bold underline",
    "h2": "black bold",
    "h3": "black",
    "mark": "#664d03",
    # bootstrap-like styles
    "primary": "ansiblue",
    "secondary": "#343a40",
    "info": "#032830",
    "success": "#051b11",
    "warning": "#332701",
    "danger": "#2c0b0e",
    "fatal": "#140330",
    # abstract palette
    "color1": "ansiblue",
    "color2": "ansiyellow",
    "color3": "ansigreen",
    "color4": "ansimagenta",
    "color5": "ansicyan",
    "color6": "ansired",
}


_CACHE: dict[str, BaseStyle] = {}


def get_style() -> Style:
    global _CACHE
    from bliss import _get_current_session

    current_session = _get_current_session()
    name = "" if current_session is None else current_session.name

    style = _CACHE.get(name)
    if style is None:
        if current_session is not None:
            bliss_repl = current_session.bliss_repl
            theme_mode = "light" if bliss_repl is None else bliss_repl.theme_mode
        else:
            theme_mode = "light"
        style_dict = DARK_STYLE if theme_mode == "dark" else LIGHT_STYLE
        styles = [
            Style.from_dict(style_dict),
            Style.from_dict(SHARED_STYLE),
        ]
        style = merge_styles(styles)
        _CACHE[name] = style
    return style
