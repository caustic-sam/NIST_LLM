# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Symetrie hexapod controlled using the provided Python API and gpascii.
Need to install Symetrie library libsymetriehexapodlibrarie.
Attention libsymetriehexapodlibrarie requires paralles-ssh.
YAML_ configuration example:

.. code-block:: yaml

    plugin: emotion
    class: SHexapod
    ip: id99hexa1
    verbose_mode: False
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

"""
from collections import namedtuple
from enum import Enum, unique
from tabulate import tabulate
from bliss.common.logtools import log_error
from bliss.controllers.motors.shexapod import BaseHexapod, Pose, CUNIT_TO_UNIT

try:
    from libsymetriehexapod import API
except ImportError:
    pass


@unique
class ValidationEnum(Enum):
    """Validation Limitations"""

    FL = 0, "Factory workspace limits"
    ML = 1, "Machine workspace limits"
    UL = 2, "User workspace limits"
    AL = 3, "Actuator limits"
    JL = 4, "Joints limits"
    BC = 5, "Due to backlash compensation"


class HexapodProtocolV5(BaseHexapod):
    """Base protocol V5 implementation"""

    SYSTEM_STATUS_FIELDS = (
        "error",
        "ready",
        "control",
        "in_position",
        "moving",
        "homing_running",
        "homing_done",
        "homing_virtual",
        "phase",
        "brake",
        "motion_restrained",
        "power_encoder",
        "power_limitswitch",
        "power_drives",
        "emergency_stop",
    )

    SystemStatus = namedtuple("SystemStatus", SYSTEM_STATUS_FIELDS)

    AXIS_STATUS_FIELDS = (
        "error",
        "control",
        "in_position",
        "moving",
        "homing_running",
        "homing_done",
        "phase",
        "brake",
        "home_hardware_input",
        "negative_hardware_limit",
        "positive_hardware_limit",
        "software_limit",
        "following_error",
        "drive_fault",
        "encoder_error",
    )

    AxisStatus = namedtuple("AxisStatus", AXIS_STATUS_FIELDS)

    DUMP_TEMPLATE = """\

Symetrie hexapod
----------------
API version: {o.api_version}

{system_status}
{actuators}

{pose}

User limits: {o.user_limits_enabled}; \
Machine limits: {o.machine_limits_enabled}

{speed}

