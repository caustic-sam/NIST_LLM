# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

# run tests for this module from the bliss root directory with:
# python -m unittest discover -s tests/acquisition -v

from collections import namedtuple
from collections.abc import Callable
from typing import Union
import functools
import enum
import inspect
import numpy
from beartype import beartype

from bliss.common.utils import autocomplete_property
from bliss.common.protocols import HasMetadataForDataset, HasMetadataForScan


@enum.unique
class SamplingMode(enum.IntEnum):
    """SamplingCounter modes:
    * MEAN: emit the mathematical average
    * STATS: in addition to MEAN, use iterative algorithms to emit std,min,max,N etc.
    * SAMPLES: in addition to MEAN, emit also individual samples as 1D array
    * SINGLE: emit the first value (if possible: call read only once)
    * LAST: emit the last value
    * INTEGRATE: emit MEAN multiplied by counting time
    """

    MEAN = enum.auto()
    STATS = enum.auto()
    SAMPLES = enum.auto()
    SINGLE = enum.auto()
    LAST = enum.auto()
    INTEGRATE = enum.auto()
    INTEGRATE_STATS = enum.auto()


def _Identity(val):
    return val


class Counter(HasMetadataForScan, HasMetadataForDataset):
    """Counter class"""

    def __init__(
        self,
        name,
        controller=None,
        conversion_function=None,
        unit=None,
        dtype=numpy.float64,
    ):
        self._name = name
        self.__counter_controller = None

        if controller is not None:
            self._set_controller(controller)

        self._dtype = dtype
        self._unit = unit

        self._conversion_function = conversion_function

    @property
    def name(self):
        return self._name

    @autocomplete_property
    def _counter_controller(self):
        return self.__counter_controller

    def _set_controller(self, controller):
        if self.__counter_controller:
            self.__counter_controller._global_map_unregister()
        self.__counter_controller = controller
        self.__counter_controller._counters[self.name] = self

    @property
    def dtype(self):
        """The counter data type as used by numpy."""
        return self._dtype

    @property
    def data_dtype(self):
        """The counter data type as used by numpy taking into account the conversion function"""
        if self._conversion_function is None:
            return self.dtype
        else:
            # conversion function always produces float64
            return numpy.float64

    @property
    def shape(self):
        """The data shape as used by numpy."""
        return ()

    @property
    def fullname(self):
        """A unique name within the session scope.

        The standard implementation defines it as:
        `[<master_controller_name>].[<controller_name>].<counter_name>`
        """
        args = []
        if self._counter_controller._master_controller is not None:
            args.append(self._counter_controller._master_controller.name)
        args.append(self._counter_controller.name)
        args.append(self.name)
        return ":".join(args)

    @property
    def unit(self):
        return self._unit

    @unit.setter
    def unit(self, unit):
        self._unit = unit

    @property
    def conversion_function(self):
        if self._conversion_function is None:
            return _Identity
        return self._conversion_function

    @conversion_function.setter
    @beartype
    def conversion_function(self, func: Union[Callable, None]):
        if func is None:
            self._conversion_function = None
        else:

            def convert(*args, **kwargs):
                x = func(*args, **kwargs)
                # assuming return from func 'x' is either a number
                # or a numpy array ; numpy doc says Python 'float' is 'numpy.float64'
                try:
                    return float(x)
                except TypeError:
                    return x.astype(numpy.float64)

            self._conversion_function = functools.update_wrapper(convert, func)

    def dataset_metadata(self) -> dict:
        return {"name": self.name}

    def scan_metadata(self) -> dict:
        return dict()

    def __info__(self):
        info_str = f"{self.__class__.__name__}:\n"
        info_str += f" name  = {self.name}\n"
        info_str += f" dtype = {numpy.dtype(self.data_dtype).name}\n"
        info_str += f" shape = {len(self.shape)}D\n"
        info_str += f" unit  = {self.unit}\n"
        if self.conversion_function is not _Identity:
            info_str += f" conversion_function = {self.conversion_function}\n"
        return info_str


SamplingCounterStatistics = namedtuple(
    "SamplingCounterStatistics", "mean N std var min max p2v count_time timestamp"
)


