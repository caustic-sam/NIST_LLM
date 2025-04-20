# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import gevent
import atexit

from collections.abc import MutableMapping
from bliss.scanning.scan import MonitoringScan, ScanAbort
from bliss.scanning.scan_info import ScanInfo
from bliss.common.scans import DEFAULT_CHAIN
from bliss.scanning.toolbox import DefaultAcquisitionChain

from bliss.common.types import _int, _float, _countables
from bliss.common.utils import typecheck


class MScan:
    def __init__(
        self,
        name,
        count_time,
        *counters,
        sleep_time=1.0,
        npoints=0,
        save=False,
        use_default_chain=True,
        table=False,
    ):
        self._name = name
        self._count_time = count_time
        self._counters = counters
        self._sleep_time = sleep_time
        self._npoints = npoints
        self._save = save
        self._use_default_chain = use_default_chain
        self._use_table = table

        self._scan_type = "monitoring"
        self._unique_id = None
        self._task = None
        self._scan = None

    @property
    def name(self):
        return self._name

    @property
    def count_time(self):
        return self._count_time

    @property
    def counters(self):
        return self._counters

    @property
    def sleep_time(self):
        return self._sleep_time

    @property
    def npoints(self):
        return self._npoints

    @property
    def saving_is_enabled(self):
        return self._save

    @property
    def use_default_chain(self):
        return self._use_default_chain

    @property
    def scan(self):
        return self._scan

    def start(self):
        if not self._task:
            self._scan = self._build_scan()
            self._task = gevent.spawn(self._scan.run)
            self._task.name = f"moniscan_{self.name}"

    def stop(self):
        if self._task:
            self._task.kill(ScanAbort)

    # def get_scan_devices(self, scan_name):
    #     acqObjs = self.get_scan().acq_chain.nodes_list
    #     devices = [ ao.device for ao in acqObjs if ao.device is not None]
    #     return devices

    def _build_scan(self):

        scan_info = ScanInfo.normalize(
            {
                "type": self._scan_type,
                "save": self._save,
                "sleep_time": self._sleep_time,
            }
        )

        npoints = "INF" if self._npoints == 0 else self._npoints
        title = (
            f"monitoring:{self.name} (cout_time={self.count_time}, npoints={npoints})"
        )

        scan_info.update(
            {"npoints": self._npoints, "count_time": self._count_time, "title": title}
        )

        scan_params = {
            "npoints": self._npoints,
            "count_time": self._count_time,
            "type": self._scan_type,
            "sleep_time": self._sleep_time,
        }

        if self._use_default_chain:
            chain = DEFAULT_CHAIN.get(scan_params, self._counters)
        else:
            # use another default acquisition chain without current session chain settings
            chain = DefaultAcquisitionChain().get(scan_params, self._counters)

        # Specify a default plot
        time_channel = chain.timer.channels[1]  # epoch
        if self._use_table:
            scan_info.add_table_plot(name=self.name)
        else:
            scan_info.add_curve_plot(name=self.name, x=time_channel.fullname)

        scan = MonitoringScan(
            chain,
            scan_info=scan_info,
            name=self._name,
            save=self._save,
            save_images=False,
        )

        return scan


class MScanManager(MutableMapping):
    def __init__(self):
        self.__registered_scans = {}

    def __getitem__(self, key):
        return self.__registered_scans[key]

    def __setitem__(self, key, value):
        if not isinstance(value, MScan):
            raise ValueError(
                f"Expecting a 'MonitoringScan' instance but received {value} instead"
            )

        # stop registered scan before overwriting
        if key in self:
            self.__registered_scans[key].stop()

        self.__registered_scans[key] = value

    def __delitem__(self, key):
        # stop registered scan before cleaning
        self.__registered_scans[key].stop()

        del self.__registered_scans[key]

    def __iter__(self):
        return iter(self.__registered_scans)

    def __len__(self):
        return len(self.__registered_scans)


MONITORING_SCANS = MScanManager()
atexit.register(MONITORING_SCANS.clear)


@typecheck
def start_monitoring(
    name: str,
    count_time: _float,
    *counters: _countables,
    sleep_time: _float = 1.0,
    npoints: _int = 0,
    save: bool = False,
    use_default_chain: bool = True,
    table: bool = False,
):
    """Launch a monitoring scan task in the background

    Args:
        name: monitoring scan name
        count_time: measurement time
        *counters: counters, measurement group or counters container
        sleep_time: sleeping time between two measurements (default = 0)
        npoints: number of measurements (default = 0, i.e never ending)
        save: enable data archiving (default = False)
        use_default_chain: enable usage of the same default chain as all step-by-step scans
        table: enable data display as a table instead of a curve

    """

    mscan = MScan(
        name,
        count_time,
        *counters,
        sleep_time=sleep_time,
        npoints=npoints,
        save=save,
        use_default_chain=use_default_chain,
        table=table,
    )
    MONITORING_SCANS[name] = mscan
    mscan.start()


@typecheck
def stop_monitoring(name: str):
    """Stop a monitoring scan.

    Args:
        name: name of the monitoring scan
    """
    MONITORING_SCANS[name].stop()


def stop_all_monitoring():
    """Stop all running monitoring scans."""
    for mscan in MONITORING_SCANS.values():
        mscan.stop()


def start_all_monitoring():
    """Re-start all stopped monitoring scans."""
    for mscan in MONITORING_SCANS.values():
        mscan.start()


def clean_monitoring():
    """Stop and delete all monitoring scans."""
    MONITORING_SCANS.clear()