"""
    # Current translation speed: {speeds[0]} mm/s
    # Current rotation speed: {speeds[1]} deg/s

    def __init__(self, config):
        self.ip_addr = config.get("tcp")["url"]
        self._verbose = config.get("verbose_mode", False)
        self.accelerations = {}
        super().__init__(config)

    def connect(self):
        """Connect to the controller"""
        try:
            self.comm = API()
        except NameError:
            msg = f"Symetrie hexapod {self.ip_addr}: could not instantiate"
            msg += "libsymetriehexapod API. Hint: did you install the package ?"
            log_error(self, msg)
        else:
            self.comm.connect(self.ip_addr, self._verbose)

    @property
    def controller_id(self):
        """Read the controller ID.
        Retuns:
            (str): Controller ID
        """
        asw = self.comm.SendCmd("VERSION?", [], 12).split()
        if self._verbose:
            print(f"---->  controller_id: {asw}")
        return {
            "id": f"Controller ID: {asw[9]} - {asw[10]}",
            "version": f"{asw[5]}.{asw[6]}.{asw[7]}.{asw[8]}",
        }

    @property
    def api_version(self):
        """Read the API version
        Returns:
            (str): The API version
        """
        return self.controller_id["version"]

    def _limits(self, csys):
        """Get the hexapode Factory, Machine or User coordinate system limits.
        Args:
            csys (int): Factory (0), Machinen (1) or User (2) limits.
        Returns:
            (dict): Dictionary of two lists, containing the low and hight limits
                    fof the requested coordinate system in the order
                    tx, ty, tz, rx, ry, rz.
        """
        asw = list(map(float, self.comm.SendCmd("CFG_LIMIT?", [csys], 13).split()))
        if self._verbose:
            print(f"---> _limits: {asw}")
        low_limit = asw[1:7]
        high_limit = asw[7:]
        return {"low_limit": low_limit, "high_limit": high_limit}

    @property
    def factory_limits(self):
        """Get the factory limits, expressed in machine coordinate system limits.
        Returns:
            (dict): Low and High limits in the order tx, ty, tz, rx, ry, rz.
        """
        return self._limits(0)

    @property
    def machine_limits(self):
        """Get the Machine coordinate system limits.
        Returns:
            (dict): Low and High limits in the order tx, ty, tz, rx, ry, rz.
        """
        return self._limits(1)

    @property
    def user_limits(self):
        """Get the User coordinate system limits.
        Returns:
            (dict): Low and High limits in the order tx, ty, tz, rx, ry, rz.
        """
        return self._limits(2)

    @property
    def _limits_status(self):
        """Get the workspace limits enable state
        Returns:
            (list): False - disabled, True - enabled limits for coordinate
                    systems in the order Factory, Machine, User.
        """
        asw = list(map(int, self.comm.SendCmd("CFG_LIMITENABLE?", [], 3).split()))
        if self._verbose:
            print(f"---> _limits_status: {asw}")
        return asw

    def _limits_status_enable(self, csys, enable):
        """Enable/disable limits
        Args:
            csys (int): Machine (1) or User (2) coordinate system.
            enable (bool): True to enable, False to disable
        """
        if self._verbose:
            print(f"---> _limits_status_enable: {csys}, {enable}")
        if csys in (1, 2):
            self.comm.SendCmd("CFG_LIMITENABLE", [csys, enable], 2)

    @property
    def factory_limits_enabled(self):
        """Get the Factory coordinate system limits enable state.
        Returns:
            (bool): True - enable, False - disable.
        """
        return bool(self._limits_status[0])

    @property
    def machine_limits_enabled(self):
        """Get the Machine coordinate system limits enable state.
        Returns:
            (bool): True - enable, False - disable.
        """
        return bool(self._limits_status[1])

    @machine_limits_enabled.setter
    def machine_limits_enabled(self, enable):
        """Set the Machine coordinate system limits enable state.
        Args:
            enable (bool): True - enable, False - disable.
        """
        self._limits_status_enable(1, enable)

    @property
    def user_limits_enabled(self):
        """Get the User coordinate system limits enable state.
        Returns:
            (bool): True - enable, False - disable.
        """
        return bool(self._limits_status[2])

    @user_limits_enabled.setter
    def user_limits_enabled(self, enable):
        """Set the User coordinate system limits enable state.
        Args:
            enable (bool): True - enable, False - disable.
        """
        self._limits_status_enable(2, enable)

    @property
    def full_system_status(self):
        """Full status: general state, object_pose and platform_pose.
        Get the actuiators status and the leght of each actuator.
        The returned positions are always in the order tx, ty, tz, rx, ry, rz.
        object_pose is the position of the Object coordinate system in
        the User coordinate system.
        platform_pose is the position of the Platform coordinate system
        in the Machine coordinate system.
        Returns:
            (dict): state, object_pose, platform_pose,
                    actuators_status, actuators_length.
                    Translations (tx, ty, tz) and actuators length [mm],
                    rotations (rx, ry, rz) [deg]
        """
        asw = self.comm.Term("s_hexa,50,1").split()
        if self._verbose:
            print(f"---> full_system_status: {asw}")
        status = int(asw[0])
        current_action = int(asw[1])
        # object position list tx, ty, tz, rx, ry, rz
        o_pos = Pose(*list(map(float, asw[2:8])))
        # platform position list tx, ty, tz, rx, ry, rz
        p_pos = Pose(*list(map(float, asw[8:14])))
        # actuators status for tx, ty, tz, rx, ry, rz
        a_stat = list(map(int, asw[14:20]))
        # actuators length tx, ty, tz, rx, ry, rz
        a_l = list(map(float, asw[20:26]))

        return {
            "status": status,
            "current_action": current_action,
            "object_pose": o_pos,
            "platform_pose": p_pos,
            "actuators_status": a_stat,
            "actuators_length": a_l,
        }

    @property
    def verbose_system_status(self):
        """Get the Hexapod system status.
        Returns:
            (str): Status
        """
        return self._bitfield_to_string(
            self.full_system_status["status"], self.comm.s_hexa
        )

    @property
    def system_status(self):
        """Get the Hexapod system status.
        Returns:
            (namedtupple): Status
        """
        return self._bitfield_to_klass(
            self.full_system_status["status"], self.SystemStatus
        )

    @property
    def object_pose(self):
        """Get the translation [mm] and rotation [deg] current positions of
        the hexapod. Corresponds to the position of the Object coordinate
        system in the User coordinate system.
        Returns:
            (list): List of floats tx, ty, tz, rx, ry, rz
        """
        pos = self.full_system_status["object_pose"]
        if self._verbose:
            print("---> object_pose:")
            print(f"   tx: {pos[0]}, ty: {pos[1]}, tz: {pos[2]}")
            print(f"   rx: {pos[3]}, ry: {pos[4]}, rz: {pos[5]}")
        return pos

    @property
    def platform_pose(self):
        """Get the translation [mm] and rotation [deg] current positions of
        the hexapod. Corresponds to the position of the Platform coordinate
        system in the Machine coordinate system.
        Returns:
            (list): List of floats tx, ty, tz, rx, ry, rz
        """
        return self.full_system_status["platform_pose"]

    @property
    def full_actuators_status(self):
        """Get the status of the six actuators
        Returns:
            (list): List of six bitfields, representing the status for each leg.
        """
        stat_list = []
        for stat in self.full_system_status["actuators_status"]:
            stat_list.append(self._bitfield_to_klass(stat, self.AxisStatus))
        return stat_list

    @property
    def actuators_length(self):
        """Get the length [mm] for each leg.
        Returns:
            (list): List of six floats.
        """
        return self.full_system_status["actuators_length"]

    @property
    def user_and_object_cs(self):
        """Read the definition of the User and Object coordinate systems.
        The User coordinate system is relative to the Machine coordinate system.
        The Object coordinate system is relative to the Platform one.
        Returns:
            (dict): Dictionary of two lists of doubles with positions [mm]
                    around X, Y, Z axis as follows:
                    user_cs: User (txu, tyu, tzu, rxu, ryu, rzu)
                    object_cs: Object (txo, tyo, tzo, rxo, ryo, rzo)
        """
        asw = list(map(float, self.comm.SendCmd("CFG_CS?", [], 12).split()))
        if self._verbose:
            print(f"---> user_and_object_cs: {asw}")
        user_cs = Pose(*map(float, asw[:6]))
        object_cs = Pose(*map(float, asw[6:]))
        return {"user_cs": user_cs, "object_cs": object_cs}

    @property
    def user_and_object_cs_unit(self):
        """Read the definition of the User and Object coordinate systems in the            units of the motor.
        Returns:
            (dict): Dictionary of two lists of doubles with positions [mm]
                    around X, Y, Z axis as follows:
                    user_cs: User (txu, tyu, tzu, rxu, ryu, rzu)
                    object_cs: Object (txo, tyo, tzo, rxo, ryo, rzo)
        """
        unit = "mrad"
        asw = list(map(float, self.comm.SendCmd("CFG_CS?", [], 12).split()))
        if self._verbose:
            print(f"---> user_and_object_cs_unit: {asw}")
        asw[3:6] = [x * CUNIT_TO_UNIT[unit] for x in asw[3:6]]
        asw[-3:] = [x * CUNIT_TO_UNIT[unit] for x in asw[-3:]]
        user_cs_mrad = Pose(*map(float, asw[:6]))
        object_cs_mrad = Pose(*map(float, asw[6:]))
        return {"user_cs": user_cs_mrad, "object_cs": object_cs_mrad}

    def set_origin(self, user_origin, object_origin):
        """Set the definition of the User and Object coordinate systems.
        The User coordinate system is relative to the Machine coordinate system.
        The Object coordinate system is relative to the Platform one.
        Args:
            Two lists of doubles with positions [mm] around X, Y, Z axis.
            user_origin(list): txu, tyu, tzu, rxu, ryu, rzu
            object_origin(list): txo, tyo, tzo, rxo, ryo, rzo.
        """
        cmd_list = user_origin + object_origin
        try:
            asw = self.comm.SendCmd("CFG_CS", cmd_list, 12)
            if isinstance(asw, int) and asw < 0:
                print(self.comm.CommandReturns.get(asw, "Unknown error"))
                return False
            asw = int(asw.split()[0])
        except (TypeError, ValueError):
            return False
        if asw == 0:
            return True
        return False

    @user_and_object_cs.setter
    def user_and_object_cs(self, values_dict):
        """Set the definition of the User and Object coordinate systems.
        The User coordinate system is relative to the Machine coordinate system.
        The Object coordinate system is relative to the Platform one.
        Args:
            (dict): Dictionary of two lists of doubles with positions [mm]
                    around X, Y, Z axis as follows:
                    user_cs: User (txu, tyu, tzu, rxu, ryu, rzu)
                    object_cs: Object (txo, tyo, tzo, rxo, ryo, rzo)
        """

    @property
    def speed_all(self):
        """Read the translation and rotation current, min and max speed.
        Returns:
            (list): translation [mm/s] and rotational [deg/s] speed:
                    [0] - current translation
                    [1] - current rotation
                    [2] - min translation
                    [3] - min rotation
                    [4] - max translation
                    [5] - max rotation.
        """
        asw = list(map(float, self.comm.SendCmd("CFG_SPEED?", [], 6).split()))

        if self._verbose:
            print("---> speed:")
            print(f" - current: translation {asw[0]} mm/s, rotation {asw[1]} deg/s")
            print(f" - minimum: translation {asw[2]} mm/s, rotation {asw[3]} deg/s")
            print(f" - maximum: translation {asw[4]} mm/s, rotation {asw[5]} deg/s")
        return asw

    @speed_all.setter
    def speed_all(self, value_list):
        """Set the current translation and rotation speed.
        Args:
            value_list(list): translation [mm/s] and rotational [deg/s] speed:
                    (float) [0] - current translation
                    (float) [1] - current rotation
        """
        if self.speed_all[:2] == value_list:
            print(f"requested speed {value_list} already set, nothing done")
            return value_list
        asw = self.comm.SendCmd("CFG_SPEED", value_list, 2)
        return asw

    @property
    def tspeed(self):
        """Read the current translation speed.
        Returns:
            (float): The speed [mm/s]
        """
        return self.speed_all[0]

    @property
    def rspeed(self):
        """Read the current rotation speed.
        Returns:
            (float): The speed [deg/s]
        """
        return self.speed_all[1]

    def _stop(self):
        """Stop the movement."""
        self.comm.SendCmd("STOP")

    def _reset(self):
        """Abstract method. No relevant command in API, nothing done."""

    @property
    def racceleration(self):
        """Abstrcact method. No relevant command in API, nothing done."""

    def acceleration_all(self):
        """Get the translational acceleration current, min and max time.
        Returns:
            (dict): current, min and max acceleration time [s].
        """
        try:
            asw = self.comm.SendCmd("CFG_TA?", [], 3)

            self.accelerations = {
                "tacceleration": float(asw[0]),
                "tacceleration_min": float(asw[1]),
                "tacceleration_max": float(asw[2]),
            }
            return self.accelerations
        except (ValueError, TypeError) as err:
            raise RuntimeError("Cannot read the acceleration") from err

    @property
    def tacceleration(self):
        """Get the translational acceleration time.
        Returns:
            (float): Acceleration time [s]
        """
        return self.acceleration_all()["tacceleration"]

    @tacceleration.setter
    def tacceleration(self, tacc):
        """Set the translational acceleration time.
        Args:
            tacc(float): Translational acceleration time [s].
        """
        self.comm.SendCmd("CFG_TA", tacc, 1)
        self.accelerations["tacceleration"] = tacc

    def _homing(self, async_=False):
        """Do the homing - to be used when the hexapod is not equiped with
        absolute encoder. The command can be executed only if:
          - there is no motion task running
          - the emergency stop button is not engaged.
        """
        # async_ parameter is not used
        self.comm.SendCmd("HOME")

    @property
    def homing_options(self):
        """Get the homing auto/virtual, enabled/disabled, absolute/relative.
        Returns:
            (list): Homing [auto, virtual, type]
                    0=disabled, 1 = enabled
                    0=absolute, 1=relative
        """
        asw = self.comm.SendCmd("CFG_HOME?", [], 3)
        return asw

    def validate_move(self, mode, values_list):
        """
        Verify if the movement defined by the arguments is feasible.
        Args:
            mode(int): Control mode 0=absolute, 1=object relative, 2=user_relative
            values_list (list): [tx, ty, tz, rx, ry, rz] translation [mm] and rotation [deg].
        Returns:
            (bool): Target position is valid (True).
        Raises:
            RuntimeError: Invalid movement
        """
        cmd_list = [1, mode] + values_list
        try:
            asw = self.comm.SendCmd("VALID_PTP", cmd_list, 8)
            if isinstance(asw, int) and asw < 0:
                # Error
                err = self.comm.CommandReturns.get(asw, "Unknown error")
                raise RuntimeError(err)
            asw = int(asw.split()[0])
        except (TypeError, ValueError) as err:
            raise RuntimeError(f"Validation error: {err}") from err
        if asw == 0:
            # Target position is valid
            return True
        if asw > 0:
            # Limitation
            err = f"Validation error: {self._bitfield_to_string(asw, ValidationEnum)}"
            raise RuntimeError(err)
        raise RuntimeError("Validation error: Unknown")

    def _move(self, pose, async_=False):
        """Move the hexapode"""
        values_list = []
        for role in pose._fields:
            value = getattr(pose, role)
            if value is None:
                values_list.append(getattr(self.object_pose, role))
            else:
                values_list.append(getattr(pose, role))
        self.__move(values_list, mode=0)

    def __move(self, values_list, mode):
        """
        Start to move to position. Performed only if the following
        conditions are met:
          - there is no motion task running
          - home is completed
          - the control loop on servo motors is activated
          - the hexapod is not stopping
        Args:
            values_list(list): [tx, ty, tz, rx, ry, rz] translation [mm] and rotation [deg].
            mode(int): Control mode 0=absolute, 1=object relative, 2=user_relative
        Raises:
            RuntimeError: Cannot move
        """
        self.validate_move(mode, values_list)

        cmd_list = [mode] + values_list
        self.comm.SendCmd("MOVE_PTP", cmd_list, 7)

    @property
    def control(self):
        """Read the state of the control loop on motors.
        Returns:
            (bool): True if control loop activated, False otherwise.
        """
        return self.system_status.control

    @control.setter
    def control(self, control):
        """Activate/Disable the control loop on servo motors. Performed only
        if the following conditions are met:
          - there is no motion task running.
          - there is no action running.
        Wnen activate:
          Switch on the power on the motors and release the brakes if present.
          Should be used before starting a movement.
        When disable:
          It is advisable to disable the servo motors if the system is not
          used for a long period (more than 1 hour for example).
        Args:
            control(bool): True (activate), False (disable).
        """
        curr_control = self.system_status.control
        if control and not curr_control:
            self.comm.SendCmd("CONTROLON")
        elif not control and curr_control:
            self.comm.SendCmd("CONTROLOFF")

    @property
    def current_action(self):
        """Get teh currently executing action
        Returns:
            (str): Action
        """
        return self.comm.s_action[self.full_system_status["current_action"]]

    def _bitfield_to_string(self, value, descriptions):
        """Translate bitfield to human readble string.
        Args:
            value(int): value, returned by the command
            descriptions(Enum or dict): Enum or dictionary containing the
                                   descriptions.
        Returns:
            (str): Description string.
        """
        ret_string = ""
        if isinstance(descriptions, dict):
            for bit in descriptions:
                if value & (1 << bit):
                    ret_string += f"{descriptions[bit]}\n"
        else:
            for key in descriptions.__members__.values():
                if value & (1 << key.value[0]):
                    ret_string += f"{key.value[1]}\n"
        return ret_string

    def _bitfield_to_klass(self, value, klass):
        """Translate bitfield to named tupple.
        Args:
            value(int): value, returned by the command
            klass(namedtupple): The named tuppel to be converted.
        Returns:
            (class): Class, correspondingn to the namedtupple
        """
        return klass(*[bool(value & (1 << i)) for i in range(len(klass._fields))])

    def dump(self, tunit="mm", runit="mrad"):
        """Print full status.
        Args:
            tunit (str): Translation axes unit. Piossible values - mm, micron.
                         Default value mm.
            runit (str): Rotation axes unit. Possible values - mrad, rad, deg.
                         Default value mrad.
        """
        full_system_status = self.full_system_status
        csys = self.user_and_object_cs
        machine_limits = self.machine_limits
        user_limits = self.user_limits
        speed_all = self.speed_all

        system_status_table = (
            f"System status\n--------------\n{self.verbose_system_status}"
        )

        act_header = [f"#{i} [mm]" for i in range(1, 7)]
        headers = ["Actuators"] + act_header
        rows = [
            ["Length"] + self.actuators_length,
            # ["Status"] + self.full_actuators_status
        ]
        actuators_table = tabulate(rows, headers=headers)

        pose_header = [f"{i} [mm]" for i in self.Pose._fields[:3]] + [
            "{i} [mrad]" for i in self.Pose._fields[3:]
        ]
        user_cs = ["User coordinate system"] + list(csys["user_cs"])
        user_cs[-6:-3] = [x * CUNIT_TO_UNIT[tunit] for x in user_cs[-6:-3]]
        user_cs[-3:] = [x * CUNIT_TO_UNIT[runit] for x in user_cs[-3:]]
        object_cs = ["Object coordinate system"] + list(csys["object_cs"])
        object_cs[-6:-3] = [x * CUNIT_TO_UNIT[tunit] for x in object_cs[-6:-3]]
        object_cs[-3:] = [x * CUNIT_TO_UNIT[runit] for x in object_cs[-3:]]
        object_pose = ["Object position"] + list(full_system_status["object_pose"])
        object_pose[-6:-3] = [x * CUNIT_TO_UNIT[tunit] for x in object_pose[-6:-3]]
        object_pose[-3:] = [x * CUNIT_TO_UNIT[runit] for x in object_pose[-3:]]
        platform_pose = ["Platform position"] + list(
            full_system_status["platform_pose"]
        )
        platform_pose[-6:-3] = [x * CUNIT_TO_UNIT[tunit] for x in platform_pose[-6:-3]]
        platform_pose[-3:] = [x * CUNIT_TO_UNIT[runit] for x in platform_pose[-3:]]

        user_limits["low_limit"][-6:-3] = [
            x * CUNIT_TO_UNIT[tunit] for x in user_limits["low_limit"][-6:-3]
        ]
        user_limits["low_limit"][-3:] = [
            x * CUNIT_TO_UNIT[runit] for x in user_limits["low_limit"][-3:]
        ]
        user_limits["high_limit"][-6:-3] = [
            x * CUNIT_TO_UNIT[tunit] for x in user_limits["high_limit"][-6:-3]
        ]
        user_limits["high_limit"][-3:] = [
            x * CUNIT_TO_UNIT[runit] for x in user_limits["high_limit"][-3:]
        ]
        machine_limits["low_limit"][-6:-3] = [
            x * CUNIT_TO_UNIT[tunit] for x in machine_limits["low_limit"][-6:-3]
        ]
        machine_limits["low_limit"][-3:] = [
            x * CUNIT_TO_UNIT[runit] for x in machine_limits["low_limit"][-3:]
        ]
        machine_limits["high_limit"][-6:-3] = [
            x * CUNIT_TO_UNIT[tunit] for x in machine_limits["high_limit"][-6:-3]
        ]
        machine_limits["high_limit"][-3:] = [
            x * CUNIT_TO_UNIT[runit] for x in machine_limits["high_limit"][-3:]
        ]

        rows = [
            user_cs,
            object_cs,
            object_pose,
            platform_pose,
            ["Low user limits"] + user_limits["low_limit"],
            ["High user limits"] + user_limits["high_limit"],
            ["Low machine limits"] + machine_limits["low_limit"],
            ["High machine limits"] + machine_limits["high_limit"],
        ]
        headers = ["Hexapod"] + pose_header
        pose_table = tabulate(rows, headers=headers)

        headers = ["Speed", "Current", "Min", "Max"]
        rows = [
            ["Translation", speed_all[0], speed_all[2], speed_all[4]],
            ["Rotation", speed_all[1], speed_all[3], speed_all[5]],
        ]
        speed_table = tabulate(rows, headers=headers)

        return self.DUMP_TEMPLATE.format(
            o=self,
            speed=speed_table,
            system_status=system_status_table,
            actuators=actuators_table,
            pose=pose_table,
        )
