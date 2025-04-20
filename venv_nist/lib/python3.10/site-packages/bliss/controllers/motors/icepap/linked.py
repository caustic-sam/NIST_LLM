# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.common.axis.axis import (
    Axis,
    NoSettingsAxis,
    DEFAULT_POLLING_TIME,
    lazy_init,
)
from bliss.common.axis.motion import Motion
from bliss.common.axis.group_move import GroupMove
from bliss.controllers.motors.icepap.comm import _ackcommand, _command
from bliss.common.utils import autocomplete_property
from bliss.common.logtools import log_debug
import types
import gevent


class LinkedAxis(Axis):
    class Real(NoSettingsAxis):
        def __info__(self):
            cnx = self.controller._cnx
            pre_cmd = f"{self.address}:"
            info = "REAL AXIS CONTROLLED BY LINKED AXIS:\n"
            info += f"Real axis address     : {self.address}\n"
            value = _command(cnx, pre_cmd + "?NAME", origin_axis=self)
            info += f"Real axis name        : {value}\n"
            value = (
                "NO"
                if _command(cnx, pre_cmd + "?CFG HOMESRC", origin_axis=self) == "NONE"
                else "YES"
            )
            info += f"Home switch           : {value}\n"
            value = _command(cnx, pre_cmd + "?HOMESTAT", origin_axis=self).split()[0]
            info += f"Homing                : {value}\n"
            value = (
                "NO"
                if _command(cnx, pre_cmd + "?CFG CTRLENC", origin_axis=self) == "NONE"
                else "YES"
            )
            info += f"Control encoder       : {value}\n"
            pos = int(_command(cnx, pre_cmd + "?POS", origin_axis=self))
            info += f"Indexer steps         : {pos}\n"
            measure = int(_command(cnx, pre_cmd + "?POS MEASURE", origin_axis=self))
            info += f"Encoder steps         : {measure}\n"
            try:
                value = _command(cnx, pre_cmd + "?HOMEPOS", origin_axis=self)
            except RuntimeError:
                value = "unavailable"
            info += f"Homepos steps         : {value}\n"
            info += f"Indexer user unit     : {pos / self.steps_per_unit}\n"
            info += f"Encoder user unit     : {measure / self.steps_per_unit}\n"
            value = _command(cnx, pre_cmd + "?PCLOOP", origin_axis=self)
            info += f"Closed loop           : {value}\n"
            return info

    def __init__(self, name, controller, config):
        Axis.__init__(self, name, controller, config)
        if config.get("address") is None:
            self.config.set("address", name)
        self.__real_axes_namespace = None
        self.__in_disprotected_move = False

    def _init_hardware(self):
        linked_axis = self.controller.get_linked_axis()
        if linked_axis.get(self.address) is None:
            raise RuntimeError(
                "Linked axis named %s doesn't exist ;"
                "linked axis configured in the system are : %s"
                % (self.address, linked_axis.keys())
            )

    def _init_software(self):
        # check if real motors are also defined in the config
        linked_axis = self.controller.get_linked_axis()
        mot_addresses = linked_axis.get(self.address)
        if mot_addresses is None:
            raise RuntimeError(
                f"Axis {self.name} (address: {self.address}) is not a linked axis"
            )

        for name, axis in self.controller.axes.items():
            if axis.config.get("address", converter=None) in mot_addresses:
                raise RuntimeError(
                    "Cannot initialize linked axis '%s',"
                    " real axis '%s' found in controller configuration"
                    % (self.name, axis.name)
                )

    @autocomplete_property
    @lazy_init
    def real_axes(self):
        if self.__real_axes_namespace is None:
            real_axes = {}
            linked_axis = self.controller.get_linked_axis()
            mot_addresses = linked_axis.get(self.address)

            for address in mot_addresses:
                axis_name = _command(
                    self.controller._cnx, "%d:?NAME" % address, origin_axis=self
                )
                config_dict = {
                    "autopower": False,
                    "steps_per_unit": self.steps_per_unit,
                    "acceleration": self.acceleration,
                    "velocity": self.velocity,
                }
                real_axis = LinkedAxis.Real(axis_name, self.controller, config_dict)
                real_axis.address = address
                self.controller._Controller__initialized_axis[real_axis] = True
                real_axes[axis_name] = real_axis
            self.__real_axes_namespace = types.SimpleNamespace(**real_axes)
        return self.__real_axes_namespace

    @lazy_init
    def sync(self, user_position):
        """
        Synchronizes all real linked axes members of the given virtual axis
        to the given position. No motion will take place.
        The position is given in user units of the virtual axis.
        """
        dial_position = self.user2dial(user_position)
        self.dial = dial_position
        self.acceleration = self.acceleration
        self.velocity = self.velocity
        # Reset control encoder
        # TODO: if any?
        _ackcommand(self.controller._cnx, "CTRLRST %s" % self.address)
        # switch power on (should re-enable the closed loop)
        self.on()

        self.sync_hard()

        self.position = user_position

        return self.position

    def get_linked_info(self):
        cnx = self.controller._cnx
        info_str = "ICEPAP LINKED AXIS:\n"
        info_str += f"    address / icepapcms linked name: {self.address}\n"
        for axis in self.real_axes.__dict__.values():
            pre_cmd = f"{axis.address}:"
            info_str += f"    real axis ({axis.name}): ADDR:{axis.address}"
            info_str += f"  POWER:{_command(cnx, pre_cmd + '?POWER', origin_axis=axis)}"
            info_str += (
                f"  CLOOP:{_command(cnx, pre_cmd + '?PCLOOP', origin_axis=axis)}"
            )
            _warn_str = _command(cnx, pre_cmd + "?WARNING", origin_axis=self).strip()
            info_str += f"  WARNING:{_warn_str}"
            info_str += f"  ALARM:{_command(cnx, pre_cmd + '?ALARM', origin_axis=axis)}"
            info_str += f"  POS:{_command(cnx, pre_cmd + '?POS', origin_axis=axis)}\n"
        return info_str

    @lazy_init
    def real_axes_status(self):
        cnx = self.controller._cnx

        info = "\nVIRTUAL LINKED AXIS:\n"
        info += f"Bliss axis name       : {self.name}\n"
        info += f"Icepapcms name/address: {self.address}\n"
        value = _command(cnx, "?POWER %s" % self.address, origin_axis=self)
        info += f"POWER                 : {value}\n"
        pos = int(_command(cnx, "?POS %s" % self.address, origin_axis=self))
        info += f"Indexer steps         : {pos}\n"
        info += f"Indexer in user unit  : {pos / self.steps_per_unit}\n"

        for add in self.real_axes.__dict__.values():
            info += "\n" + add.__info__()

        print(info)

    @lazy_init
    def disprotected_move(self, axis, user_target_pos, wait=True, use_hook=True):
        if axis not in self.real_axes.__dict__.values():
            raise RuntimeError(
                f"{self.name}: disprotected move is reserved for real axes"
            )

        try:
            self.__in_disprotected_move = True
            with self._lock:
                if self.is_moving:
                    raise RuntimeError("axis %s state is %r" % (self.name, "MOVING"))

                pos = int(round(self.user2dial(user_target_pos) * self.steps_per_unit))

                # add linked axis motion hooks to real axis
                axis.motion_hooks.clear()
                if use_hook:
                    axis.motion_hooks.extend(self.motion_hooks)

                # create motion object for hooks
                motion = Motion(axis, None, None)

                def start_one(controller, motions):
                    cnx = controller._cnx
                    _command(
                        cnx,
                        cmd=f"{axis.address}:MOVE {pos}",
                        pre_cmd=f"DISPROT LINKED {axis.address} ; ",
                        origin_axis=axis,
                    )
                    gevent.sleep(0.2)

                def stop_one(controller, motions):
                    controller.stop(motions[0].axis)

                _group_move = GroupMove()
                _group_move.start(
                    {self.controller: [motion]},
                    None,  # no prepare
                    start_one,
                    stop_one,
                    wait=False,
                )
        finally:
            self.__in_disprotected_move = False
        if wait:
            _group_move.wait()

    @property
    @lazy_init
    def _hw_position(self):
        if self.__in_disprotected_move and self.is_moving:
            # do not really read hw pos when moving,
            # since it can be a disprotected move, that
            # would report an error
            return self.dial
        else:
            return super()._hw_position

    @lazy_init
    def home(self, switch=1, wait=True, polling_time=DEFAULT_POLLING_TIME):
        raise NotImplementedError(
            "Linked axis homing cannot be an automatic procedure, see with electronics unit."
        )

    @lazy_init
    def disprotected_command(self, real_axis, cmd):
        if real_axis not in self.real_axes.__dict__.values():
            raise RuntimeError(
                f"{self.name}: disprotected move is reserved for real axes"
            )
        self.__disprotected_command(real_axis, cmd)

    def __disprotected_command(self, real_axis, cmd):
        addr = real_axis.address
        return _command(
            self.controller._cnx,
            f"{addr}:DISPROT LINKED ; {addr}:{cmd}",
            origin_axis=real_axis,
        )

    @lazy_init
    def open_closed_loop(self):
        for axis in self.real_axes.__dict__.values():
            self.__disprotected_command(axis, "PCLOOP OFF")

    @lazy_init
    def activate_closed_loop(self):
        for axis in self.real_axes.__dict__.values():
            # activate closed loop
            self.__disprotected_command(axis, "PCLOOP ON")
        # enable power on all axis
        self.on()

    @lazy_init
    def reset_closed_loop(self):
        for axis in self.real_axes.__dict__.values():
            log_debug(self, f"linked axis reset closed loop on {axis.name}")
            # sync to encoder
            self.__disprotected_command(axis, "ESYNC")
            # activate closed loop
            self.__disprotected_command(axis, "PCLOOP ON")
        # enable power on all axis
        self.on()

    @lazy_init
    def reset(self, use_hook=True):
        # sync with encoder and get axis position
        log_debug(self, "linked axis reset started")
        user_steps = list()
        for axis in self.real_axes.__dict__.values():
            log_debug(self, f"linked axis reset closed loop on {axis.name}")
            addr = axis.address
            # sync to encoder
            self.__disprotected_command(axis, "ESYNC")
            # get axis position
            steps = int(
                _command(self.controller._cnx, f"{addr}:?POS", origin_axis=axis)
            )
            user_steps.append(steps)

        # move each axis to mean position
        mean_steps = int(round(sum(user_steps) / len(user_steps)))
        mean_pos = self.dial2user(mean_steps / self.steps_per_unit)

        moved = False
        for (axis, steps) in zip(self.real_axes.__dict__.values(), user_steps):
            if steps != mean_steps:
                log_debug(self, f"linked axis move {axis.name} to {mean_pos}")
                self.disprotected_move(axis, mean_pos, use_hook=use_hook)
                moved = True

        if moved:
            sync_pos = int(
                _command(
                    self.controller._cnx, "?POS %s" % self.address, origin_axis=self
                )
            )
            sync_pos = self.dial2user(sync_pos / self.steps_per_unit)

            # sync linked axis to mean position
            log_debug(self, f"linked axis sync to {mean_pos}")
            self.sync(sync_pos)
        log_debug(self, "linked axis reset done")
