# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Handle multiple, predefined motor positions equpment.

Example YAML_ configuration:

1. Attenuator with 3 predefined positions, moving 1 motor (dummy1)

.. code-block::

    class: MultiplePositions
    name: att1
    positions:
    - label: Al3
      description: Aluminum 3 mm
      target:
      - axis: $dummy1
        destination: 2.5
        tolerance: 0.01
    - label: Cu2
      description: Copper 2 mm
      target:
      - axis: $dummy1
        destination: 2.0
        tolerance: 0.2
    - label: Al4
      description: Aluminum 4 mm
      target:
      - axis: $dummy1
        destination: 3.5
        tolerance: 0.01

2. Beamstop with 3 predefined positions, moving 2 motors at the same time

.. code-block::

    class: MultiplePositions
    name: beamstop
    simultaneous_move: True
    positions:
    - label: IN
      description: Beamstop position IN the beam
      target:
      - axis: $dummy1
        destination: 2.5
        tolerance: 0.01
      - axis: $dummy2
        destination: 1.0
        tolerance: 0.2
    - label: OUT
      description: Beamstop position OUT of the beam
      target:
      - axis: $dummy1
        destination: 3.5
        tolerance: 0.01
      - axis: $dummy2
        destination: 2.0
        tolerance: 0.2
    - label: PARK
      description: Beamstop in safe position
      target:
      - axis: $dummy1
        destination: 1.5
        tolerance: 0.01
      - axis: $dummy2
        destination: 0.0
        tolerance: 0.2
