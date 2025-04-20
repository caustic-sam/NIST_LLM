# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations


def layout_packed_grid(
    nb_elements: int, max_columns: int, max_rows: int
) -> tuple[int, int]:
    """Returns final grids from constraints.

    It tries to pack together cells for readability instead of following primary then secondary axes only.
    """
    rows = nb_elements // max_columns + int(nb_elements % max_columns > 0)
    columns = min(nb_elements // rows + int(nb_elements % rows > 0), max_columns)
    rows = min(max_rows, rows)
    columns = min(nb_elements // rows + int(nb_elements % rows > 0), columns)
    return columns, rows


def layout_box(available_size: int, cell_size: int, spacing: int) -> int:
    """Returns the amount of cells that can be layouted in an available 1D space"""
    # solve: ncells * cell_width + (ncells - 1) * spacing = available_width
    ncells = (available_size + spacing) // (cell_size + spacing)
    return ncells


def layout_grid_flow_min_size(nb_elements: int, fixed_axis_size: int):
    """Returns the min size of the secondary axis to arange elements in
    a 2D space with the other axis size already fixed.
    """
    return nb_elements // fixed_axis_size + int(nb_elements % fixed_axis_size > 0)
