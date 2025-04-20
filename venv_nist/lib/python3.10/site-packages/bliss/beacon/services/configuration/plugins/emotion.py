# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


import os
import sys
import pkgutil
from collections.abc import MutableSequence
from jinja2 import Environment, FileSystemLoader

import flask.json

from bliss.common.utils import auto_coerce
from bliss.config.static import ConfigNode
from bliss.common.tango import DeviceProxy, _DeviceProxy, DevFailed
import bliss.controllers.motors


__KNOWN_AXIS_PARAMS = {
    "name": str,
    "controller": str,
    "user_tag": lambda x: x.split(","),
    "unit": str,
    "steps_per_unit": float,
    "velocity": float,
    "acceleration": float,
    "backlash": float,
    "low_limit": float,
    "high_limit": float,
}

__KNOWN_CONTROLLER_PARAMS = ("name", "class", "plugin", "axes")

__this_path = os.path.realpath(os.path.dirname(__file__))

__environment = None


def __get_controller_class_names():
    return bliss.controllers.motors.__all__


def get_jinja2():
    global __environment
    if __environment is not None:
        return __environment
    __environment = Environment(loader=FileSystemLoader(__this_path))
    return __environment


def get_item(cfg):
    klass = cfg.get("class")
    result = {"class": klass}
    if klass in [None, "MockupAxis", "ModuloAxis", "NoSettingsAxis"]:
        result["icon"] = "fa fa-gear"
        result["type"] = "axis"
    else:
        result["icon"] = "fa fa-gears"
        result["type"] = "controller"
    return result


def get_tree(cfg, perspective):
    item = get_item(cfg)
    name = cfg.get("name")
    ctrl_class = cfg.get("class")
    if ctrl_class is None:
        path = os.path.join(get_tree(cfg.parent, "files")["path"], name)
    else:
        if perspective == "files":
            path = os.path.join(cfg.filename, name)
        else:
            path = name
    item["path"] = path
    return item


def get_html(cfg):
    ctrl_class = cfg.get("class")
    if ctrl_class in [None, "MockupAxis", "ModuloAxis", "NoSettingsAxis"]:
        return get_axis_html(cfg)
    else:
        return get_ctrl_html(cfg)


def get_axis_html(cfg):
    name = cfg["name"]
    ctrl_class = cfg.parent.get("class")
    ctrl_name = cfg.parent.get("name")
    vars = dict(cfg.items())
    vars["filename"] = cfg.filename
    filename = "emotion_" + ctrl_class + "_axis.html"
    html_template = get_jinja2().select_template([filename, "emotion_axis.html"])

    extra_params = {}
    for key, value in vars.items():
        if key not in __KNOWN_AXIS_PARAMS:
            extra_params[key] = dict(name=key, label=key.capitalize(), value=value)

    tags = cfg.get(ConfigNode.USER_TAG_KEY, [])
    if not isinstance(tags, (tuple, list, MutableSequence)):
        tags = [tags]
    vars["tags"] = tags
    vars["controller_class"] = ctrl_class
    if ctrl_name:
        vars["controller_name"] = ctrl_name
    vars["params"] = extra_params
    vars["units"] = cfg.get("unit", "unit")
    controllers = list()
    vars["controllers"] = controllers
    for controller_name in __get_controller_class_names():
        controllers.append({"class": controller_name})
    vars["__tango_server__"] = __is_tango_device(name)

    return html_template.render(**vars)


def get_ctrl_html(cfg):
    ctrl_class = cfg.get("class")
    vars = dict(cfg.items())
    vars["filename"] = cfg.filename

    filename = "emotion_" + ctrl_class + ".html"
    html_template = get_jinja2().select_template([filename, "emotion_controller.html"])

    extra_params = []
    for key, value in vars.items():
        if key not in __KNOWN_CONTROLLER_PARAMS:
            extra_params.append(dict(name=key, label=key.capitalize(), value=value))

    vars["params"] = extra_params
    controllers = list()
    vars["controllers"] = controllers
    pkgpath = os.path.dirname(bliss.controllers.motors.__file__)
    for _, controller_name, _ in pkgutil.iter_modules([pkgpath]):
        controllers.append({"class": controller_name})

    for axis in vars["axes"]:
        device = __is_tango_device(axis["name"])
        if device:
            vars["__tango_server__"] = True
            break
    else:
        vars["__tango_server__"] = False

    return html_template.render(**vars)


