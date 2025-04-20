# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import weakref

"""
SIGNALS
"""


class SpeedgoatHdwSignalController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self.signals = SpeedgoatHdwSignals(self._speedgoat)
        self._signals = None
        self._signal_dict = None
        self._signal_tree = None

    def __info__(self):
        self._tree.show()
        return ""

    def _load(self, force=False):
        if self._signals is None or force:
            self._signals = self._speedgoat.get_signal_infos()
            self._signal_dict = self._speedgoat._create_block_dict(self._signals)
            self._signal_tree = self._speedgoat._create_tree(self._signals)
        return {
            "signals": self._signals,
            "signal_dict": self._signal_dict,
            "signal_tree": self._signal_tree,
        }

    def get(self, signal_path):
        signal_nodes = self._tree.children(signal_path)
        if len(signal_nodes) > 0:
            signal = []
            for signal_node in signal_nodes:
                signal.append(self.signals[f"{signal_path}/{signal_node.tag}"])
        else:
            signal = self.signals[signal_path]
        return signal

    def get_signal_index(self, name):
        node = self._tree.get_node(name)
        return node.data["idx"]

    @property
    def _tree(self):
        return self._load()["signal_tree"]


class SpeedgoatHdwSignals(object):
    def __init__(self, speedgoat):
        self._speedgoat = weakref.proxy(speedgoat)

    def __getitem__(self, name):
        if isinstance(name, str):
            tree = self._speedgoat.signal._load()["signal_tree"]
            node = tree.get_node(name)
            idx = node.data["idx"]
            return self._speedgoat.get_signal_value_from_idxs([idx])[0]
            # block, name = name.rsplit("/", 1) if "/" in name else ("", name)
            # return self._speedgoat.get_signal_value_from_name(block, name)
        # block_names = [bn.rsplit("/", 1) if "/" in bn else ("", bn) for bn in name]
        # return self._speedgoat.get_signal_value_from_names(*block_names)

    def keys(self):
        result = []
        signal_dict = self._speedgoat.signal._load()["signal_dict"]
        for block_name, data in signal_dict.items():
            for key in data:
                result.append(block_name + "/" + key)
        return result
