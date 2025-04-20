# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import sys
import enum

from bliss.common.axis import axis as axis_module
from bliss.common import encoder as encoder_module
from bliss.common.axis.axis import Axis
from bliss.common.encoder import Encoder
from bliss.config.static import ConfigReference
from bliss.config.plugins.utils import find_class
from bliss.controllers.motor import CalcController


OBJECT_TYPE = enum.Enum("OBJECT_TYPE", "AXIS ENCODER SHUTTER SWITCH")


def create_objects_from_config_node(config, node):
    if "axes" in node or "encoders" in node:
        # asking for a controller
        obj_name = None
    else:
        obj_name = node.get("name")
        node = node.parent

    controller_class_name = node.get("class")
    controller_name = node.get("name")
    if controller_name is None:
        # build a controller name, from the config
        controller_name = f"{controller_class_name}_{node.md5hash()}"
    controller_class = find_class(node, "bliss.controllers.motors")
    controller_module = sys.modules[controller_class.__module__]
    axes = dict()
    encoders = dict()
    switches = dict()
    shutters = dict()
    cache_dict = dict()

    for (
        objects,
        object_type,
        default_class,
        default_class_name,
        config_nodes_list,
    ) in (
        (axes, OBJECT_TYPE.AXIS, Axis, "Axis", node.get("axes", [])),
        (encoders, OBJECT_TYPE.ENCODER, Encoder, "Encoder", node.get("encoders", [])),
        (shutters, OBJECT_TYPE.SHUTTER, None, "Shutter", node.get("shutters", [])),
        (switches, OBJECT_TYPE.SWITCH, None, "Switch", node.get("switches", [])),
    ):
        for config_dict in config_nodes_list:
            object_name = config_dict.raw_get("name")
            if isinstance(object_name, ConfigReference):
                object_class = None
                object_name = object_name.object_name
            else:
                cache_dict[object_name] = object_type, config_dict
                object_class_name = config_dict.get("class")

                if object_class_name is None:
                    try:
                        object_class = getattr(controller_module, default_class_name)
                    except AttributeError:
                        object_class = default_class
                else:
                    try:
                        object_class = getattr(controller_module, object_class_name)
                    except AttributeError:
                        default_module = {
                            OBJECT_TYPE.AXIS: axis_module,
                            OBJECT_TYPE.ENCODER: encoder_module,
                        }
                        module = default_module.get(object_type)
                        if module is not None:
                            object_class = getattr(module, object_class_name)
                        else:
                            raise
            objects[object_name] = object_class, config_dict

    controller = controller_class(
        controller_name, node, axes, encoders, shutters, switches
    )
    cache_dict = {
        name: (controller, object_type, config_dict)
        for name, (object_type, config_dict) in cache_dict.items()
    }
    objects_dict = {}
    if controller_name:
        objects_dict[controller_name] = controller
    yield objects_dict, cache_dict

    # evaluate referenced axes
    for axis_name, (axis_class, config_dict) in axes.items():
        if axis_class is None:  # mean reference axis
            create_object_from_cache(
                config, axis_name, (controller, OBJECT_TYPE.AXIS, config_dict)
            )
    if isinstance(controller, CalcController):
        # As any motors can be used into a calc
        # force for all axis creation
        for axis_name in list(cache_dict.keys()):
            config.get(axis_name)

    controller._init()

    if obj_name is not None:
        obj = config.get(obj_name)
        yield {obj_name: obj}


def create_object_from_cache(config, name, cache_objects):
    controller, object_type, config_dict = cache_objects
    if object_type == OBJECT_TYPE.AXIS:
        return controller.get_axis(name)
    elif object_type == OBJECT_TYPE.ENCODER:
        return controller.get_encoder(name)
    elif object_type == OBJECT_TYPE.SWITCH:
        return controller.get_switch(name)
    elif object_type == OBJECT_TYPE.SHUTTER:
        return controller.get_shutter(name)
    else:
        raise RuntimeError("Object type not managed")
