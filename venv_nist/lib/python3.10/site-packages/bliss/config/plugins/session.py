# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.config.plugins.utils import find_class
from bliss.common.measurementgroup import MeasurementGroup


def create_objects_from_config_node(config, item_cfg_node):
    klass = find_class(item_cfg_node, "bliss.common")

    item_name = item_cfg_node["name"]
    if issubclass(klass, MeasurementGroup):
        available_counters = _get_available_counters(config, item_cfg_node)
        if available_counters != item_cfg_node.get("counters", list()):
            item_cfg_node = item_cfg_node.clone()
            item_cfg_node["counters"] = available_counters

    return {item_name: klass(item_name, item_cfg_node)}


def _get_available_counters(config, mes_grp_cfg):
    cnt_list = mes_grp_cfg.get("counters", list())
    include_list = mes_grp_cfg.get("include", list())
    for sub_grp_ref in include_list:
        sub_grp_cfg = config.get_config(sub_grp_ref)
        if sub_grp_cfg is None:
            raise RuntimeError(
                "Reference **%s** in MeasurementGroup **%s** in file %s doesn't exist"
                % (sub_grp_ref, mes_grp_cfg.get("name"), mes_grp_cfg.filename)
            )
        sub_cnt_list = _get_available_counters(config, sub_grp_cfg)
        cnt_list.extend(sub_cnt_list)
    return cnt_list
