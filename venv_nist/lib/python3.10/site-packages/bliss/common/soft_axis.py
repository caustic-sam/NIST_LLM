# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.controllers.motors.soft import SoftController
from bliss import current_session


def SoftAxis(
    name,
    obj,
    position="position",
    move="position",
    stop=None,
    state=None,
    low_limit=float("-inf"),
    high_limit=float("+inf"),
    tolerance=None,
    export_to_session=True,
    unit=None,
    as_positioner=False,
    display_digits=None,
):
    """
    Create a software axis named `name` from any object `obj`.

    The software axis object class will be the standard :class:`Axis` class.

    Arguments:
        position (str): the name of a method of <obj> which will return the position of the soft axis
        move     (str): the name of a method of <obj> which will start the motion of the soft axis to a given position
        stop     (str): the name of a method of <obj> which will stop the motion of the soft axis
        state    (str): the name of a method of <obj> which will return the state of the soft axis
                        returned state must be an object of class :class:`AxisState`
                        returned state can be one or a combination of the following states:
                        - `AxisState("READY")`   : "Axis is READY"
                        - `AxisState("MOVING")`  : "Axis is MOVING"
                        - `AxisState("FAULT")`   : "Error from controller"
                        - `AxisState("LIMPOS")`  : "Hardware high limit active"
                        - `AxisState("LIMNEG")`  : "Hardware low limit active"
                        - `AxisState("HOME")`    : "Home signal active"
                        - `AxisState("OFF")`     : "Axis power is off"
                        - `AxisState("DISABLED")`: "Axis cannot move"
        low_limit  (float): the soft axis lower limit
        high_limit (float): the soft axis upper limit
        tolerance  (float): the soft axis tolerance
        unit       (str)  : the soft axis position unit
        export_to_session (bool): export the axis name into the current session env_dict (name must be unique)
        as_positioner     (bool): allow display of the soft axis in wa() as any other real axis
        display_digits    (int): Number of digits used to print positions of this axis
    """

    config = {"low_limit": low_limit, "high_limit": high_limit, "name": name}

    if tolerance is not None:
        config["tolerance"] = tolerance

    if unit is not None:
        config["unit"] = unit

    if display_digits is not None:
        config["display_digits"] = display_digits

    controller = SoftController(name, obj, config, position, move, stop, state)
    controller._initialize_config()

    axis = controller.get_axis(name)
    axis._positioner = as_positioner

    if export_to_session and current_session:
        if (
            name in current_session.config.names_list
            or name in current_session.env_dict.keys()
        ):
            raise ValueError(
                f"Cannot export object to session with the name '{name}', name is already taken! "
            )

        current_session.env_dict[name] = axis

    return axis