class SamplingCounter(Counter):
    def __init__(
        self,
        name,
        controller,
        conversion_function=None,
        mode=SamplingMode.MEAN,
        unit=None,
        dtype=None,
    ):
        super().__init__(
            name,
            controller,
            conversion_function=conversion_function,
            unit=unit,
            dtype=numpy.float64,
        )

        if isinstance(mode, SamplingMode):
            self._mode = mode
        else:
            # <mode> can also be a string
            self._mode = SamplingMode[mode]

        self._statistics = SamplingCounterStatistics(
            numpy.nan,
            numpy.nan,
            numpy.nan,
            numpy.nan,
            numpy.nan,
            numpy.nan,
            numpy.nan,
            None,
            None,
        )

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, value):
        try:
            if value in list(SamplingMode):
                self._mode = SamplingMode(value)
            else:
                self._mode = SamplingMode[value]
        except KeyError:
            raise ValueError(
                "Invalid mode '%s', the mode must be in %s"
                % (value, list(SamplingMode.__members__.keys()))
            )

    @autocomplete_property
    def statistics(self):
        return self._statistics

    def __info__(self):
        """Standard method called by BLISS Shell info helper."""
        info_str = super().__info__()
        info_str += f" mode  = {SamplingMode(self.mode).name} ({self.mode})\n"
        return info_str

    @property
    def raw_read(self):
        return self._counter_controller.read_all(self)[0]


class IntegratingCounter(Counter):
    pass


class SoftCounter(SamplingCounter):
    """
    Transforms any given python object into a sampling counter.
    By default it assumes the object has a member called *value* which will be
    used on a read.
    You can overwrite this behaviour by passing the name of the object member
    as value. It can be an object method, a property/descriptor or even a simple
    attribute of the given object.

    If no name is given, the counter name is the string representation of the
    value argument.
    The counter full name is `controller.name` + '.' + counter_name. If no
    controller is given, the obj.name is used instead of controller.name. If no
    obj is given the counter full name is counter name.

    You can pass an optional apply function if you need to transform original
    value given by the object into something else.

    Here are some examples::

        from bliss.common.counter import SoftCounter

        class Potentiostat:

            def __init__(self, name):
                self.name = name

            @property
            def potential(self):
                return float(self.comm.write_readline('POT?\n'))

            def get_voltage(self):
                return float(self.comm.write_readline('VOL?\n'))

        pot = Potentiostat('p1')

        # counter from an object property (its name is 'potential'.
        # Its full name is 'p1.potential')
        pot_counter = SoftCounter(pot, 'potential')

        # counter form an object method
        milivol_counter = SoftCounter(pot, 'get_voltage', name='voltage',
                                      apply=lambda v: v*1000)

        # you can use the counters in any scan
        from bliss.common.standard import loopscan
        loopscan(10, 0.1, pot_counter, milivol_counter)
    """

    def __init__(
        self,
        obj=None,
        value="value",
        name=None,
        apply=None,
        mode=SamplingMode.MEAN,
        unit=None,
        dtype=None,
        conversion_function=None,
    ):
        if obj is None and inspect.ismethod(value):
            obj = value.__self__
        self.get_value, value_name = self.get_read_func(obj, value)
        name = value_name if name is None else name
        obj_has_name = hasattr(obj, "name") and isinstance(obj.name, str)
        if obj_has_name:
            self.ctrl_name = obj.name
        elif obj is None:
            self.ctrl_name = name
        else:
            self.ctrl_name = type(obj).__name__
        if apply is None:
            self.apply = lambda x: x
        else:
            self.apply = apply

        from bliss.controllers.counter import SoftCounterController

        super().__init__(
            name,
            SoftCounterController(self.ctrl_name),
            mode=mode,
            unit=unit,
            dtype=dtype,
            conversion_function=conversion_function,
        )

    @staticmethod
    def get_read_func(obj, value):
        if callable(value):
            value_name = value.__name__
            value_func = value
        else:
            otype = type(obj)
            value_name = value
            val = getattr(otype, value_name, None)
            if val is None or not callable(val):

                def value_func():
                    return getattr(obj, value_name)

            else:

                def value_func():
                    return val(obj)

            value_func.__name__ = value_name
        return value_func, value_name

    # as there is a 1-1 correspondance between counter controller and
    # sampling counter in the case of SoftCounter, the 'max_sampling_frequency'
    # property, which has to be set on the controller, is exposed on the
    # SoftCounter object
    @property
    def max_sampling_frequency(self):
        return self._counter_controller.max_sampling_frequency

    @max_sampling_frequency.setter
    def max_sampling_frequency(self, freq):
        self._counter_controller.max_sampling_frequency = freq


class CalcCounter(Counter):
    def __init__(
        self,
        name,
        controller=None,
        dim=None,
        conversion_function=None,
        unit=None,
        dtype=None,
        shape=None,
    ):
        super().__init__(
            name,
            controller,
            conversion_function=conversion_function,
            unit=unit,
            dtype=dtype,
        )

        if shape is None:
            self._shape = (-1,) * dim if dim is not None else ()
        else:
            if dim is not None and len(shape) != dim:
                raise ValueError(
                    f"dim and shape are provided but mismatching: len({shape}) != {dim} ('dim' can be omitted if using 'shape')"
                )
            if not isinstance(shape, tuple):
                raise ValueError(f"shape must be a tuple, but receive: {shape}")
            self._shape = shape

    @property
    def shape(self):
        """The data shape as used by numpy."""
        return self._shape
