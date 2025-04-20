# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.controllers.motor import Controller
from bliss.common.axis.state import AxisState
from bliss.common.tango import DeviceProxy, DevFailed
import codecs
import functools
import pickle


def reraise_not_impl_error(func):
    @functools.wraps(func)
    def func_wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except DevFailed as e:
            if "NotImplementedError" in str(e):
                raise NotImplementedError from None
            else:
                raise

    return func_wrapper


def lazy_init(func):
    @functools.wraps(func)
    def func_wrapper(self, *args, **kwargs):
        if self._proxy is None:
            self._initialize()
        return func(self, *args, **kwargs)

    return func_wrapper


class TangoMotorController(Controller):
    """
    Controller for remote controller
    bliss.tango.servers.motor_controller_ds.MotorControllerDevice
    """

    def __init__(self, config, *args, **kwargs):
        tango_server_config = config.get("tango-server")
        self._tango_name = tango_server_config.get("tango_name")
        if self._tango_name is None:
            raise RuntimeError('Missing key "tango_name"')
        self._proxy = None
        super().__init__(config, *args, **kwargs)

    def _initialize(self):
        self._proxy = DeviceProxy(self._tango_name)
        try:
            self._proxy.ping()
        except DevFailed:
            self._proxy = None
            raise
        else:
            pickled_settings = self._proxy.controller_axis_settings
            self._axis_settings = pickle.loads(
                codecs.decode(pickled_settings.encode(), "base64")
            )

    @lazy_init
    def initialize_axis(self, axis):
        self._proxy.initialize_axis(axis.name)

    @lazy_init
    def get_axis_info(self, axis):
        return self._proxy.get_axis_info(axis.name)

    @lazy_init
    @reraise_not_impl_error
    def read_position(self, axis):
        return self._proxy.read_position(axis.name)

    @lazy_init
    @reraise_not_impl_error
    def set_position(self, axis, new_position):
        self._proxy.set_position(f"{axis.name} {new_position}")
        return self.read_position(axis)

    @lazy_init
    @reraise_not_impl_error
    def read_acceleration(self, axis):
        return self._proxy.read_acceleration(axis.name)

    @lazy_init
    @reraise_not_impl_error
    def set_acceleration(self, axis, new_acceleration):
        self._proxy.set_acceleration(f"{axis.name} {new_acceleration}")
        return self.read_acceleration(axis)

    @lazy_init
    @reraise_not_impl_error
    def read_velocity(self, axis):
        return self._proxy.read_velocity(axis.name)

    @lazy_init
    @reraise_not_impl_error
    def set_velocity(self, axis, new_velocity):
        self._proxy.set_velocity(f"{axis.name} {new_velocity}")
        return self.read_velocity(axis)

    @lazy_init
    def state(self, axis):
        state_name = self._proxy.axis_state(axis.name)
        return AxisState(state_name)

    @lazy_init
    def start_one(self, motion):
        axis = motion._Motion__axis
        motion._Motion__axis = motion.axis.name
        self._proxy.start_one(codecs.encode(pickle.dumps(motion), "base64").decode())
        motion._Motion__axis = axis

    @lazy_init
    def stop(self, axis):
        self._proxy.stop(axis.name)

    @lazy_init
    @reraise_not_impl_error
    def raw_write(self, com):
        self._proxy.raw_write(com)

    @lazy_init
    @reraise_not_impl_error
    def raw_write_read(self, com):
        return self._proxy.raw_write_read(com)
