# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import weakref
import tabulate
import numpy as np
import gevent

"""
SPEEDGOAT PARAMETERS
"""


class SpeedgoatHdwParameterController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self.params = SpeedgoatHdwParameters(self._speedgoat)
        self._params = None
        self._param_dict = None
        self._param_tree = None
        self._load()

    def _load(self, force=False):
        if self._params is None or force:
            self._params = self._speedgoat.get_param_infos()
            self._param_dict = self._speedgoat._create_block_dict(self._params)
            self._param_tree = self._speedgoat._create_tree(self._params)

            # Thomas: Try o have easy access to all parameters
            for param in self._params:
                if param["block"] == "" and param["name"][-1] != "_":
                    setattr(self.__class__, param["name"], SpeedgoatParam(self, param))

        return {
            "params": self._params,
            "param_dict": self._param_dict,
            "param_tree": self._param_tree,
        }

    def __info__(self):
        # Get Tunnable parameters
        parameters = [param for param in self._params if param["block"] == ""]
        # Display Tunnable parameters
        lines = []
        lines.append(["Parameters", "Values"])
        lines.append(["", ""])
        for param in parameters:
            if param["name"][-1] != "_":
                lines.append([param["name"], self.get(param["name"])])

        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def set(self, param, value):
        # Try both with and without /Value at the end
        try:
            self.params[param + "/Value"] = value
        except Exception:
            try:
                self.params[param] = value
            except Exception:
                raise NameError(f"Parameter {param} does not exist")

        # Verify that the value has been set
        if isinstance(value, np.ndarray):
            if len(value) == 1:
                while self.get(param) != value[0]:
                    gevent.sleep(0.00001)  # 10us
            else:
                while not np.array_equiv(
                    np.squeeze(self.get(param)), np.squeeze(value)
                ):
                    gevent.sleep(0.00001)  # 10us
        elif isinstance(value, list):
            if len(value) == 1:
                while self.get(param) != value[0]:
                    gevent.sleep(0.00001)  # 10us
            else:
                npvalue = np.asarray(value)
                while not np.array_equiv(self.get(param), npvalue):
                    gevent.sleep(0.00001)  # 10us
        elif isinstance(value, str):
            raise NotImplementedError
        else:
            while self.get(param) != value:
                gevent.sleep(0.00001)  # 10us

    def get(self, param):
        if param.split("/")[-1] == "String":
            val = self.params[param]
            rep = "".join([chr(i[0]) for i in val]).rstrip("\x00")
        else:
            # Try both with and without /Value at the end
            try:
                rep = self.params[param + "/Value"]
            except Exception:
                try:
                    rep = self.params[param]
                except Exception:
                    raise NameError(f"Parameter {param} does not exist")
        return rep

    @property
    def _tree(self):
        return self._load()["param_tree"]


class SpeedgoatParam:
    def __init__(self, param_ctl, counter_dic):
        self._param_ctl = param_ctl
        self._name = counter_dic["name"]

    def __get__(self, obj, objtype):
        return self._param_ctl.get(self._name)

    def __set__(self, obj, value):
        self._param_ctl.set(self._name, value)


class SpeedgoatHdwParameters(object):
    """
    Parameters dictionnary
    """

    def __init__(self, speedgoat):
        self._speedgoat = weakref.proxy(speedgoat)

    def __getitem__(self, name):
        if isinstance(name, str):
            block, name = name.rsplit("/", 1) if "/" in name else ("", name)
            return self._speedgoat.get_param_value_from_name(block, name)
        block_names = [bn.rsplit("/", 1) if "/" in bn else ("", bn) for bn in name]
        return self._speedgoat.get_param_value_from_names(*block_names)

    def __setitem__(self, name, value):
        if isinstance(name, str):
            if name.rfind("/") == -1:
                block = ""
                name = name
            else:
                block, name = name.rsplit("/", 1)
            self._speedgoat.set_param_value_from_name(block, name, value)
            return
        # TODO: set multiple items
        raise NotImplementedError

    def keys(self):
        result = []
        param_dict = self._speedgoat.parameter._load()["param_dict"]
        for block_name, data in param_dict.items():
            for key in data:
                result.append(block_name + "/" + key)
        return result
