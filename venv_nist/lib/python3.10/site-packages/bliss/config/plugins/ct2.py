# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.controllers.ct2 import card


def create_objects_from_config_node(config, node):
    klass = node.get("class")
    if klass == "CT2":
        address = node["address"]
        if address.startswith("tcp://"):
            from bliss.controllers.ct2 import client as module
        else:
            from bliss.controllers.ct2 import device as module
    else:
        module = card
    return module.create_object_from_config_node(config, node)
