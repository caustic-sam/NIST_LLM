# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.comm.util import get_comm


def create_objects_from_config_node(config, cfg_node):
    com_obj = get_comm(cfg_node)
    com_name = cfg_node["name"]

    return {com_name: com_obj}
