# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
The ESRF white beam attenuators are motor driven coper poles with several
holes/filters.

Each attenuator pole has positive/negative limit switch and a home switch
active for each filter. The configuration procedure tries to find
the home switches and set the position of each filter at the middle of the
home switch position.

Example YAML_ configuration:

.. code-block::

  name: wba
  plugin: bliss
  class: WhiteBeamAttenuator
  attenuators:
    - attenuator: $wba_Al
    - attenuator: $wba_Mo
    - attenuator: $wba_Cu

Each attenuator pole has to be configured as bliss MultiplePosition object.
"""
from gevent import sleep
from bliss import global_map
from bliss.common.utils import grouped
from bliss.common.logtools import log_error
from bliss.common.shutter import BaseShutter, BaseShutterState
from bliss.common.hook import MotionHook


class FeMotionHook(MotionHook):
    def __init__(self, frontend: BaseShutter):
        self.frontend = frontend

    def pre_move(self, motion_list):
        if not self.frontend.is_closed:
            raise RuntimeError("Cannot move motor when frontend is not on a safe state")


class CheckHook(MotionHook):
    def __init__(self, att, attname):
        self.att = att
        self.attname = attname

    def post_move(self, motion_list):
        attobj = self.att._get_attenuator(self.attname)

        # ?? remove the hook itself
        for axis in attobj.motors.values():
            axis.motion_hooks.remove(self)

        attstate = attobj.position
        wagostate = self.att.read_switch_state(self.attname)

        if attstate == "unknown" and wagostate is None:
            log_error(self, "Attenuator {self.attname} did not stop on a SWITCH")
        elif attstate != wagostate:
            log_error(
                self,
                "Attenuator {self.attname} inconsistency: position at {attstate}, switch at {wagostate}",
            )


class WhiteBeamAttenuatorWago:
    """Methods to control White Beam Attenuator."""

    def __init__(self, name, config):
        self.__name = name
        self.wago = config.get("wago")
        attconf = config.get("attenuators")
        if attconf is None:
            raise ValueError(f"{self.__error_msg}: missing attenuators section")
        self._parse_config(attconf)

        self._add_frontend_hooks(config.get("frontend"))
        global_map.register(self, tag=name)

    @property
    def name(self):
        return self.__name

    @property
    def __error_msg(self):
        return f"WhiteBeamAttenuatorWago[{self.__name}]"

    def _parse_config(self, attconfig):
        self.attenuators = list()
        self.wagokeys = dict()
        for (idx, config) in enumerate(attconfig):
            attobj = config["attenuator"]
            attname = attobj.name

            # -- check wagokeys
            wkeys = config.get("wagokeys")
            if wkeys is None:
                raise ValueError(f"{self.__error_msg}: missing wagokeys for {attname}")
            wkeys = dict(wkeys)

            # -- keys must match attenuator label
            poslist = [p["label"] for p in attobj.positions_list]
            for pos in poslist:
                if pos not in wkeys:
                    raise ValueError(
                        f"{self.__error_msg}: missing wago key for {pos} on {attname}"
                    )
            for key in wkeys:
                if key not in poslist:
                    raise ValueError(
                        f"{self.__error_msg}: unknown label {key} on {attname}"
                    )

            # -- keep track
            self.attenuators.append(attobj)
            self.wagokeys[attobj.name] = wkeys

    def _add_frontend_hooks(self, frontend):
        if not frontend:
            return
        if not hasattr(frontend, "state") or frontend.state not in BaseShutterState:
            raise RuntimeError("Could not create Frontend hook")

        for att in self.attenuators:
            for motor in att.motors.values():
                motor.motion_hooks.append(FeMotionHook(frontend))

    def find_configuration(self, attenuator_name):
        att = self._get_attenuator(attenuator_name)
        motor = att.motor_objs[0]

        print(" - Search negative limit switch")
        motor.hw_limit(-1)
        print(" - Reset motor position to 0")
        motor.position = 0
        motor.dial = 0
        print(" - Check no switch are active")
        state = self.read_switch_state(attenuator_name)
        if state is not None:
            raise RuntimeError(
                f"{self.__error_msg}: filter switch active at motor negative limit"
            )
        print(" - Search positive limit switch")
        motor.hw_limit(1, wait=False)

        last_state = None
        switch_pos = dict()
        while motor.is_moving:
            pos = motor.position
            new_state = self.read_switch_state(attenuator_name)
            if last_state != new_state:
                if last_state is None and new_state not in switch_pos:
                    switch_pos[new_state] = [
                        pos,
                    ]
                    print(f"switch {new_state} start at {pos}")
                if new_state is None and last_state in switch_pos:
                    switch_pos[last_state].append(pos)
                    print(f"switch {last_state} end at {pos}")
            last_state = new_state
            sleep(0.01)
        print(" - Motor stopped")

        print("\nFilter switch positions found:")
        for (name, pos) in switch_pos.items():
            midpos = (pos[0] + pos[1]) / 2.0
            midsize = (pos[1] - pos[0]) / 2.0
            print(
                f" - {name} : from {pos[0]:.3f} to {pos[1]:.3f} ==> {midpos:.3f} +/- {midsize:.3f}"
            )
            switch_pos[name] = midpos

        return switch_pos

    def update_configuration(self, att_name, new_positions):
        """Update already existing positions for a given attenuator
        Args:
            (str): attenuator name configured as multiple position axis.
            (dict): label:position
        """
        att = self._get_attenuator(att_name)

        for lbl, pos in new_positions.items():
            att.update_position(lbl, [(att.motor_objs[0], pos)])

    @property
    def state(self):
        """Read the state"""
        msg = ""
        for att in self.attenuators:
            msg += f"{att.name}: {att.state} "
        return msg

    @property
    def switch_state(self):
        msg = ""
        for attname in self.wagokeys:
            state = self.read_switch_state(attname)
            state = state is None and "unknown" or state
            msg += f"{attname}: {state} "
        return msg

    def read_switch_state(self, attname):
        wkeys = self.wagokeys[attname]
        vals = self.wago.get(*wkeys.values())
        state = [name for (name, val) in zip(wkeys.keys(), vals) if not val]
        if len(state) == 0:
            return None
        if len(state) > 1:
            raise RuntimeError(
                f"{self.__error_msg}: multiple switch active on wago for {attname}"
            )
        return state[0]

    def __info__(self):
        """Return the exhaustive status of the object.
        Returns:
            (str): The status as string
        """
        info_str = ""
        for att in self.attenuators:
            att_name = att.name
            info_str += f"Attenuator: '{att_name}'\n"
            info_str += att.__info__()[:-1]  # remove trailing '\n'

            wstate = self.read_switch_state(att_name)
            if wstate is None:
                index = info_str.rfind("\n")
                info_str = (
                    info_str[: index + 1]
                    + " WARNING:"
                    + info_str[index + 1 :]
                    + " NO switch active\n"
                )
            else:
                info_str += f" {wstate} switch ON\n"
            info_str += "\n"
        return info_str

    @property
    def position(self):
        """Read the position of the attenuators.
        Returns:
            (list): atteuator, position for all the attenuators.
        """
        pos = []
        for att in self.attenuators:
            pos += [att.name, att.position]
        return pos

    def move(self, *att_name_pos_list, wait: bool = True):
        """Move attenuator(s) to given position.

        The attenuators are moved simultaneously.

        Args:
            att_name_pos_list(list): two elements per attenuator: (name or
                                     attenuator object, position)
            wait: wait until the end of move. Default value is True.
        """

        if len(att_name_pos_list) == 1:
            # assuming is a list or tuple
            att_name_pos_list = att_name_pos_list[0]

        # start moving all the attenuators
        for arg_in, pos in grouped(att_name_pos_list, 2):
            attenuator = self._get_attenuator(arg_in)

            for motor_obj in attenuator.motors.values():
                # add hook
                motor_obj.motion_hooks.insert(0, CheckHook(self, attenuator.name))

            attenuator.move(pos, wait=False)

        # wait the end of the move
        if wait:
            self.wait(att_name_pos_list)

    def _get_attenuator(self, arg_in):
        if hasattr(arg_in, "name"):
            name = arg_in.name
        elif isinstance(arg_in, str):
            name = arg_in
        else:
            raise RuntimeError(
                f"{self.__error_msg}: Provide a valid attenuator object or name"
            )

        atts = [obj for obj in self.attenuators if obj.name == name]
        if not len(atts):
            raise ValueError(f"{self.__error_msg}: wrong attenuator object or name")
        if len(atts) > 1:
            raise RuntimeError(
                f"{self.__error_msg}: multiple attenuators with same name"
            )

        return atts[0]

    def wait(self, *att_name_pos_list):
        """Wait until the end of move finished.

        Args:
            att_name_pos_list(list): two elements per attenuator: (name or
                                     attenuator object, position)
        """
        if len(att_name_pos_list) == 1:
            # assuming is a list or tuple
            att_name_pos_list = att_name_pos_list[0]

        for name, _ in grouped(att_name_pos_list, 2):
            self._get_attenuator(name).wait()