"""

from __future__ import annotations
from typing import Any

import functools
import gevent
from tabulate import tabulate
from bliss.common.protocols import HasMetadataForScan, HasMetadataForDataset
from bliss.common.axis.axis import Axis
from bliss.common.axis.state import AxisState
from bliss.config.channels import Channel
from bliss.common import event
from bliss.common.logtools import log_warning, log_error
from bliss import global_map, is_bliss_shell
from bliss.common.utils import flatten


class MultiplePositions(HasMetadataForDataset, HasMetadataForScan):
    """Handle multiple positions."""

    def __init__(self, name, config):
        self.simultaneous = True
        self.targets_dict = {}  # dict of all the targets (to be used by GUI)
        self.positions_list: list[dict] = []
        self._config = config
        self.name = name
        self._last_label = None
        self._last_states: dict[Axis, AxisState] = {}
        self._current_label = None
        self._position_channel = Channel(
            f"{name}:position",
            default_value="unknown",
            callback=self.__position_changed,
        )
        self._g_move: gevent.Greenlet = None
        self._state_channel = Channel(
            f"{name}:state", default_value="READY", callback=self.__state_changed
        )
        self._read_config()
        self._check_config()

        # Add label-named method for all positions.
        for position in self.positions_list:
            self.add_label_move_method(position["label"])

        for axis in self.motor_objs:
            self._last_states[axis] = axis.state
            event.connect(axis, "state", self.__positioner_state_changed)
            event.connect(axis, "position", self.__positioner_position_changed)

        global_map.register(self, tag=name)

    def dataset_metadata(self) -> dict[str, Any] | None:
        mdata = self._get_position_config().get("dataset_metadata", None)
        if mdata is None:
            return None
        mdata = dict(mdata)

        # Deprecated keys
        positioners_name = mdata.pop("Positioners_name", None)
        positioners_value = mdata.pop("Positioners_value", None)
        if positioners_name is not None or positioners_value is not None:
            log_warning(
                self,
                "'Positioners_name' and 'Positioners_value' are deprecated. Use `positioners: $motobj` instead.",
            )
            if "positioners" not in mdata:
                mdata["positioners"] = {}
                if positioners_name is not None:
                    mdata["positioners"]["name"] = positioners_name
                if positioners_value is not None:
                    mdata["positioners"]["value"] = positioners_value

        return mdata

    def scan_metadata(self) -> dict[str, Any] | None:
        cfg = self._get_position_config()
        mdata = cfg.get("metadata", None)
        if mdata:
            log_warning(
                self,
                "The MultiplePositions configuration tag 'metadata' is deprecated and needs to be split in 'scan_metadata' and 'dataset_metadata'.",
            )
            return dict(mdata)
        mdata = cfg.get("scan_metadata", None)
        if mdata:
            return dict(mdata)
        return None

    def __positioner_state_changed(self, value, signal, sender):
        """Triggered when one of the positioners state change"""
        # It would be better to update a state machine for safety
        self._last_states[sender] = value
        _st = self.state
        if self._state_channel.value != _st:
            self._state_channel.value = _st

    def __positioner_position_changed(self, value, signal, sender):
        """Triggered when one of the positioners position change"""
        # It would be better to update a state machine
        pos = self.position
        if self._position_channel.value != pos:
            self._position_channel.value = pos

    def _get_position_config(self) -> dict[str, Any]:
        position = self.position
        for pos in self._config["positions"]:
            if pos["label"] == position:
                return pos
        return {}

    def add_label_move_method(self, pos_label):
        """Add a method named after the position label to move to the
        corresponding position.
        """

        def label_move_func(mp_obj, pos):
            print(f"Moving '{mp_obj.name}' to position: {pos}")
            # display of motors values ?
            mp_obj.move(pos)

        # ACHTUNG: cannot start with a number...
        if pos_label.isidentifier():
            setattr(
                self,
                pos_label,
                functools.partial(label_move_func, mp_obj=self, pos=pos_label),
            )
        else:
            log_error(
                self, f"{self.name}: '{pos_label}' is not a valid python identifier."
            )

    def _read_config(self):
        """Read the configuration."""
        self.targets_dict = {}
        self.positions_list = []
        try:
            for pos in self._config.get("positions"):
                self.positions_list.append(pos)
                self.targets_dict[pos.get("label")] = pos.get("target")
            self.simultaneous = self._config.get("move_simultaneous", True)
            _label = self.position
            if "unknown" not in self.position:
                self._current_label = _label
            if not (self._last_label and self._current_label):
                self._last_label = self.positions_list[0]["label"]
        except TypeError:
            print("No position configured")

    def __info__(self) -> str:
        """Standard method called by BLISS Shell info helper.

        Return the exhaustive status of the object.

        Returns:
            (str): tabulated string
        """
        # HEADER
        table = [("", "LABEL", "DESCRIPTION", "MOTOR POSITION(S)")]

        curr_pos = self._get_position()
        motpos_str = ""
        for pos in self.positions_list:
            descr = pos.get("description", "")
            if pos["label"] == curr_pos:
                mystr = "* "
            else:
                mystr = ""

            motstr = ""
            for mot in self.targets_dict[pos["label"]]:
                motstr += f'{mot["axis"].name}: {mot["destination"]}'
                if not isinstance(mot["destination"], str):
                    motstr += f'(± {mot.get("tolerance", 0):2.3f})'
                motstr += "\n"
                if mot["axis"].name not in motpos_str:
                    motpos_str += f'{mot["axis"].name} = {mot["axis"].position}\n'
            table.append((mystr, pos["label"], descr, motstr))
        # POSITIONS
        pos_str = tabulate(tuple(table), numalign="right", tablefmt="plain")

        return f"{pos_str}\n{motpos_str}"

    @property
    def position(self) -> str:
        """Get the position of the object.

        Returns:
            (str): The position as defined in the label configuration parameter.
        """
        pos = self._get_position()
        if pos == self._current_label:
            self._last_label = pos
        return pos

    def __position_changed(self, pos):
        event.send(self, "position", pos)

    @property
    def state(self) -> str:
        """Get the state of the object."""
        return self._state_from_positioners()

    def __state_changed(self, sta):
        event.send(self, "state", sta)

    def _state_from_positioners(self, label: str | None = None) -> str:
        """The state as defined by the motor(s).

        Args:
            label: The label. If not defined, the last known label will be used.
        Returns:
            (AxisState): The state as a motor state.
        """
        if not label:
            states: dict[Axis, AxisState] = {}
            label = self._current_label or self._last_label
        if label in self.targets_dict:
            for axis in self.targets_dict[label]:
                _axis = axis.get("axis")
                states[_axis] = self._last_states[_axis]
            return self._reduce_axis_states(states)
        return "UNKNOWN"

    def _reduce_axis_states(self, states: dict[Axis, AxisState]) -> str:
        """Merge axis states all together into a single axis state"""

        whole_states = set(
            flatten(
                [
                    s if isinstance(s, str) else s.current_states_names
                    for s in states.values()
                ]
            )
        )
        priorities = [
            "MOVING",
            "OFF",
            "FAULT",
            "DISABLED",
            "READY",
        ]
        for _st in priorities:
            if _st in whole_states:
                return _st

        return "UNKNOWN"

    def __close__(self):
        for axis in self.motor_objs:
            event.disconnect(axis, "state", self.__positioner_state_changed)
            event.disconnect(axis, "position", self.__positioner_position_changed)

    def move(self, label: str, wait: bool = True):
        """Move the motors to the destination.

        The move can be simultaneous or not, as defined in the config
        `move_simultaneously` parameter (default value True).

        Args:
            label: The label of the position to move to.
            wait: Wait until the end of the movement of all the motors.

        Raises:
            RuntimeError: Wrong label
        """
        if label not in self.targets_dict:
            raise RuntimeError(f"{label} is not a valid label")
        if self._g_move is not None:
            raise RuntimeError("A motion is already processing")
        self._current_label = label

        def _do_move(motion: dict[Axis, float]):
            if self.simultaneous:
                is_axis = [isinstance(a, Axis) for a in list(motion.keys())]
                # have to move differently if not all the axes are motors
                if not all(is_axis):
                    for axis, val in motion.items():
                        axis.move(val, wait=False)

                    for axis, val in motion.items():
                        if isinstance(axis, Axis):
                            axis.wait_move()
                        else:
                            axis.wait()
                else:
                    motion_list = flatten([[a, d] for a, d in motion.items()])
                    if is_bliss_shell() and wait:
                        from bliss.shell.standard import umv

                        umv(*motion_list)
                    else:
                        from bliss.common.standard import move

                        move(*motion_list, print_motion=False)
            else:
                for axis, destination in motion.items():
                    axis.move(destination, wait=True)

        motion: dict[Axis, float] = {}
        for axis in self.targets_dict[label]:
            motion[axis.get("axis")] = axis.get("destination")
        self._g_move = gevent.spawn(_do_move, motion)
        self._g_move.link(self._link_motion)
        if wait:
            self.wait()
            self.stop()

    def _link_motion(self, greenlet: gevent.Greenlet):
        self._g_move = None

    def wait(self, timeout: float | None = None, label: str | None = None):
        """Wait for the motors to finish their movement.

        Args:
            timeout: Timeout in second.
            label: Destination position label (only in case of
                   non silultaneous move).

        Raises:
            TimeoutError: Timeout while waiting for motors to move
        """
        if not label:
            label = self._current_label

        if not self._g_move:
            return
        try:
            self._g_move.get(timeout=timeout)
            self._g_move = None
        except gevent.Timeout as exc:
            raise TimeoutError(
                f"Timeout while waiting for '{self.name}' to move"
            ) from exc
        finally:
            self.stop()

    def stop(self):
        """Stop all the moving motors."""
        if self._g_move is None:
            return
        for axis in self.targets_dict[self._current_label]:
            axis.get("axis").stop()
        if self._g_move is None:
            return
        self._g_move.kill(block=True)
        self._g_move = None

    def _in_position(self, motor_destination) -> bool:
        """Check if the destination of a position is within the tolerance.
        Args:
            motor_destination(dict): The motor dictionary.
        Returns:
            (bool): True if on position.
        """
        destination = motor_destination.get("destination")
        if isinstance(destination, str):
            if destination == motor_destination["axis"].position:
                return True
            return False

        # set some tolerance if none defined as we deal with floats
        tolerance = motor_destination.get("tolerance", 0.0001)
        if abs(motor_destination["axis"].position - destination) < tolerance:
            return True
        return False

    def _check_config(self) -> [list]:
        """Check if not the same real motors are used twice.
        Raises:
            RuntimeError: Same real motor in several configurations.
        """
        real_motors_list = []
        err_msg = "Wrong configuration! "
        for val in self.motor_objs:
            if isinstance(val, Axis):
                if val.name in real_motors_list:
                    err_msg += f"{val.name} already used"
                    raise RuntimeError(err_msg)
                real_motors_list.append(val.name)
            else:
                for nval in val.motor_objs:
                    if isinstance(nval, Axis):
                        if nval.name in real_motors_list:
                            err_msg += f"{val.name}:{nval.name} already used"
                            raise RuntimeError(err_msg)
                        real_motors_list.append(nval.name)
        return real_motors_list

    @property
    def motors(self) -> dict[str, Axis]:
        """Return dictionary {NAME: OBJECT} of all the axes."""
        _mot_dict = {}
        for motor in self.targets_dict.values():
            for idx, _ in enumerate(motor):
                if motor[idx]["axis"] not in _mot_dict:
                    _mot_dict.update({f'{motor[idx]["axis"].name}': motor[idx]["axis"]})
        return _mot_dict

    @property
    def motor_names(self) -> list[str]:
        """Return list of NAMES of all the axes."""
        return list(self.motors.keys())

    @property
    def motor_objs(self) -> list[Axis]:
        """Return list of motors OBJECTS of all the axes."""
        return list(self.motors.values())

    def _get_position(self) -> str:
        """Read the postion.
        Returns:
            (str): The position label having all axes at destination.
                   Or 'unknown' if no valid position found.
        """
        # for all positions,
        for label, motor_destinations in self.targets_dict.items():
            in_position = []
            # check all destinations of this position.
            for motor_destination in motor_destinations:
                in_position.append(self._in_position(motor_destination))
            if all(in_position):
                self._last_label = label
                return label
        return "unknown"

    def update_position(
        self,
        label: str,
        motors_destinations_list: list[Axis | tuple[Axis, float, float]] | None = None,
        description: str | None = None,
    ):
        """Update existing label to new motor position(s).

        If only the label specified, the current motor(s) position replaces
        the previous one.

        Args:
            label: The unique position label.
            motors_destinations_list: List of motor(s) or
                                      tuples (motor, position, tolerance).
                                      Important: motor is an Axis object.
                                      tolerance is optional
            description: The description of the position.

        Raises:
            TypeError: motors_destinations_list must be a list
            RuntimeError: Invalid label
        """
        if label not in self.targets_dict:
            raise RuntimeError("Invalid label")

        for elem in self._config["positions"]:
            if label == elem["label"]:
                idx = self._config["positions"].index(elem)
                break

        if description:
            self._config["positions"][idx]["description"] = description

        if motors_destinations_list:
            if not isinstance(motors_destinations_list, list):
                raise TypeError("motors_destinations_list must be a list")
            for element in motors_destinations_list:
                iii = motors_destinations_list.index(element)
                if isinstance(element, tuple):
                    if element[0] == self.targets_dict[label][iii]["axis"]:
                        self._config["positions"][idx]["target"][iii]["axis"] = element[
                            0
                        ]
                        self._config["positions"][idx]["target"][iii][
                            "destination"
                        ] = element[1]
                        try:
                            self._config["positions"][idx]["target"][iii][
                                "tolerance"
                            ] = element[2]
                        except IndexError:
                            pass
                else:
                    if element == self.targets_dict[label][iii]["axis"]:
                        self._config["positions"][idx]["target"][iii][
                            "destination"
                        ] = element.position
        else:
            for element2 in self.targets_dict[label]:
                iiii = self.targets_dict[label].index(element2)
                self._config["positions"][idx]["target"][iiii]["axis"] = element2[
                    "axis"
                ]
                self._config["positions"][idx]["target"][iiii][
                    "destination"
                ] = element2["axis"].position

        self._config.save()
        self._read_config()

    def create_position(
        self,
        label: str,
        motors_destinations_list: list[Axis | tuple[Axis, float, float]],
        description: str | None = None,
    ):
        """Create new position.

        Args:
            label: The unique position label.
            motors_destinations_list: List of motor(s) or
                                      tuples (motor, position, tolerance).
                                      Important: motor is an Axis object.
                                      tolerance is optional.
            description: The description of the position.

        Raises:
            TypeError: motors_destinations_list must be a list
        """
        if label in self.targets_dict:
            raise RuntimeError("Label already exists. Please use update_position")
        target_list = []
        idx = len(self._config["positions"])
        self._config["positions"].append({"label": label})
        if description:
            self._config["positions"][idx].update({"description": description})

        if not isinstance(motors_destinations_list, list):
            raise TypeError("motors_destinations_list must be a list")

        for element in motors_destinations_list:
            if isinstance(element, tuple):
                try:
                    tolerance = element[2]
                except IndexError:
                    tolerance = 0
                target_list.append(
                    {
                        "axis": element[0],
                        "destination": element[1],
                        "tolerance": tolerance,
                    }
                )
            else:
                target_list.append(
                    {"axis": element, "destination": element.position, "tolerance": 0}
                )
            self._config["positions"][idx].update({"target": target_list})

        self._config.save()
        self._read_config()

    def remove_position(self, label):
        """Remove position.

        Args:
            label (str): The unique position label.
        Raises:
            RuntimeError: Try to remove non existing position
        """
        if label not in self.targets_dict:
            raise RuntimeError("Try to remove non existing position")

        for elem in self._config["positions"]:
            if elem["label"] == label:
                self._config["positions"].remove(elem)
                break

        self._config.save()
        self._read_config()
