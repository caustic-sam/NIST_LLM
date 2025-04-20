# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations
from typing import Any, Literal, cast
from typing_extensions import TypeAlias

import dataclasses
import numbers
from prompt_toolkit import HTML, ANSI
from prompt_toolkit.formatted_text import FormattedText, to_formatted_text
from .number import number_widths
from wcwidth import wcswidth

_COL_SPACING = 2

_Align: TypeAlias = Literal["default", "right", "left", "center", None]
_NumAlign: TypeAlias = Literal["default", "right", "left", "decimal", "center", None]


@dataclasses.dataclass
class Cell:
    """Hold the formatting of a cell of the table"""

    fragments: list[tuple[str, str]]

    is_number: bool = False

    is_separator: bool = False

    def __len__(self) -> int:
        return sum([wcswidth(t[1]) for t in self.fragments])


def separator(char: str = "-") -> Cell:
    return Cell(fragments=[("", char)], is_separator=True)


class Table:
    def __init__(self):
        self.stralign: _Align = "default"
        self.numalign: _NumAlign = "default"
        self.floatfmt: str = "g"
        self._col_widths: list[int] = []
        self._col_integers: list[int] = []
        self._col_decimals: list[int] = []

    def _int_to_cell(self, value: numbers.Integral, style="") -> Cell:
        return Cell([(style, f"{value:d}")], is_number=True)

    def _float_to_cell(self, value: numbers.Real, style="") -> Cell:
        self.floatfmt
        return Cell([(style, f"{value:{self.floatfmt}}")], is_number=True)

    def _to_cell(self, data: Any) -> Cell:
        if isinstance(data, Cell):
            return data
        if data is None:
            return Cell([("", "")])
        if isinstance(data, numbers.Integral):
            return self._int_to_cell(data)
        if isinstance(data, numbers.Real):
            return self._float_to_cell(data)
        if isinstance(data, tuple):
            if len(data) == 2:
                style, value = data
                assert isinstance(style, str)
                if isinstance(value, numbers.Integral):
                    return self._int_to_cell(value, style)
                elif isinstance(value, numbers.Real):
                    return self._float_to_cell(value, style)
                return Cell(fragments=[(style, str(value))])
        cell = to_formatted_text(data)
        fragments = cast(list[tuple[str, str]], cell)
        return Cell(fragments=fragments)

    def _normalize_data(
        self,
        tabular_data: list[
            list[
                str
                | int
                | float
                | tuple[str, int | float | str]
                | FormattedText
                | HTML
                | ANSI
            ]
        ],
    ) -> list[list[Cell]]:
        nb_col = max([len(c) for c in tabular_data])
        table: list[list[Cell]] = []
        for irow in range(len(tabular_data)):
            row: list[Cell] = []
            table.append(row)
            for icol, data in enumerate(tabular_data[irow]):
                cell = self._to_cell(data)
                row.append(cell)
            for icol in range(len(row), nb_col):
                row.append(Cell([("", "")]))
        return table

    def _format_number(self, cell: Cell, col: int) -> list[tuple[str, str]]:
        value = cell.fragments[0][1]
        widths = number_widths(value)
        integers = self._col_integers[col] - widths[0]
        decimals = self._col_decimals[col] - widths[1]
        text = " " * integers + value + " " * decimals
        return [(cell.fragments[0][0], text)]

    def _format_cell(self, cell: Cell, col: int, width: int) -> list[tuple[str, str]]:
        align: _Align
        if cell.is_separator:
            f = cell.fragments[0]
            return [(f[0], f[1][0] * width)]
        if cell.is_number:
            if self.numalign == "decimal":
                cell_fragments = self._format_number(cell, col)
                align = "right"
            else:
                cell_fragments = cell.fragments
                if self.numalign == "default":
                    align = self.stralign
                else:
                    align = self.numalign
            actual_width = len(cell_fragments[0][1])
        else:
            cell_fragments = cell.fragments
            align = self.stralign
            actual_width = len(cell)

        diff = width - actual_width
        if diff <= 0:
            return cell_fragments

        fragments: list[tuple[str, str]] = []

        if align in ["left", "default", None]:
            fragments.extend(cell_fragments)
            fragments.append(("", " " * diff))
        elif align == "right":
            fragments.append(("", " " * diff))
            fragments.extend(cell_fragments)
        elif align in ["center", None]:
            left = diff // 2
            right = diff - left
            fragments.append(("", " " * left))
            fragments.extend(cell_fragments)
            fragments.append(("", " " * right))
        else:
            raise ValueError(f"Unsupported {align=}")

        return fragments

    def format(
        self,
        tabular_data: list[
            list[
                str
                | int
                | float
                | tuple[str, int | float | str]
                | FormattedText
                | HTML
                | ANSI
            ]
        ],
    ) -> FormattedText:
        table: list[list[Cell]] = self._normalize_data(tabular_data)
        col_nb = len(table[0])

        def iter_col(index):
            for row in table:
                yield row[index]

        self._col_widths = []
        self._col_integers = []
        self._col_decimals = []

        for index in range(col_nb):
            cells = list(iter_col(index))
            width = max([len(c) for c in cells])
            if self.numalign == "decimal":
                num_cells = [c for c in cells if c.is_number]
                num_widths = [number_widths(c.fragments[0][1]) for c in num_cells]
                integers = max([w[0] for w in num_widths])
                decimals = max([w[1] for w in num_widths])
                self._col_integers.append(integers)
                self._col_decimals.append(decimals)
                self._col_widths.append(max(width, integers + decimals))
            else:
                self._col_widths.append(width)

        fragments: list[tuple[str, str]] = []
        for row in table:
            if len(fragments) != 0:
                fragments.append(("", "\n"))
            for icol, col in enumerate(row):
                if icol != 0:
                    fragments.append(("", " " * _COL_SPACING))
                cell = self._format_cell(col, col=icol, width=self._col_widths[icol])
                fragments.extend(cell)
        return FormattedText(fragments)


def tabulate(
    tabular_data: list[
        list[
            str
            | int
            | float
            | tuple[str, int | float | str]
            | FormattedText
            | HTML
            | ANSI
        ]
    ],
    tablefmt: str = "plain",
    stralign: _Align = "default",
    numalign: _NumAlign = "default",
    floatfmt: str = "g",
) -> FormattedText:
    """Format a fixed width table for pretty printing supporting prompt toolkit
    styles.

    The prompt toolkit types `HTML`, `ANSI`, `FormattedText` are supported,
    plus tuples compound by a string for style and a string for content

    .. code-block::

        tabulate.tabulate([[("fg:red", "aaaa"), "bb"], ["ccc", "dddd"]])

    .. code-block::

        tabulate.tabulate([[HTML("<red>aaaa</red>"), "bb"], ["ccc", "dddd"]])

    .. code-block::

        tabulate.tabulate([[("fg:red", 10), "bb"], ["ccc", "dddd"]])

    """
    assert tablefmt == "plain"
    table = Table()
    table.stralign = stralign
    table.numalign = numalign
    table.floatfmt = floatfmt
    return table.format(tabular_data)
