# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import weakref

from bliss.controllers.keithley import Multimeter

CTRL = weakref.WeakValueDictionary()


def create_sensor(node):
    ctrl_node = node.parent
    while ctrl_node and "sensors" not in ctrl_node:
        ctrl_node = ctrl_node.parent

    try:
        name = ctrl_node["name"]
    except KeyError:
        name = node["name"]

    ctrl = CTRL.setdefault(name, Multimeter(ctrl_node))

    sensor_names = [sensor_node["name"] for sensor_node in ctrl_node["sensors"]]
    for s_name in sensor_names:
        CTRL[s_name] = ctrl
    obj = ctrl.Sensor(node, ctrl)
    return obj


def create_objects_from_config_node(config, node):
    name = node["name"]
    if "sensors" in node:
        # controller node
        obj = Multimeter(node)
        CTRL[name] = obj
        for s_node in node["sensors"]:
            create_sensor(s_node)
    else:
        # sensor node
        obj = create_sensor(node)
    return {name: obj}
