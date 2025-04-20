# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) 2015-2022 Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations
import typing
from bliss.controllers.motor import Controller
from bliss.common.axis import AxisState
from bliss.common.axis import Axis as BaseAxis
from bliss.common.tango import DevState, DeviceProxy
from bliss.common.logtools import log_debug, log_warning, log_error
from bliss import global_map
from bliss.common.tango_callbacks import TangoCallbacks
from bliss.common import event

"""
Bliss controller tango attribute as motor.

V. Rey ESRF BCU June 2022

A motor moves by modifying an attribute in a Tango server.

An (optional) state attribute can be used to report the state.

An (optional) stop cmd can also be used to stop the motor if
necessary.

If not specified, default attributes are used:

default attributes are: 

- pos_attr: Position
- state_attr: State

If `velocity_attr` and `acceleration_attr` are not declared, a default
hardcoded values will be reported.  no access to those
attributes would be done.

To setup `Velocity` and/or `Acceleration` use declarations as follow:

- velocity_attr: Velocity
- acceleration_attr: Acceleration

Default stop command is:

- stop_cmd: Abort

Example configuration:

.. code-block:: yaml

    - class: tango_attr_as_motor
      axes:

       # first example 'chi' uses defaults for all attributes:
       - name: chi
         uri: id39/slitbox/1

       # in this example 'phi' uses "diffpos" as attribute to change
       # when moving
       - name: phi
         uri: id39/slitbox/1
         pos_attr: diffpos

       # in the last example 'omega' both attributes and stop command 
       # are different from defaults
       - name: omega
         uri: id39/slitbox/3
         pos_attr: OmegaPosition
         state_attr: OmegaState
         stop_cmd: OmegaStop

       # A tango icepap exposes an acceleration time instead of an acceleration.
       # This can be setup the following way:
       - name: icepap_roby
         uri: id00/icepap/roby
         pos_attr: Position
         velocity_attr: Velocity
         acceleration_time_attr: Acceleration


Beware that, even if not configured, if the `state` attribute and/or the `stop`
command exists for that device, they may be used by this controller. That
is the default behaviour. You can still inhibit their use by manually positioning
them to a non-existant attribute name/command name.

For example `state_attr: dummy` or `stop_cmd: not-used`.
"""

ERROR_POS = -9999


class TangoAttrMotorAxis(BaseAxis):
    """
    Settings of tango attr motor axes are managed by the device server
    """

    def __init__(
        self,
        name: str,
        controller: TangoAttrMotorController,
        config: dict[str, typing.Any],
    ):
        super().__init__(name, controller, config)
        self._proxy: DeviceProxy | None = None
        self._callbacks: TangoCallbacks | None = None

        self._pos_attr = config.get("pos_attr", "position")
        self._state_attr = config.get("state_attr", "state")
        self._velocity_attr = config.get("velocity_attr")
        self._accel_attr = config.get("acceleration_attr", None)
        self._accel_time_attr = config.get("acceleration_time_attr", None)
        self._stop_cmd = config.get("stop_cmd", "Abort")
        event.connect(self, "move_done", self._move_done)
        self._is_move_done = True

    def _move_done(self, value: bool):
        """Event when a BLISS session do the move on the axis"""
        self._is_move_done = value

    def sync_hard(self):
        state = self.hw_state
        if "DISABLED" in state:
            self.settings.set("state", state)
            log_warning(self, "Motor is disabled, no position update")
        else:
            super().sync_hard()

    def _tango_to_bliss_state(self, tango_state: DevState) -> AxisState:
        if tango_state == DevState.ON:
            return AxisState("READY")
        elif tango_state == DevState.OFF:
            return AxisState("OFF")
            # return AxisState("READY")
        elif tango_state == DevState.MOVING:
            return AxisState("MOVING")
        elif tango_state == DevState.FAULT:
            return AxisState("FAULT")
        elif tango_state == DevState.ALARM:
            # Ignore ALARM
            return AxisState("READY")
        else:
            log_warning(self, "Unknown Tango state %s", tango_state)
            return AxisState("READY")

    def _tango_state_changed(self, attr_name, new_value):
        if self.is_moving or not self._is_move_done:
            # Assume the motion loop is already doing the update
            return
        state = self._tango_to_bliss_state(new_value)
        self.settings.set("state", state)

    def _tango_position_changed(self, attr_name, new_value):
        if self.is_moving or not self._is_move_done:
            # Assume the motion loop is already doing the update
            return

        update_list = (
            "dial_position",
            new_value,
            "position",
            self.dial2user(new_value),
        )
        self.settings.set(*update_list)

    def _tango_velocity_changed(self, attr_name, new_value):
        self.settings.set("velocity", new_value)

    def _tango_acceleration_changed(self, attr_name, new_value):
        self.settings.set("acceleration", new_value)

    def _tango_acceleration_time_changed(self, attr_name, new_value):
        v = self.settings.get("velocity")
        self.settings.set("acceleration", new_value / v)

    def __close__(self):
        event.disconnect(self, "move_done", self._move_done)
        if self._callbacks is not None:
            self._callbacks.stop()
            self._callbacks = None


