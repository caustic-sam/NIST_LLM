# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import time
import enum
import logging
from contextlib import contextmanager

from bliss.common.event import dispatcher

_logger = logging.getLogger("bliss.scans.debugger")

SIGNAL_ACTION = "_acquisition_chain_debug_action_signal"
SIGNAL_NEXT_ITER = "_acquisition_chain_debug_next_iter_signal"

DEBUG_MODES = enum.Enum("DEBUG_MODES", "STD CHAIN STATS")
VALID_DEBUG_MODES = {
    "scan": DEBUG_MODES.STD,
    "scan_chain": DEBUG_MODES.CHAIN,
    "scan_stats": DEBUG_MODES.STATS,
}


@contextmanager
def chain_debug(action: str, actor: object):
    """
    action: function name
    actor: acquisition object
    """

    info = {
        "action": action,
        "status": "ENTER",
    }
    dispatcher.send(SIGNAL_ACTION, actor, info)
    now = time.perf_counter()
    yield
    elapsed = time.perf_counter() - now
    info.update(
        {
            "status": "EXIT",
            "elapsed": elapsed,
        }
    )
    dispatcher.send(SIGNAL_ACTION, actor, info)


class ScanDebugger:
    def __init__(self, scan, mode="scan") -> None:
        self.__scan = scan
        self.__mode = VALID_DEBUG_MODES[mode]
        self.__connected = False
        self.__start_time = None
        self.__stats = {}

    @property
    def connected(self):
        return self.__connected

    @property
    def scan(self):
        return self.__scan

    @property
    def chain(self):
        return self.__scan.acq_chain

    @property
    def mode(self):
        return self.__mode

    @property
    def stats(self):
        return self.__stats

    def stats_show(self):
        print(self._build_stats_info())

    def connect(self):
        if self.__connected:
            raise RuntimeError("already connected")
        self._connect()

    def disconnect(self):
        if self.__connected:
            self._disconnect()

        if self.mode in [DEBUG_MODES.STATS, DEBUG_MODES.CHAIN]:
            _logger.info(self._build_stats_info())
        elif self.mode == DEBUG_MODES.STD:
            _logger.debug(self._build_stats_info())

        self.__scan = None
        self.__stats.clear()

    def _connect(self):
        dispatcher.connect(self._on_signal_next_iter, SIGNAL_NEXT_ITER, self.chain)
        for acq in self.chain.nodes_list:
            dispatcher.connect(self._on_signal_action, SIGNAL_ACTION, acq)
        self.__connected = True

    def _disconnect(self):
        dispatcher.disconnect(self._on_signal_next_iter, SIGNAL_NEXT_ITER, self.chain)
        for acq in self.chain.nodes_list:
            dispatcher.disconnect(self._on_signal_action, SIGNAL_ACTION, acq)
        self.__connected = False

    def _prefix_msg(self, msg):
        lines = []
        for line in msg.split("\n"):
            lines.append(f"SCAN {self.scan.scan_number}:  {line}")
        return "\n".join(lines)

    def _get_formatted_table(self, *args, **kwargs):
        from bliss.shell.formatters.table import IncrementalTable

        return IncrementalTable(*args, **kwargs)

    def _build_chain_info(self, chain):
        header = f"\n{str(chain.tree)}"

        tab = self._get_formatted_table(
            [["name", "type", "npoints", "prepare_once", "start_once"]],
            col_sep=" ",
            flag="",
            lmargin="",
        )

        for acqobj in chain.nodes_list:
            tab.add_line(
                [
                    acqobj.name,
                    acqobj.__class__.__name__,
                    acqobj.npoints,
                    str(acqobj.prepare_once),
                    str(acqobj.start_once),
                ]
            )
        tab.resize(10, 40)
        tab.add_separator("-", line_index=1)

        return f"{header}\n{str(tab)}\n"

    def _build_stats_info(self):
        info = [f"Statistics of {self.scan.name} #{self.scan.scan_number}"]
        for acqobj, action_dict in self.__stats.items():
            tab = self._get_formatted_table(
                [
                    [
                        "action",
                        "calls",
                        "sum [s]",
                        "average [s]",
                        "mini [s]",
                        "maxi [s]",
                    ]
                ],
                col_sep=" ",
                flag="",
                lmargin=" ",
                align="<",
                fpreci=".5",
                dtype="f",
            )
            for action, stats_dict in action_dict.items():
                tab.add_line(
                    [
                        action,
                        stats_dict["calls"],
                        stats_dict["sum"],
                        stats_dict["average"],
                        stats_dict["mini"],
                        stats_dict["maxi"],
                    ]
                )

            tab.set_column_params(1, {"dtype": "g", "align": "^"})
            tab.resize(10, 20)
            # tab.add_separator("-", line_index=1)

            tab.full_width
            header = f" {acqobj.name}: {acqobj.__class__.__name__} "
            fac = max(int((tab.full_width + len(tab.lmargin) - len(header)) / 2), 3)
            info.append(f"\n{'='*fac}{header}{'='*fac}\n{str(tab)}\n")

        return "".join(info)

    def _on_signal_next_iter(self, iter_index=None, signal=None, sender=None):
        """callback on SIGNAL_NEXT_ITER event, sender is an AcquisitionChain object"""

        if self.mode == DEBUG_MODES.CHAIN:
            if iter_index == 0:
                self.__start_time = time.perf_counter()
                msg = f"\n====== ACQUISITION CHAIN ======\n{self._build_chain_info(sender)}\n"
                _logger.info(self._prefix_msg(msg))

            msg = f"=====> SCAN ITERATION {iter_index}"
            _logger.info(self._prefix_msg(msg))

    def _on_signal_action(self, event_dict=None, signal=None, sender=None):
        """callback on SIGNAL_ACTION event, sender is an AcquisitionObject object"""

        status = event_dict["status"]
        action = event_dict["action"]

        if self.mode == DEBUG_MODES.CHAIN:
            msg = f"{action:18s} {sender.name[-40:]:40s} {status:6s}"
            if status == "ENTER":
                timestamp = f"{time.perf_counter() - self.__start_time:.4f}"
                msg += f"@ {timestamp:10s} {'':28s}"
            elif status == "EXIT":
                elapsed = event_dict["elapsed"]
                timestamp = f"{time.perf_counter() - self.__start_time:.4f}"
                elapsed = f"{elapsed:.4f}"
                msg += f"@ {timestamp:10s} elapsed {elapsed:20s}"
            _logger.info(self._prefix_msg(msg))

        if status == "EXIT":
            elapsed = event_dict["elapsed"]

            acqobj_stats = self.__stats.setdefault(sender, {})
            action_stats = acqobj_stats.setdefault(action, {})

            calls = action_stats.setdefault("calls", 0)
            action_stats["calls"] = calls + 1

            tsum = action_stats.setdefault("sum", 0)
            action_stats["sum"] = tsum + elapsed

            action_stats["average"] = action_stats["sum"] / action_stats["calls"]

            mini = action_stats.setdefault("mini", None)
            if mini is None:
                action_stats["mini"] = elapsed
            else:
                action_stats["mini"] = min(mini, elapsed)

            maxi = action_stats.setdefault("maxi", None)
            if maxi is None:
                action_stats["maxi"] = elapsed
            else:
                action_stats["maxi"] = max(maxi, elapsed)
