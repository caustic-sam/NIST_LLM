# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations
import re
import warnings

warnings.simplefilter("once", DeprecationWarning)


class AxisState:
    """
    State of axis, defined as a set of state name.

    Standard states:

    - `MOVING`  : Axis is moving
    - `READY`   : Axis ready to handle a new request
    - `FAULT`   : Error from controller
    - `LIMPOS`  : Hardware high limit active
    - `LIMNEG`  : Hardware low limit active
    - `HOME`    : Home signal active
    - `OFF`     : Axis power is off
    - `DISABLED`: Axis cannot move (must be enabled - not ready ?)

    When creating a new instance, you can pass any number of arguments, each
    being either a string or tuple of strings (state, description). They
    represent custom axis states.

    Arguments:
        states: Can be one or many string or tuple of strings (state, description)
    """

    #: state regular expression validator
    STATE_VALIDATOR = re.compile(r"^[A-Z0-9]+\s*$")

    _STANDARD_STATES = {
        "READY": "Axis is READY",
        "MOVING": "Axis is MOVING",
        "FAULT": "Error from controller",
        "LIMPOS": "Hardware high limit active",
        "LIMNEG": "Hardware low limit active",
        "HOME": "Home signal active",
        "OFF": "Axis power is off",
        "DISABLED": "Axis cannot move",
        "UNKNOWN": "Axis state not handled",
    }

    _STANDARD_STATES_STYLES = {
        "READY": "class:success",
        "MOVING": "class:info",
        "FAULT": "class:danger",
        "LIMPOS": "class:warning",
        "LIMNEG": "class:warning",
        "HOME": "class:success",
        "OFF": "class:info",
        "DISABLED": "class:secondary",
        "UNKNOWN": "class:danger",
    }

    @property
    def READY(self) -> bool:
        """Axis is ready to be moved"""
        return "READY" in self._current_states

    @property
    def MOVING(self) -> bool:
        """Axis is moving"""
        return "MOVING" in self._current_states

    @property
    def FAULT(self) -> bool:
        """Error from controller"""
        return "FAULT" in self._current_states

    @property
    def LIMPOS(self) -> bool:
        """Hardware high limit active"""
        return "LIMPOS" in self._current_states

    @property
    def LIMNEG(self) -> bool:
        """Hardware low limit active"""
        return "LIMNEG" in self._current_states

    @property
    def OFF(self) -> bool:
        """Axis power is off"""
        return "OFF" in self._current_states

    @property
    def HOME(self) -> bool:
        """Home signal active"""
        return "HOME" in self._current_states

    @property
    def DISABLED(self) -> bool:
        """Axis is disabled (must be enabled to move (not ready ?))"""
        return "DISABLED" in self._current_states

    def __init__(self, *states: tuple[str, str] | AxisState | str):
        # set of active states.
        self._current_states: list[str] = list()

        # dict of descriptions of states.
        self._state_desc: dict[str, str] = self._STANDARD_STATES

        for state in states:
            if isinstance(state, tuple):
                self.create_state(*state)
                self.set(state[0])
            else:
                if isinstance(state, AxisState):
                    state = state.current_states()
                self._set_state_from_string(state)

    def states_list(self) -> list[str]:
        """
        Return a list of available/created states for this axis.
        """
        return list(self._state_desc)

    def _check_state_name(self, state_name: str):
        if not isinstance(state_name, str) or not AxisState.STATE_VALIDATOR.match(
            state_name
        ):
            raise ValueError(
                "Invalid state: a state must be a string containing only block letters"
            )

    def _has_custom_states(self):
        return self._state_desc is not AxisState._STANDARD_STATES

    def create_state(self, state_name: str, state_desc: str | None = None):
        """
        Adds a new custom state

        Args:
            state_name (str): name of the new state
        Keyword Args:
            state_desc (str): state description [default: None]

        Raises:
            ValueError: if state_name is invalid
        """
        # Raises ValueError if state_name is invalid.
        self._check_state_name(state_name)
        if state_desc is not None and "|" in state_desc:
            raise ValueError(
                "Invalid state: description contains invalid character '|'"
            )

        # if it is the first time we are creating a new state, create a
        # private copy of standard states to be able to modify locally
        if not self._has_custom_states():
            self._state_desc = AxisState._STANDARD_STATES.copy()

        if state_name not in self._state_desc:
            # new description is put in dict.
            if state_desc is None:
                state_desc = "Axis is %s" % state_name
            self._state_desc[state_name] = state_desc

            # Makes state accessible via a class property.
            # NO: we can't, because the objects of this class will become unpickable,
            # as the class changes...
            # Error message is: "Can't pickle class XXX: it's not the same object as XXX"
            # add_property(self, state_name, lambda _: state_name in self._current_states)

    """
    Flags ON a given state.
    ??? what about other states : clear other states ???  -> MG : no
    ??? how to flag OFF ???-> no : on en cree un nouveau.
    """

    def set(self, state_name: str):
        """
        Activates the given state on this AxisState

        Args:
            state_name (str): name of the state to activate

        Raises:
            ValueError: if state_name is invalid
        """
        if state_name in self._state_desc:
            if state_name not in self._current_states:
                self._current_states.append(state_name)

                # Mutual exclusion of READY and MOVING
                if state_name == "READY":
                    if self.MOVING:
                        self._current_states.remove("MOVING")
                if state_name == "MOVING":
                    if self.READY:
                        self._current_states.remove("READY")
        else:
            raise ValueError("state %s does not exist" % state_name)

    def unset(self, state_name: str):
        """
        Deactivates the given state on this AxisState

        Args:
            state_name (str): name of the state to deactivate

        Raises:
            ValueError: if state_name is invalid
        """
        self._current_states.remove(state_name)

    def clear(self):
        """Clears all current states"""
        # Flags all states off.
        self._current_states = list()

    @property
    def current_states_names(self) -> list[str]:
        """
        Return a list of the current states names
        """
        return self._current_states[:]

    def current_states(self) -> str:
        """
        Return a string of current states.

        Return:
            str: *|* separated string of current states or string *UNKNOWN* \
            if there is no current state
        """
        states = [
            "%s%s"
            % (
                state.rstrip(),
                (
                    " (%s)" % self._state_desc[state]
                    if self._state_desc.get(state)
                    else ""
                ),
            )
            for state in map(str, self._current_states)
        ]

        if len(states) == 0:
            return "NOT_READY"

        return " | ".join(states)

    def _set_state_from_string(self, state):
        # is state_name a full list of states returned by self.current_states() ?
        # (copy constructor)
        if "(" in state:
            full_states = [s.strip() for s in state.split("|")]
            p = re.compile(r"^([A-Z0-9_]+)\s\((.+)\)", re.DOTALL)
            for full_state in full_states:
                m = p.match(full_state)
                if m is None:
                    raise ValueError(f"Wrong state format: '{state}'")
                state = m.group(1)
                desc = m.group(2)
                self.create_state(state, desc)
                self.set(state)
        else:
            if state != "NOT_READY":
                self.create_state(state)
                self.set(state)

    def __str__(self):
        return self.current_states()

    def __repr__(self):
        return "AxisState: %s" % self.__str__()

    def __contains__(self, other):
        if isinstance(other, str):
            if not self._current_states:
                return other == "NOT_READY"
            return other in self._current_states
        else:
            return NotImplemented

    def __eq__(self, other):
        if isinstance(other, str):
            warnings.warn("Use: **%s in state** instead" % other, DeprecationWarning)
            return self.__contains__(other)
        elif isinstance(other, AxisState):
            return set(self._current_states) == set(other._current_states)
        else:
            return NotImplemented

    def new(self, share_states=True):
        """
        Create a new AxisState which contains the same possible states but no
        current state.

        If this AxisState contains custom states and *share_states* is True
        (default), the possible states are shared with the new AxisState.
        Otherwise, a copy of possible states is created for the new AxisState.

        Keyword Args:
            share_states: If this AxisState contains custom states and
                          *share_states* is True (default), the possible states
                          are shared with the new AxisState. Otherwise, a copy
                          of possible states is created for the new AxisState.

        Return:
            AxisState: a copy of this AxisState with no current states
        """
        result = AxisState()
        if self._has_custom_states() and not share_states:
            result._state_desc = self._state_desc.copy()
        else:
            result._state_desc = self._state_desc
        return result
