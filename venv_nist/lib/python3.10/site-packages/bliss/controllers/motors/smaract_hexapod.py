# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""SmarAct piezo hexapod motor controller

YAML_ configuration example:

.. code-block:: yaml

    plugin: emotion
    class: SmarAct
    tcp:
      url: id99smaract1            # (1)
    pivot_point: [0, 0, 0]
    pivot_relative: False
    axes:
      - name: X
        unit: um                   # (2)
        channel: 0                 # (3)
        steps_per_unit: 1
      - name: Y
        unit: um
        channel: 1
        steps_per_unit: 1
      - name: Z
        unit: um
        channel: 2
        steps_per_unit: 1
      - name: rotX
        unit: deg
        channel: 3
        steps_per_unit: 1
      - name: rotY
        unit: deg
        channel: 4
        steps_per_unit: 1
      - name: rotZ
        unit: deg
        channel: 5
        steps_per_unit: 1


1. controller hostname, default port number is 2000
2. unit:
   For translations, units must be um
   For rotations, units must be degrees
3. channel goes from 0 to 5, 0..2 are translations and 3..5 are rotations
"""

from bliss.common.axis.state import AxisState
from bliss.comm.util import get_comm
from bliss.controllers.motor import Controller
from bliss import global_map
from bliss.config.beacon_object import BeaconObject


class SmaractHexapodSettings(BeaconObject):
    def __init__(self, hexapod, config):
        # build an unique name for BeaconObject, to be able to store in redis
        name = f"{config['class']}:{','.join(axis['name'] for axis in config['axes'])}"

        super().__init__(config, name)

        self._hexapod = hexapod

    pivot_point = BeaconObject.property_setting("pivot_point")

    @pivot_point.setter
    def pivot_point(self, xyz_in_mm_values_list):
        self._hexapod._set_pivot_point(xyz_in_mm_values_list)

    pivot_relative = BeaconObject.property_setting("pivot_relative")

    @pivot_relative.setter
    def pivot_relative(self, relative: bool):
        self._hexapod._set_pivot_relative(relative)


class SmaractHexapod(Controller):
    def __init__(self, config):
        self._beacon_settings = SmaractHexapodSettings(self, config)
        Controller.__init__(self, config)

    def initialize(self):
        self.axis_settings.config_setting["velocity"] = False
        self.axis_settings.config_setting["acceleration"] = False

        config_dict = self.config.config_dict
        self._comm = get_comm(config_dict)

        global_map.register(self, children_list=[self._comm])

        self._beacon_settings.initialize()

    def initialize_axis(self, axis):
        ch = int(axis.config.get("channel"))
        if ch < 0 or ch > 6:
            raise ValueError(
                f"Invalid channel for axis {axis.name}, must be between 0 and 5"
            )

    def apply_config(self, reload=False):
        self._beacon_settings.apply_config(reload)

    def _raw_read_positions(self):
        try:
            ans = self._putget("pos?")
        except RuntimeError as exc:
            raise RuntimeError("Could not read positions") from exc
        else:
            return list(map(float, ans.split()))

    def read_position(self, axis):
        ch = int(axis.config.get("channel"))
        factor = (
            1e6 if ch < 3 else 1
        )  # default unit for translation axes is meter, convert to um
        try:
            all_pos = self._raw_read_positions()
        except RuntimeError as exc:
            raise RuntimeError("Could not read position for axis {axis.name}") from exc
        else:
            return all_pos[ch] * factor

    def read_acceleration(self, axis):
        try:
            ans = self._putget("acc?")
        except RuntimeError as exc:
            raise RuntimeError(
                f"Could not read acceleration for axis {axis.name}"
            ) from exc
        return float(ans) * 1e6

    def read_velocity(self, axis):
        try:
            ans = self._putget("vel?")
        except RuntimeError as exc:
            raise RuntimeError(f"Could not read velocity for axis {axis.name}") from exc
        return float(ans) * 1e6

    def state(self, axis):
        # move status from controller:
        # 0 == stopped
        # 1 == holding position actively, but not moving
        # 2 == moving
        try:
            ans = self._putget("mst?")
        except RuntimeError as exc:
            raise RuntimeError(f"Could not read state for axis {axis.name}") from exc
        else:
            if int(ans) == 2:
                # at least a leg is moving
                return AxisState("MOVING")
            return AxisState("READY")

    def start_all(self, *motion_list):
        cmd = "mov"
        targets = {ch: pos for ch, pos in enumerate(self._raw_read_positions())}
        targets.update(
            {
                int(axis.config.get("channel")): axis._set_position
                for axis in self.axes.values()
            }
        )
        for motion in motion_list:
            ch = int(motion.axis.config.get("channel"))
            targets[ch] = motion.target_pos
        for ch, target in sorted(targets.items()):
            if ch < 3:
                # NB: conversion to um
                target /= 1e6
            cmd += f" {target}"
        try:
            _ = self._putget(cmd)
        except RuntimeError as exc:
            raise RuntimeError("Could not start motion") from exc

    def stop_all(self, *motion_list):
        self._putget("stop")

    @property
    def pivot_point(self):
        return self._beacon_settings.pivot_point

    @pivot_point.setter
    def pivot_point(self, xyz_in_mm_values_list):
        self._beacon_settings.pivot_point = xyz_in_mm_values_list

    def _set_pivot_point(self, xyz_in_mm_values_list):
        # take input values, convert to meters
        x, y, z = map(lambda x: float(x) / 1e6, xyz_in_mm_values_list)
        try:
            self._putget(f"piv {x}m {y}m {z}m")
        except RuntimeError as exc:
            raise RuntimeError("Could not set new pivot point") from exc

    @property
    def pivot_relative(self):
        return self._beacon_settings.pivot_relative

    @pivot_relative.setter
    def pivot_relative(self, relative: bool):
        self._beacon_settings.pivot_relative = relative

    def _set_pivot_relative(self, relative: bool):
        try:
            self._putget(f"pvm {0 if relative else 1}")
        except RuntimeError as exc:
            raise RuntimeError("Could not set pivot mode") from exc

    def reset(self):
        """Perform a full reset of the device

        //!\\ This will move the legs to their limit switches
        """
        self._putget("ref", timeout=10)  # blocking call for the device
        ans = self._putget("ref?")
        if int(ans) != 1:
            raise RuntimeError("Failed to reset hexapod")
        for axis in self.axes.values():
            axis.sync_hard()

    def get_axis_info(self, axis):
        return ""

    def __info__(self):
        dev_info = self._putget("%info device", lines=3)
        dev_info_str = ",".join(
            dict(x.split(":") for x in dev_info.split("\n")).values()
        )
        status_info = self._putget("%info status", lines=9)
        status_info_str = "\n".join(" " * 5 + x for x in status_info.split("\n"))
        pivot_info = self._putget("piv?")
        # NB: pivot position unit conversion directly in f-string below:
        pivot_info_str = ", ".join(
            f"{axis_name}: {float(value)*1e6} um"
            for axis_name, value in zip(("X", "Y", "Z"), pivot_info.split())
        )
        pivot_mode_str = "fixed" if int(self._putget("pvm?")) else "relative"
        ref_pos_str = "yes" if int(self._putget("ref?")) == 1 else "no"
        return "\n".join(
            [
                "",
                f"CONTROLLER id: {dev_info_str}",
                "",
                status_info_str,
                "",
                f"     Reference position ? {ref_pos_str}",
                "     Pivot point:",
                f"          {pivot_info_str}",
                f"     Pivot mode: {pivot_mode_str}",
            ]
        )

    def _putget(self, cmd, lines=1, timeout=1):
        if not cmd.endswith("\n"):
            cmd += "\n"
        ans = self._comm.write_readlines(
            bytes(cmd, "utf-8"), lines, eol=b"\r\n", timeout=timeout
        )
        if len(ans) == 1 and ans[0].startswith(b"!"):
            err = int(ans[0][1:].decode())
            if not err:
                return True
            err_msg = self._putget(f"%code? {err}")
            raise RuntimeError(err_msg)
        return b"\n".join(ans).decode()
