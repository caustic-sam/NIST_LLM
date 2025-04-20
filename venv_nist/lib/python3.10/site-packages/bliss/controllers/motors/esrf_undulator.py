# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Control the ESRF Undulators as bliss motors.
Example yml configuration:
.. code-block::
  controller:
    class: ESRF_Undulator
    ds_name: //acs:10000/id/master/idxx
    axes:
        -
            name: u35c
            undulator_prefix: U35c_GAP
            steps_per_unit: 1
            unit: mm
            tolerance: 0.01
"""

import time
import numpy
import gevent

from bliss.controllers.motor import Controller
from bliss.common.axis.state import AxisState
from bliss.common.axis.axis import NoSettingsAxis
from bliss.common.tango import DevState, DeviceProxy, AttributeProxy
from bliss.common.logtools import log_debug, log_warning
from bliss.common.utils import object_method
from bliss import global_map
from bliss.common.user_status_info import status_message


class UndulatorAxis(NoSettingsAxis):
    """
    NoSettingsAxis -> Settings of undulators axes are managed by tango device server.
    """

    def sync_hard(self):
        state = self.hw_state
        if "DISABLED" in state:
            self.settings.set("state", state)
            log_warning(self, "undulator %s is disabled, no position update", self.name)
        else:
            super().sync_hard()

    @property
    def movable_name(self):
        """Get the undulator movable attribute"""
        self.hw_state  # initialize axis, if not already done
        return self.controller.axis_info[self].get("movable_name")


# NoSettingsAxis does not use cache for settings
# -> force to re-read velocity/position at each usage.
Axis = UndulatorAxis


def get_all():
    """
    Return a list of all insertion device sevice server found in the
    global env.
    """
    try:
        return list(global_map.instance_iter("undulators"))
    except KeyError:
        # no undulator has been created yet there is nothing in map
        return []


class ESRF_Undulator(Controller):
    """Undulator motor controler"""

    def __init__(self, *args, **kwargs):
        Controller.__init__(self, *args, **kwargs)

        global_map.register(self, parents_list=["undulators"])

        self.axis_info = {}
        self.device = None

        self.ds_name = self.config.get("ds_name")
        if self.ds_name is None:
            raise RuntimeError(
                f"no 'ds_name' defined in config for {self.config.get('name')}"
            )

    def initialize(self):
        # velocity and acceleration are not mandatory in config
        self.axis_settings.config_setting["velocity"] = False
        self.axis_settings.config_setting["acceleration"] = False

        # Get a proxy on Insertion Device device server of the beamline.
        self.device = DeviceProxy(self.ds_name)
        global_map.register(
            self, parents_list=["undulators"], children_list=[self.device]
        )

    def initialize_axis(self, axis):
        """
        Read configuration to forge tango attributes names.
        """
        attr_pos_name = axis.config.get("attribute_position", str, "Position")
        log_debug(self, f"initialize_axis({axis.name})\nattr_pos_name={attr_pos_name}")

        attr_vel_name = axis.config.get("attribute_velocity", str, "Velocity")
        log_debug(self, f"attr_vel_name={attr_vel_name}")

        attr_fvel_name = axis.config.get(
            "attribute_first_velocity", str, "FirstVelocity"
        )
        log_debug(self, f"attr_fvel_name={attr_fvel_name}")

        attr_acc_name = axis.config.get("attribute_acceleration", str, "Acceleration")
        log_debug(self, f"attr_acc_name={attr_acc_name}")

        # Try to read undu_prefix or undulator_prefix in config
        undu_prefix = axis.config.get("undu_prefix", str) or axis.config.get(
            "undulator_prefix", str
        )
        msg = f"(1) 'undu(lator)_prefix' in config: {undu_prefix}"
        log_debug(self, msg)

        if undu_prefix is None:
            if attr_pos_name == "Position":
                raise RuntimeError("'undulator_prefix' must be specified in config")
            undu_prefix = attr_pos_name.replace("Position", "")
            log_debug(
                self, f"intialize_axis({axis.name})\nattr_pos_name={attr_pos_name}"
            )

        if undu_prefix.endswith("_"):
            undu_prefix = undu_prefix[:-1]

        movable_list = [movable.lower() for movable in self.device.movablenames]
        if undu_prefix.lower() not in movable_list:
            raise RuntimeError(
                f"ERROR: {undu_prefix} is not in movable list: {movable_list}"
            )

        # belt and braces
        if not attr_pos_name.startswith(undu_prefix):
            attr_pos_name = undu_prefix + "_" + attr_pos_name
        if not attr_vel_name.startswith(undu_prefix):
            attr_vel_name = undu_prefix + "_" + attr_vel_name
        if not attr_fvel_name.startswith(undu_prefix):
            attr_fvel_name = undu_prefix + "_" + attr_fvel_name
        if not attr_acc_name.startswith(undu_prefix):
            attr_acc_name = undu_prefix + "_" + attr_acc_name

        # check for revolver undulator
        is_revolver = False
        undulator_index = None

        # Extract undulator name
        # U32A_GAP_position -> u32a
        uname = attr_pos_name.split("_")[0].lower()
        log_debug(self, f"uname={uname}")

        # NB: "UndulatorNames" tango attribute returns list of names but not indexed properly :(
        uname_list = [item.lower() for item in self.device.UndulatorNames]
        log_debug(self, f"uname_list={uname_list}")
        undulator_index = uname_list.index(uname)
        # "UndulatorRevolverCarriage" returns an array of booleans.
        if self.device.UndulatorRevolverCarriage[undulator_index]:
            is_revolver = True

        self.axis_info[axis] = {
            "name": uname,
            "is_revolver": is_revolver,
            "undulator_index": undulator_index,
            "attr_pos_name": attr_pos_name,
            "attr_vel_name": attr_vel_name,
            "attr_fvel_name": attr_fvel_name,
            "attr_acc_name": attr_acc_name,
            "movable_name": undu_prefix,
        }

        log_debug(self, "OK: axis well initialized")

    def finalize(self):
        pass

    def _set_attribute(self, axis, attribute_name, value):
        if "DISABLED" in self.state(axis):
            if self.axis_info[axis]["is_revolver"]:
                raise RuntimeError("Revolver axis is disabled.")
            raise RuntimeError("Undulator is disabled.")
        self.device.write_attribute(self.axis_info[axis][attribute_name], value)

    def _get_attribute(self, axis, attribute_name):
        if "DISABLED" in self.state(axis):
            if self.axis_info[axis]["is_revolver"]:
                raise RuntimeError("Revolver axis is disabled.")
        return self.device.read_attribute(self.axis_info[axis][attribute_name]).value

    def start_one(self, motion):
        self._set_attribute(
            motion.axis,
            "attr_pos_name",
            float(motion.target_pos / motion.axis.steps_per_unit),
        )
        log_debug(self, f"end of start {motion.axis.name}")

    @object_method
    def enable(self, axis):
        """Enable the undulator axis when it is a disabled revolver axis."""
        axis_info = self.axis_info[axis]
        undulator_index = axis_info["undulator_index"]

        # check that the axe is a revolver axe
        if not axis_info["is_revolver"]:
            raise ValueError(f"{axis.name} is not a revolver axis")

        # check axis is disabled
        if "DISABLED" not in self.state(axis):
            raise ValueError(f"{axis.name} is already enabled")

        # send the Enable command
        uname = self.device.UndulatorNames[undulator_index]
        self.device.Enable(uname)

        ustate = DevState.DISABLE
        # wait for state to be neither disable nor moving
        while ustate in (DevState.DISABLE, DevState.MOVING):
            ustate = self.device.UndulatorStates[undulator_index]
            time.sleep(1)

        return axis.hw_state

    def read_position(self, axis):
        """
        Returns the position taken from controller
        in controller unit (steps).
        """
        if "DISABLED" in self.state(axis):
            if self.axis_info[axis]["is_revolver"]:
                return numpy.nan

        return self._get_attribute(axis, "attr_pos_name")

    # VELOCITY

    def read_velocity(self, axis):
        """
        Returns the current velocity taken from controller
        in motor units.
        """
        if "DISABLED" in self.state(axis):
            if self.axis_info[axis]["is_revolver"]:
                return numpy.nan
        return self._get_attribute(axis, "attr_vel_name")

    def set_velocity(self, axis, new_velocity):
        """
        <new_velocity> is in motor units
        """
        old_velocity = self._get_attribute(axis, "attr_vel_name")
        self._set_attribute(axis, "attr_vel_name", new_velocity)
        if old_velocity != new_velocity:
            # Velocity take time to be set (timeout set to 4s)!!!
            start_time = time.perf_counter()
            while (
                self._get_attribute(axis, "attr_vel_name") == old_velocity
                and time.perf_counter() - start_time < 4
            ):
                gevent.sleep(0.01)

    # ACCELERATION

    def read_acceleration(self, axis):
        if "DISABLED" in self.state(axis):
            if self.axis_info[axis]["is_revolver"]:
                return numpy.nan
        return self._get_attribute(axis, "attr_acc_name")

    def set_acceleration(self, axis, new_acceleration):
        old_acceleration = self._get_attribute(axis, "attr_acc_name")
        self._set_attribute(axis, "attr_acc_name", new_acceleration)
        if old_acceleration != new_acceleration:
            # Acceleration take time to be set (timeout set to 4s)!!!
            start_time = time.perf_counter()
            while (
                self._get_attribute(axis, "attr_acc_name") == old_acceleration
                and time.perf_counter() - start_time < 4
            ):
                gevent.sleep(0.01)

    # STATE

    def state(self, axis):
        if self.device.state() == DevState.DISABLE:
            return AxisState("DISABLED")

        undulator_index = self.axis_info[axis]["undulator_index"]
        ustate_list = self.device.UndulatorStates
        _state = ustate_list[undulator_index]

        if _state == DevState.ON:
            log_debug(self, f"{axis.name} READY")
            return AxisState("READY")
        if _state == DevState.MOVING:
            log_debug(self, f"{axis.name} MOVING")
            return AxisState("MOVING")
        if _state == DevState.DISABLE:
            log_debug(self, f"{axis.name} DISABLED")
            return AxisState("DISABLED")
        log_debug(self, f"{axis.name} READY after unknown state")
        return AxisState("READY")

    # POSITION

    def set_position(self, axis, new_position):
        """Implemented to avoid NotImplemented error in apply_config()."""
        return axis.position

    # Must send a command to the controller to abort the motion of given axis.

    def stop(self, axis):
        self.device.abort()

    def stop_all(self, *motion_list):
        self.device.abort()

    def __info__(self):
        info_str = f"\nUNDULATOR DEVICE SERVER: {self.ds_name} \n"
        info_str += f"     status = {str(self.device.status()).strip()}\n"
        info_str += f"     Power = {self.device.Power:.3g} kW"
        info_str += f"  (max: {self.device.MaxPower:.3g} kW)\n"
        info_str += f"     PowerDensity = {self.device.PowerDensity:.3g} kW/mr2"
        info_str += f"  (max: {self.device.MaxPowerDensity:.3g} kW/mr2)\n"
        return info_str

    def get_axis_info(self, axis):
        """Return axis info specific to an undulator"""
        info_str = "TANGO DEVICE SERVER VALUES:\n"

        state = self.state(axis)
        info_str += f"     state = {str(state)}\n"

        if "DISABLED" in state:
            position = "-"
            velocity = "-"
            first_vel = "-"
            acceleration = "-"
        else:
            position = getattr(self.device, self.axis_info[axis].get("attr_pos_name"))
            velocity = getattr(self.device, self.axis_info[axis].get("attr_vel_name"))
            first_vel = getattr(self.device, self.axis_info[axis].get("attr_fvel_name"))
            acceleration = getattr(
                self.device, self.axis_info[axis].get("attr_acc_name")
            )

        info_str += (
            f"     {self.axis_info[axis].get('attr_pos_name')} = {position} mm\n"
        )

        info_str += (
            f"     {self.axis_info[axis].get('attr_vel_name')} = {velocity} mm/s\n"
        )

        info_str += (
            f"     {self.axis_info[axis].get('attr_fvel_name')} = {first_vel} mm/s\n"
        )

        info_str += f"     {self.axis_info[axis].get('attr_acc_name')} = {acceleration} mm/s/s\n"

        return info_str

    def _test_suite(self, axis):
        """Set of tests to be used to validate the behaviour of an undulator.
        * at max speed
        *   very long moves ~100 mm
        *   moves (10mm)
        *   small moves (1mm)
        *   very small moves (10 um)

        Usage example: u55a.controller._test_suite(u55a)
        """
        axis.state  # Initialize axes if not already done.

        attr_pos_uri = self.ds_name + "/" + self.axis_info[axis]["attr_pos_name"]
        attr_pos = AttributeProxy(attr_pos_uri)
        # print(attr_pos.get_config())

        # min max values can be : 'Not specified'
        try:
            small_gap = float(attr_pos.get_config().min_value) + 2.0
        except ValueError:
            small_gap = 15
        try:
            large_gap = float(attr_pos.get_config().max_value / 2.0)
        except ValueError:
            large_gap = 50

        print("\nUndulator test suite \n")
        print("Will use following value:")
        print(f"  small gap = {small_gap}")
        print(f"  large gap = {large_gap}")
        print("\n")

        max_velocity = 5
        print(f"set velocity to {max_velocity}mm/s")
        axis.velocity = max_velocity

        print(f"move to small gap ({small_gap}mm)")
        axis.move(small_gap)

        # long move at full speed
        print(f"move to large gap ({large_gap})")
        axis.move(large_gap)

        test_movements = [
            {"desc": "10um", "dist_mm": 0.01, "nb_moves": 25},
            {"desc": "1mm", "dist_mm": 1, "nb_moves": 15},
            {"desc": "10mmm", "dist_mm": 10, "nb_moves": 5},
        ]

        print(" LARGE GAP--------------")
        for tmv in test_movements:
            print(f"     {tmv['nb_moves']} {tmv['desc']} movements")
            nb_moves = tmv["nb_moves"]
            dist_mm = tmv["dist_mm"]
            with status_message() as update:
                for itr in range(nb_moves):
                    update(f" {itr}/{nb_moves}")
                    axis.move(dist_mm, relative=True)
                    axis.move(-dist_mm, relative=True)

        print(f"move to small gap ({small_gap}mm)")
        axis.move(small_gap)
        print(" SMALL GAP--------------")

        for tmv in test_movements:
            print(f"     {tmv['nb_moves']} {tmv['desc']} movements")
            nb_moves = tmv["nb_moves"]
            dist_mm = tmv["dist_mm"]
            with status_message() as update:
                for itr in range(nb_moves):
                    update(f" {itr}/{nb_moves}")
                    axis.move(dist_mm, relative=True)
                    axis.move(-dist_mm, relative=True)

        print(f"moving to large gap ({large_gap})")
        print("now you can test double Ctrl-C")
        axis.move(large_gap)