# TangoAttrMotorAxis does not use cache for settings
# -> force to re-read velocity/position at each usage.
Axis = TangoAttrMotorAxis


class TangoAttrMotorController(Controller):
    default_velocity = 2000
    default_acceleration = 500
    default_steps_per_unit = 1

    def initialize(self):
        self.axis_settings.hardware_setting["_set_position"] = True
        global_map.register(self)
        log_debug(self, "tango attr motor controller created")

    def finalize(self):
        pass

    def initialize_hardware_axis(self, axis: TangoAttrMotorAxis):
        log_debug(self, "initialize_hardware_axis")
        axis.velocity = self.read_velocity(axis)
        axis.acceleration = self.read_acceleration(axis)

    def initialize_axis(self, axis: TangoAttrMotorAxis):
        log_debug(self, "initializing axis")

        axis.config.set("velocity", self.default_velocity)
        axis.config.set("acceleration", self.default_acceleration)

        # if velocity_attr and/or acceleration_attr are given. they
        # will be first read here
        self.proxy_check(axis)
        axis.config.set("steps_per_unit", self.default_steps_per_unit)

    def proxy_check(self, axis: TangoAttrMotorAxis):
        if axis._proxy is not None:
            return

        axis_uri = axis.config.get("uri", None)
        if axis_uri is None:
            log_error(
                self,
                "no device name defined in config for tango attr motor %s" % axis.name,
            )
            return

        try:
            proxy = DeviceProxy(axis_uri)
            axis._proxy = proxy
        except Exception:
            axis._proxy = None
            return

        global_map.register(self, children_list=[proxy])

        # get limits from tango if not set in config
        cfg_minval = axis.config.get("low_limit")
        cfg_maxval = axis.config.get("high_limit")

        if None in (cfg_minval, cfg_maxval):
            attr_config = proxy.get_attribute_config(axis._pos_attr)
            minval, maxval = attr_config.min_value, attr_config.max_value
            try:
                if cfg_minval is None:
                    minval = float(minval)
                else:
                    minval = cfg_minval

                if cfg_maxval is None:
                    maxval = float(maxval)
                else:
                    maxval = cfg_maxval

                if cfg_minval is None:
                    axis.config.set("low_limit", minval)
                if cfg_maxval is None:
                    axis.config.set("high_limit", maxval)
            except Exception:
                log_warning(
                    self, "Cannot get limits for tango_attr motor %s", axis.name
                )

        state_attr = axis._state_attr
        position_attr = axis._pos_attr
        velocity_attr = axis._velocity_attr
        acceleration_attr = axis._accel_attr
        acceleration_time_attr = axis._accel_time_attr

        if velocity_attr is not None:
            velocity = proxy.read_attribute(velocity_attr).value
            axis.config.set("velocity", velocity)

        if acceleration_attr is not None or acceleration_time_attr is not None:
            acceleration = self.read_acceleration(axis)
            axis.config.set("acceleration", acceleration)

        callbacks = TangoCallbacks(proxy)
        axis._callbacks = callbacks
        if position_attr is not None:
            callbacks.add_callback(position_attr, axis._tango_position_changed)
        if state_attr is not None:
            callbacks.add_callback(state_attr, axis._tango_state_changed)
        if velocity_attr is not None:
            callbacks.add_callback(velocity_attr, axis._tango_velocity_changed)
        if acceleration_attr is not None:
            callbacks.add_callback(acceleration_attr, axis._tango_acceleration_changed)
        if acceleration_time_attr is not None:
            callbacks.add_callback(
                acceleration_time_attr, axis._tango_acceleration_time_changed
            )

    def _get_proxy(self, axis: TangoAttrMotorAxis) -> DeviceProxy:
        """Initialize the axis and return the tango proxy.

        Raises:
            RuntimeError: If the tango controller can't be initialized
        """
        self.proxy_check(axis)
        if axis._proxy is None:
            raise RuntimeError(f"Tango proxy for '{axis.name}' is not available")
        return axis._proxy

    def read_position(self, axis: TangoAttrMotorAxis):
        """
        Returns the attribute value if it exists  / ERROR_POS otherwise
        """
        self.proxy_check(axis)
        proxy = axis._proxy
        if proxy:
            pos_attr = axis._pos_attr
            pos = proxy.read_attribute(pos_attr).value
            return pos

        return ERROR_POS

    def state(self, axis: TangoAttrMotorAxis):
        self.proxy_check(axis)
        proxy = axis._proxy
        if not proxy:
            return AxisState("FAULT")

        state_attr = axis._state_attr
        if not hasattr(proxy, state_attr):
            return AxisState("READY")

        state = proxy.read_attribute(state_attr).value
        return axis._tango_to_bliss_state(state)

    def prepare_move(self, motion):
        pass

    def start_one(self, motion):
        """
        Called on a single axis motion,
        returns immediately,
        positions in motor units
        """
        axis = motion.axis
        target_pos = motion.target_pos

        self.proxy_check(axis)
        proxy = axis._proxy

        pos_attr = axis._pos_attr
        if proxy:
            return proxy.write_attribute(pos_attr, target_pos)
        else:
            return -1

    def stop(self, axis: TangoAttrMotorAxis):
        self.proxy_check(axis)
        proxy = axis._proxy
        if proxy:
            stop_cmd = axis._stop_cmd
            if hasattr(proxy, stop_cmd):
                proxy.command_inout(stop_cmd)

    def read_velocity(self, axis: TangoAttrMotorAxis):
        """
        Returns the attribute value if it exists  / ERROR_VEL otherwise
        """
        proxy = self._get_proxy(axis)
        velocity_attr = axis._velocity_attr
        if velocity_attr is not None:
            velocity = proxy.read_attribute(velocity_attr).value
            return float(velocity)
        else:
            return float(axis.config.get("velocity", self.default_velocity))

    def set_velocity(self, axis: TangoAttrMotorAxis, velocity):
        proxy = self._get_proxy(axis)
        velocity_attr = axis._velocity_attr
        if velocity_attr is not None:
            proxy.write_attribute(velocity_attr, velocity)
            return
        # if not managed do nothing

    def read_acceleration(self, axis: TangoAttrMotorAxis):
        proxy = self._get_proxy(axis)
        accel_attr = axis._accel_attr
        if accel_attr:
            acc = proxy.read_attribute(accel_attr).value
            return float(acc)

        accel_time_attr = axis._accel_time_attr
        if accel_time_attr:
            acc_time = proxy.read_attribute(accel_time_attr).value
            vel = self.read_velocity(axis)
            acc = vel / acc_time
            return float(acc)

        return float(axis.config.get("acceleration", self.default_acceleration))

    def set_acceleration(self, axis: TangoAttrMotorAxis, acc):
        proxy = self._get_proxy(axis)
        accel_attr = axis._accel_attr
        if accel_attr:
            proxy.write_attribute(accel_attr, acc)
            return

        accel_time_attr = axis._accel_time_attr
        if accel_time_attr is not None:
            acc_time = self.read_velocity(axis) / acc
            proxy.write_attribute(accel_time_attr, acc_time)

    def set_off(self, axis: TangoAttrMotorAxis):
        proxy = self._get_proxy(axis)
        proxy.off()

    def set_on(self, axis: TangoAttrMotorAxis):
        proxy = self._get_proxy(axis)
        proxy.on()

    def initialize_encoder(self, encoder):
        pass

    def read_encoder(self, encoder):
        return encoder.axis.dial * encoder.steps_per_unit

    def __close__(self):
        for a in self.axes:
            a._callbacks.stop()


tango_attr_as_motor = TangoAttrMotorController
