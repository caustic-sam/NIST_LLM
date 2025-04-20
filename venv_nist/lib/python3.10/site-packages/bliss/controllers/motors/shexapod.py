# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""\
Symetrie hexapod

YAML_ configuration example:

.. code-block:: yaml

    plugin: emotion
    class: SHexapod
    version: 2                           # (1)
    tcp:
      url: id99hexa1
    user_origin: 0 0 328.83 0 0 0        # (2)
    object_origin: 0 0 328.83 0 0 0
    axes:
      - name: h1tx
        role: tx
        unit: mm
      - name: h1ty
        role: ty
        unit: mm
      - name: h1tz
        role: tz
        unit: mm
      - name: h1rx
        role: rx
        unit: deg
      - name: h1ry
        role: ry
        unit: deg
      - name: h1rz
        role: rz
        unit: deg

1. API version: valid values: 1, 2 or 5 (optional. If no version is given, it
   tries to discover the API version). Authors recommend to put the version
   whenever possible.

2. User/objects origins are optional, they are set at startup
"""

from collections import namedtuple
from math import pi
import gevent.lock
import gevent.socket

from bliss.comm.util import get_comm, TCP
from bliss.comm.tcp import SocketTimeout
from bliss.common.axis.state import AxisState
from bliss.controllers.motor import Controller
from bliss import global_map

ROLES = "tx", "ty", "tz", "rx", "ry", "rz"
Pose = namedtuple("Pose", ROLES)

# Symetrie hexapods work only with mm and deg, but mrad and microns are more useful units
CUNIT_TO_UNIT = {
    "mrad": pi / 180.0 * 1000,
    "rad": pi / 180.0,
    "micron": 1 / 1000.0,
    "mm": 1,
    "deg": 1,
}


class BaseHexapod:
    """Motor controller API for Symetrie Hexapod"""

    Pose = Pose

    def __init__(self, config):
        self.config = config
        self.comm = None

    def _homing(self, async_=False):
        raise NotImplementedError

    def _move(self, pose, async_=False):
        raise NotImplementedError

    def _stop(self):
        raise NotImplementedError

    def _reset(self):
        raise NotImplementedError

    #
    # API to Hexapod emotion controller
    #

    # Fist, the ones that must me overwritten sub-class

    @property
    def object_pose(self):
        """
        Return a sequence of tx, ty, tz, rx, ry, rz.
        Translation in mm; Rotation in degrees.
        """
        raise NotImplementedError

    @property
    def system_status(self):
        """
        Return object with (at least) members (bool):
        - control (true if control is active)
        - error (true if there is an error)
        - moving (true if hexapod is moving)
        """
        raise NotImplementedError

    @property
    def tspeed(self):
        """
        Returns translation speed (mm/s)
        """
        raise NotImplementedError

    @tspeed.setter
    def tspeed(self, tspeed):
        """
        Set translation speed (mm/s)
        """
        raise NotImplementedError

    @property
    def rspeed(self):
        """
        Returns rotational speed (deg/s)
        """
        raise NotImplementedError

    @rspeed.setter
    def rspeed(self, rspeed):
        """
        Set rotational speed (mm/s)
        """
        raise NotImplementedError

    @property
    def tacceleration(self):
        """
        Returns translation acceleration (mm/s)
        """
        raise NotImplementedError

    @tacceleration.setter
    def tacceleration(self, taccel):
        """
        Set translation acceleration (mm/s)
        """
        raise NotImplementedError

    @property
    def racceleration(self):
        """
        Returns rotational acceleration (deg/s)
        """
        raise NotImplementedError

    @racceleration.setter
    def racceleration(self, raccel):
        """
        Set rotational acceleration (mm/s)
        """
        raise NotImplementedError

    def start_move(self, pose):
        """Start absolute motion to user coordinates.
        Args:
            pose (list): List of 6 user coordinates [tx, ty, tz, rx, ry, rz]
        Returns:
            AsyncResult: handler which can be used to wait for the end of the
                         motion
        """
        return self._move(pose, async_=True)

    def move(self, pose):
        """Move to given user coordinates and wait for motion to finish.
        Args:
            pose (list): List of 6 user coordinates [tx, ty, tz, rx, ry, rz]
        Returns:
            AsyncResult: handler which can be used to wait for the end of the
                         motion
        """
        return self._move(pose)

    def start_homing(self):
        """Start the homing procedure"""
        return self._homing(async_=True)

    def homing(self):
        """Do the homing procedure"""
        return self._homing()

    def stop(self):
        """Stop the hexapod motors"""
        return self._stop()

    def reset(self):
        """Reset the hexapod"""
        return self._reset()

    def virtual_home(self, userpose):
        """Homing of axes"""
        raise NotImplementedError


class BaseHexapodProtocol(BaseHexapod):
    """Class to configure the communication protocol"""

    DEFAULT_PORT = None

    def __init__(self, config):
        super().__init__(config)
        self.eol = "\r\n"
        self.comm = get_comm(config, ctype=TCP, port=self.DEFAULT_PORT, eol=self.eol)


class BaseHexapodError(Exception):
    """Hexapod errors class"""


class SHexapod(Controller):
    """Symetrie hexapod controller"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._protocol = None

        user_origin = self.config.get("user_origin", "")
        object_origin = self.config.get("object_origin", "")
        self.no_offset = self.config.get("no_offset", bool, True)

        if user_origin and object_origin:
            self.set_origin(user_origin.split(), object_origin.split())

        self.lock = gevent.lock.Semaphore()

    def set_origin(self, user_origin, object_origin):
        """Set the definition of the User and Object coordinate systems.
        The User coordinate system is relative to the Machine coordinate system.
        The Object coordinate system is relative to the Platform one.
        Args:
            Two lists of doubles with positions around X, Y, Z axis.
            user_origin(list): txu, tyu, tzu, rxu, ryu, rzu
            object_origin(list): txo, tyo, tzo, rxo, ryo, rzo.
        """
        if (len(user_origin) + len(object_origin)) != 12:
            raise ValueError(
                "Wrong parameter number: need 12 values to define user and object origin"
            )
        prot = self.protocol()
        if isinstance(prot, HexapodProtocolV1):
            try:
                cmd = ""
                for i in range(0, 6):
                    cmd += f"Q{80+i}={float(user_origin[i])} "
                for i in range(0, 6):
                    cmd += f"Q{86+i}={float(object_origin[i])} "
                cmd += "Q20=21"
            except ValueError as val_err:
                raise TypeError(
                    "Need float values to define user and object origin"
                ) from val_err
            prot.pmac(cmd)
        else:
            raise NotImplementedError

    def __info__(self):
        """
        info about controller.
        """
        info_str = "Symetrie Hexapod"
        info_str += f" Prot:  {self.protocol()} \n"
        return info_str

    def get_info(self, axis):
        return self.protocol().dump()

    def protocol(self):
        """Define which version of the hexapod protocol is to be used.
        Make a connection to the instrument.
        """
        if self._protocol is not None:
            return self._protocol

        version = self.config.config_dict.get("version", None)
        if version == 1:
            all_klass = (HexapodProtocolV1,)
        elif version == 2:
            all_klass = (HexapodProtocolV2,)
        elif version == 5:
            all_klass = (HexapodProtocolV5,)
        else:
            all_klass = (HexapodProtocolV1, HexapodProtocolV2, HexapodProtocolV5)

        for klass in all_klass:
            protocol = klass(self.config.config_dict)
            self._protocol = protocol
            try:
                protocol.connect()
            except (gevent.socket.error, SocketTimeout, ConnectionRefusedError):
                continue
            else:
                break
        else:
            raise ValueError("Could not find Hexapod (is it connected?)")

        assert protocol.comm, "Could not make connection to Hexapod"
        global_map.register(protocol, children_list=[protocol.comm])
        return self._protocol

    def initialize(self):
        # velocity and acceleration are not mandatory in config
        self.axis_settings.config_setting["velocity"] = False
        self.axis_settings.config_setting["acceleration"] = False

    def initialize_hardware(self):
        try:
            self.protocol().control = True
        except AttributeError:
            pass

    def initialize_axis(self, axis):
        role = self.__get_axis_role(axis)
        axis.no_offset = self.no_offset
        if role not in ROLES:
            raise ValueError(f"Invalid role {role!r} for axis {axis.name}")

        # on this controller the homing procedure is particular so here
        # we replace the *axis.home* by the specific homing procedure.
        def _home():
            protocol = self.protocol()
            # start homing
            protocol.homing()
            # Wait the procedure to starts
            gevent.sleep(1)
            while protocol.system_status.moving:
                gevent.sleep(0.1)
            # home_done is not synchronous with moving!!!
            # Wait a little bit
            gevent.sleep(0.5)
            if not protocol.system_status.homing_done:
                print("Home failed check status for more info")
            # Wait that all axis are in position
            while True:
                gevent.sleep(0.2)
                if protocol.system_status.in_position:
                    break
            # Synchronize all hexapod axes.
            for axis in self.axes.values():
                axis.sync_hard()

        axis.home = _home

    def __get_axis_role(self, axis):
        return axis.config.get("role")

    def __get_hw_set_position(self, axis):
        user_set_pos = axis._set_position
        dial_set_pos = axis.user2dial(user_set_pos)
        hw_set_pos = dial_set_pos * axis.steps_per_unit
        return hw_set_pos

    def __get_hw_set_positions(self):
        return dict(
            (
                (
                    self.__get_axis_role(axis),
                    self.__get_hw_set_position(axis) / CUNIT_TO_UNIT[axis.unit],
                )
                for axis in self.axes.values()
            )
        )

    def start_one(self, motion):
        return self.start_all(motion)

    def start_all(self, *motion_list):
        pose_dict = dict(((r, None) for r in ROLES))
        pose_dict.update(self.__get_hw_set_positions())
        pose_dict.update(
            dict(
                (
                    (
                        self.__get_axis_role(motion.axis),
                        motion.target_pos / CUNIT_TO_UNIT[motion.axis.unit],
                    )
                    for motion in motion_list
                )
            )
        )
        pose = Pose(**pose_dict)

        self.protocol().start_move(pose)

    def stop(self, axis):
        self.protocol().stop()

    def stop_all(self, *motions):
        self.protocol().stop()

    def state(self, axis):
        with self.lock:
            status = self.protocol().system_status
        state = "READY"
        if status.moving:
            state = "MOVING"
        if not status.control:
            state = "OFF"
        if status.error:
            state = "FAULT"
        state = AxisState(state)
        return state

    def get_axis_info(self, axis):
        """Get the information for an axis of the hexapod.
        Args:
            axis (object): axis object.
        Returns:
            (str): Axis configuration information.
        """
        try:
            role = self.__get_axis_role(axis)
            if role.startswith("t"):
                return self.protocol().dump(tunit=axis.unit)
            if role.startswith("r"):
                return self.protocol().dump(runit=axis.unit)
        except TypeError:
            return self.protocol().dump()

    def read_position(self, axis):
        """Read the axis position.
        Args:
             axis (object): axis object.
        Returns:
            (float): The axis position [unit set in the axis configuration].
        """
        with self.lock:
            return CUNIT_TO_UNIT[axis.unit] * getattr(
                self.protocol().object_pose, self.__get_axis_role(axis)
            )

    def set_position(self, axis, new_position):
        """Set axis position.
        Args:
            axis (object): axis object.
            new_position (float): The position to which the axis to be set.
        """
        raise NotImplementedError

    def set_on(self, axis):
        """Set axis on.
        Args:
            axis (object): axis object.
        """
        self.protocol().control = True

    def set_off(self, axis):
        """Set axis off.
        Args:
            axis (object): axis object.
        """
        self.protocol().control = False

    def read_velocity(self, axis):
        """Read axis velocity.
        Args:
            axis (object): axis object.
        Returns:
            (float): The speed of the translation or rotation axis.
        """
        if self.__get_axis_role(axis).startswith("t"):
            return self.protocol().tspeed
        return self.protocol().rspeed

    def set_velocity(self, axis, new_velocity):
        new_speeds = {
            "tspeed": self.protocol().tspeed,
            "rspeed": self.protocol().rspeed,
        }
        if self.__get_axis_role(axis).startswith("t"):
            new_speeds["tspeed"] = new_velocity
        else:
            new_speeds["rspeed"] = new_velocity
        self.protocol().speeds = new_speeds

    def read_acceleration(self, axis):
        """Read axis acceleration.
        Args:
            axis (object): axis object.
        Returns:
            (float): The acceleration of the translation or rotation axis.
        """
        if self.__get_axis_role(axis).startswith("t"):
            return self.protocol().tacceleration
        return self.protocol().racceleration

    def set_acceleration(self, axis, new_acc):
        new_acceleration = {
            "tacceleration": self.protocol().tacceleration,
            "racceleration": self.protocol().racceleration,
        }
        if self.__get_axis_role(axis).startswith("t"):
            new_acceleration["tacceleration"] = new_acc
        else:
            new_acceleration["racceleration"] = new_acc
        self.protocol().accelerations = new_acceleration

    def make_ref(self):
        """Make reference - start homing"""
        self.protocol().start_homing()  # async or not?

    def reset(self):
        """Reset the controller"""
        self.protocol().reset()

    def virtual_home(self):
        lastpos = {}
        for axis in self.axes.values():
            lastpos[self.__get_axis_role(axis)] = axis.settings.get("position")
        user_pose = Pose(**lastpos)
        self.protocol().virtual_home(user_pose)


# at end of file to avoid circular import
# pylint: disable=wrong-import-position
from bliss.controllers.motors.shexapodV1 import HexapodProtocolV1  # noqa: E402
from bliss.controllers.motors.shexapodV2 import HexapodProtocolV2  # noqa: E402
from bliss.controllers.motors.shexapodv5 import HexapodProtocolV5  # noqa: E402
