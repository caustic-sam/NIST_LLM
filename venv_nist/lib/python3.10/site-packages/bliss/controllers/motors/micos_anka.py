# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
    BLISS controller for MICOS motor controller

"""


import time
import gevent
from bliss.controllers.motor import Controller
from bliss.comm.util import get_comm, TCP
from bliss.comm.tcp import SocketTimeout
from bliss.common.axis.state import AxisState
from bliss.config.channels import Cache


class MicosAnka(Controller):
    def initialize(self):
        config = self.config.config_dict
        ip_address = config["tcp"]["url"]
        try:
            opt = {"port": 6542, "eol": b"\n", "timeout": 2.0}
            self.comm = get_comm(config, ctype=TCP, **opt)
            _, version, _, _ = self.comm.write_readlines(b"\n", 4)
            self.version = version.decode().strip()
            self.comm.flush()

        except SocketTimeout:
            raise RuntimeError(
                f"\n\tCannot connect to Micos MotionServer on {ip_address}! \n"
            )

    def initialize_axis(self, axis):
        """
        Reads specific config
        Adds specific methods
        """

        axis._mode = Cache(axis, "mode", default_value=None)

    def flush(self):
        time.sleep(0.1)
        self.comm.flush()
        time.sleep(0.1)

    def read_position(self, axis):
        """
        Returns position's setpoint or measured position.

        Args:
            - <axis> : bliss axis.
            - [<measured>] : boolean : if True, function returns
              measured position in ???
        Returns:
            - <position> : float : axis setpoint in ???.
        """

        self.flush()
        address = axis.config.get("address", int)
        pos = self.raw_write_read(axis, f"{self.name} Crds ?")
        position = pos.split(" ")[address + 1]
        try:
            return float(position) * axis.steps_per_unit
        except Exception:
            time.sleep(0.1)
            return self.read_position(axis)

    def set_position(self, axis, new_position):
        pass

    def read_encoder(self, encoder):
        raise NotImplementedError

    def read_velocity(self, axis):
        """
        Args:
            - <axis> : Bliss axis object.
        Returns:
            - <velocity> : float
        """
        address = axis.config.get("address", int)
        vel = self.raw_write_read(axis, f"{self.name} Speed ?")
        velocity = vel.split(" ")[address + 1]
        return float(velocity) * axis.steps_per_unit

    def set_velocity(self, axis, new_velocity):
        address = axis.config.get("address", int)
        new_velocity = new_velocity / axis.steps_per_unit
        cmd = f"{self.name} Speed {address} {new_velocity}"
        self.raw_write(axis, cmd)

    def read_acceleration(self, axis):
        address = axis.config.get("address", int)
        acc = self.raw_write_read(axis, f"{self.name} Accel ?")
        accel = acc.split(" ")[address + 1]
        return float(accel) * axis.steps_per_unit

    def set_acceleration(self, axis, new_acceleration):
        address = axis.config.get("address", int)
        new_acceleration = new_acceleration / axis.steps_per_unit
        cmd = f"{self.name} Accel {address} {new_acceleration}"
        self.raw_write(axis, cmd)

    def state(self, axis):
        msg = f"{self.name} IsReady\n"
        _ans = self.comm.write_readline(msg.encode())
        if "not ready" in _ans.decode():
            return AxisState("MOVING")
        elif "ready" in _ans.decode():
            time.sleep(1)
            return AxisState("READY")
        else:
            print("\n")
            print(_ans.decode())
            return AxisState("UNKNOWN")

    def home_state(self, axis):
        while self.state(axis) == AxisState("UNKNOWN"):
            time.sleep(0.1)
        return self.state(axis)
        # while self.state(axis) != AxisState("READY"):
        #    time.sleep(0.5)

    def prepare_move(self, motion):
        cmd = f"{self.name} Power on"
        self.raw_write(motion.axis, cmd)

    def start_one(self, motion):
        """
        sdf
        """
        address = motion.axis.config.get("address", int)
        pos = motion.target_pos
        pos = pos / motion.axis.steps_per_unit
        cmd = f"{self.name} AxisAbs {address} {pos}"
        msg = self.raw_write_read(motion.axis, cmd)
        if "limits" in msg:
            raise RuntimeError("Movement not possible due to soft limits")

    def start_all(self, *motions):
        self.flush()
        axis = motions[0].axis
        pos = self.raw_write_read(axis, f"{self.name} Crds ?")
        cmd = pos.split()
        cmd[1] = "MoveAbs"
        for motion in motions:
            address = motion.axis.config.get("address", int)
            cmd[address + 1] = str(motion.target_pos / motion.axis.steps_per_unit)
        cmd = " ".join(cmd)
        # print(cmd)
        msg = self.raw_write_read(axis, cmd)
        if "limits" in msg:
            raise RuntimeError("Movement not possible due to soft limits")

    def home_search(self, axis, switch):
        address = axis.config.get("address", int)
        cmd = f"{self.name} refMove {address}"
        self.raw_write(axis, cmd)
        gevent.sleep(0.2)

    def start_jog(self, axis, velocity, direction):
        raise NotImplementedError

    def stop(self, axis):
        cmd = f"{self.name} Stop"
        self.raw_write(axis, cmd)

    def raw_readline(self):
        return self.comm.readline(timeout=10).decode()

    def raw_write(self, axis, cmd):
        self.comm.flush()
        cmd = cmd + "\n"
        self.comm.write(cmd.encode("ascii"))

    def raw_write_read(self, axis, cmd):
        self.comm.flush()
        cmd = cmd + "\n"
        msg = self.comm.write_readline(cmd.encode("ascii"), timeout=2)
        msg = msg.decode()
        # print("RETURN", msg)
        return msg

    def get_id(self, axis):
        """
        Returns firmware version.
        """
        return self.version

    def get_info(self, axis):
        """
        Returns information about controller as a string.
        """
        txt = f"MICOS Motion Server - {self.version}"
        return txt
