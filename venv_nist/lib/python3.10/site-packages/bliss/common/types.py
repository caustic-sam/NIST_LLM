# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import numbers
from typing import Union
from collections.abc import Mapping, Sequence
import numpy
from bliss.common.counter import Counter
from bliss.common.protocols import Scannable, CounterContainer, IterableNamespace
from bliss.common.measurementgroup import MeasurementGroup
from bliss.common.motor_group import _Group as MotorGroup


_int = numbers.Integral
_float = numbers.Real
_countable = Counter
_countables = Union[
    IterableNamespace, Counter, MeasurementGroup, CounterContainer, tuple
]


# pylint: disable=invalid-name
_float_or_countables = Union[
    numbers.Real, IterableNamespace, Counter, MeasurementGroup, CounterContainer, tuple
]
_scannable_or_name = Union[Scannable, str]
_scannable_start_stop_list = Sequence[tuple[Scannable, _float, _float]]
_scannable_start_stop_intervals_list = Sequence[tuple[Scannable, _float, _float, _int]]
_position_list = Union[Sequence, numpy.ndarray]
_scannable_position_list_list = Sequence[tuple[Scannable, _position_list]]
_scannable_position_list = Sequence[tuple[Scannable, _float]]
_scannable_position_list_or_group_position_list = Union[
    _scannable_position_list,
    tuple[MotorGroup, _scannable_position_list],
    Mapping[Scannable, _position_list],
]

_providing_channel = Union[None, Scannable, Counter, str]
"""Used by plotselect"""