def __is_tango_device(name):
    try:
        # using _DeviceProxy for this purpose (no side effects)
        proxy = _DeviceProxy(name)
    except Exception:
        proxy = None
    return proxy is not None


def __tango_apply_config(name):
    try:
        device = DeviceProxy(name)
        device.command_inout("ApplyConfig", True)
        msg = "'%s' configuration saved and applied to server!" % name
        msg_type = "success"
    except DevFailed as df:
        msg = "'%s' configuration saved but <b>NOT</b> applied to " " server:\n%s" % (
            name,
            df[0].desc,
        )
        msg_type = "warning"
        sys.excepthook(*sys.exc_info())
    except Exception as e:
        msg = "'%s' configuration saved but <b>NOT</b> applied to " " server:\n%s" % (
            name,
            str(e),
        )
        msg_type = "warning"
        sys.excepthook(*sys.exc_info())
    return msg, msg_type


def controller_edit(cfg, request):

    if request.method == "POST":
        form = dict([(k, v) for k, v in request.form.items() if v])
        update_server = form.pop("__update_server__") == "true"
        orig_name = form.pop("__original_name__")
        name = form.get("name", orig_name)
        result = dict(name=name)
        if name != orig_name:
            result["message"] = "Change of controller name not supported yet!"
            result["type"] = "danger"
            return flask.json.dumps(result)

        ctrl_cfg = cfg.get_config(orig_name)

        objs = set()
        for param_name, param_value in form.items():
            if " " in param_name:  # axis param
                param_name, axis_name = param_name.split()
                obj = cfg.get_config(axis_name)
                try:
                    param_value = __KNOWN_AXIS_PARAMS[param_name](param_value)
                except KeyError:
                    param_value = auto_coerce(param_value)
            else:  # controller param
                obj = ctrl_cfg
            obj[param_name] = param_value
            objs.add(obj)

        axes_server_results = {}
        for obj in objs:
            obj.save()
            if update_server and obj != ctrl_cfg:
                name = obj["name"]
                axes_server_results[name] = __tango_apply_config(name)

        msg_type = "success"
        if update_server:
            if ctrl_cfg in objs:
                msg_type = "warning"
                msg = (
                    "'%s' configuration saved! "
                    "TANGO server needs to be (re)started!" % name
                )
            else:
                msg = "'%s' configuration applied!" % name
                for axis_name, axis_result in axes_server_results:
                    msg += "<br/>" + axis_result["message"]
                    axis_msg_type = axis_result["type"]
                    if axis_msg_type != "success":
                        msg_type = axis_msg_type
        else:
            msg = "'%s' configuration applied!" % name
        result["message"] = msg
        result["type"] = msg_type
        return flask.json.dumps(result)


def axis_edit(cfg, request):

    if request.method == "POST":
        form = dict([(k, v) for k, v in request.form.items() if v])
        update_server = form.pop("__update_server__") == "true"
        orig_name = form.pop("__original_name__")
        name = form["name"]
        result = dict(name=name)

        if name != orig_name:
            result["message"] = "Change of axis name not supported yet!"
            result["type"] = "danger"
            return flask.json.dumps(result)

        axis_cfg = cfg.get_config(orig_name)

        for k, v in form.items():
            if k == "filename":
                continue
            try:
                v = __KNOWN_AXIS_PARAMS[k](v)
            except KeyError:
                v = auto_coerce(v)
            if k in axis_cfg:
                if axis_cfg[k] == v:
                    continue
            axis_cfg[k] = v
        axis_cfg.save()
        if update_server:
            result["message"], result["type"] = __tango_apply_config(name)
        else:
            result["message"] = "'%s' configuration saved!" % name
            result["type"] = "success"
        return flask.json.dumps(result)


__ACTIONS = {
    "add": [
        {
            "id": "emotion_add_controller",
            "label": "Add controller",
            "icon": "fa fa-gears",
            "action": "plugin/emotion/add_controller",
            "disabled": True,
        },
        {
            "id": "emotion_add_axis",
            "label": "Add axis",
            "icon": "fa fa-gears",
            "action": "plugin/emotion/add_axis",
            "disabled": True,
        },
    ]
}


def actions():
    return __ACTIONS


def add_controller(cfg, request):
    if request.method == "GET":
        return flask.json.dumps(
            dict(html="<h1>TODO</h1>", message="not implemented", type="danger")
        )


def add_axis(cfg, request):
    if request.method == "GET":
        return flask.json.dumps(
            dict(html="<h1>TODO</h1>", message="not implemented", type="danger")
        )
