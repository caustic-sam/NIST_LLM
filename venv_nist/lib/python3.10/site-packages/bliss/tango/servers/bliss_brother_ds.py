#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Bliss Brother python tango device server

BlissBrother is a PyTango device server designed to ease long time archiving of
Bliss settings like motor positions.

It reads settings of user interest in Redis database and exposes these values as
tango attributes.

cf: https://gitlab.esrf.fr/bliss/bliss/-/issues/3580

"""

import os
import signal
import shutil
import sys
import time
import tabulate

import redis
import redis.client
import numpy as np

import tango
from tango import DevState, Attr
from tango import AttrWriteType
from tango.server import Device, DeviceMeta, GreenMode, run
from tango.server import device_property

from bliss.common.utils import chunk_col

# * [x] TODO : axes blacklist
#
# * [x] TODO : use push_archive_event()  (why exactly ?)
#              https://pytango.readthedocs.io/en/stable/server_api/server.html
#
# * JM: si on poush un attr non declare' dans HDB -> /dev/null (no subscriber non consumer)
#       pas d'effet sur HDB
#
# * [ ] improve redis reading (cf MG)
#
# * [x] read backlist from beacon ? (no. not really importatn: users want all axes...)
#
# * [x] policy parametrization.
#     - [x] delta_time_still_motor
#     - [x] still_time_before_saving
#     - [x] redis_query_interval
#     - [ ] rtol in : _pos_has_changed = not np.isclose(_pos, _LCV, rtol=0.01)
#
# * [ ] log / debug ?
#
# * [ ] check TAC
#
# * [x] parametrization
#      - [x] subscriber
#      - [x] config server
#      - [x] pos_attr_full_url
#
# * [ ] Q: dial as spectrum attribute (or offset) ?
#       + reduce attributes number (-> ease selection in jhdbviewer)
#       - store the dial (or offset) attribute more often
#       - less easy to retreive
#
# * Calc axes are taken into account.
#
# * [ ] Q: dead-band per axis ?


def timestamp():
    """
    Timestamp string with blank space (for CSV saving: excel safe string)
    """
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class BlissBrother(Device):
    """
    Tango device server main class.
    """

    __classmeta__ = DeviceMeta  # ?

    # `blacklist' Property: string: list of axes separated by comas to not monitor.
    # ex: "sampy, sampz, zoom"
    # OR: path of a file containing blacklisted axes.
    blacklist = device_property(dtype=str)

    # `delta_time_still_motor' Property: int: time interval (in seconds) to store motion-less motors.
    # ex: 50000 (~13h)
    delta_time_still_motor = device_property(dtype=int)

    # `still_time_before_saving': int: time (in seconds) to wait to
    # store motor position if motor has moved.
    # ex: 10
    still_time_before_saving = device_property(dtype=int)

    # `redis_query_interval': int: number of seconds between 2 queries to Redis.
    redis_query_interval = device_property(dtype=int)

    # this ppt has changed and must not exist anymore
    stab_time = device_property(dtype=int)

    # `configurator_url`: str: url of the configurator server
    # ex:  "tango://acs.esrf.fr:10000/sys/hdb++config/id42"
    # If None: will be taken from tango db
    configurator_url = device_property(dtype=str)

    # `subscriber_url`: str: url of the subscriber (archiver) server
    # ex:  "tango://acs.esrf.fr:10000/sys/hdb-es/id42"
    # If None: will be taken from tango db
    subscriber_url = device_property(dtype=str)

    def __init__(self, *args, **kwargs):
        """ """
        self.redis = None
        self._status = "NOT YET STARTED"

        self.start_time = 0.0
        self.blacklisted_axes = []
        self.configurator_server = None
        self.subscriber_server = None
        self.ctrl_system_name = None

        self.last_redis_query_time = time.time()  # timestamp of last Redis query.
        self.axes_names_list = []
        self.axes_values = {}  # dict of current axis names / values

        super().__init__(*args, **kwargs)

    def dev_status(self):
        """
        NB: If defined, always_executed_hook() is called before each call to
            dev_status() (and other methods).

        Called periodically by tango polling.
        """
        self.redis_read_all_values()

        # Check configurator server.
        self.configurator_server = tango.DeviceProxy(self.configurator_url)
        if self.configurator_server.import_info().exported:
            try:
                self.configurator_server.ping()
                _msg = f"configurator_server: {self.configurator_server.dev_name()} OK"
                print(_msg)
                self._status += "\n" + _msg
                self.set_state(DevState.ON)
            except tango.DevFailed:  # as tg_err:
                _msg = f"Cannot connect to configurator server: {self.configurator_url}"
                print(_msg)
                self._status += "\n" + _msg
                self.configurator_server = None
                # raise or continue ????
                # raise RuntimeError(f"Cannot connect to configurator server:{self.configurator_url}") from tg_err
        else:
            print(f"configurator server: {self.configurator_url} not exported")
            self._status += (
                "\n" + f"configurator server: {self.configurator_url} not exported"
            )

        # Check subscriber server.
        self.subscriber_server = tango.DeviceProxy(self.subscriber_url)
        if self.subscriber_server.import_info().exported:
            try:
                self.subscriber_server.ping()
                _msg = f"subscriber_server: {self.subscriber_server.dev_name()} OK"
                print(_msg)
                self._status += "\n" + _msg
                self.set_state(DevState.ON)
            except tango.DevFailed:  # as tg_err:
                _msg = f"Cannot connect to subscriber server: {self.subscriber_url}"
                print(_msg)
                self._status += "\n" + _msg
                self.subscriber_server = None
                self.set_state(DevState.FAULT)
                # raise or continue ????
                # raise RuntimeError(f"Cannot connect to subscriber server:{self.subscriber_url}") from tg_err
        else:
            _msg = f"subscriber server: {self.subscriber_url} not exported"
            print(_msg)
            self._status += "\n" + _msg
            self.set_state(DevState.FAULT)

        return self._status

    def delete_device(self):
        """
        Called on ctrl-c
        """
        print("delete_device()")

    def signal_handler(self, signo):
        """
        Method called on signal catched.
        """
        if signo == 2:
            print(f"\nCtrl-c pressed (signal={signo})", flush=True)
        elif signo == 15:
            print(f"process killed (signal={signo})", flush=True)
        else:
            print(f"signal {signo} received", flush=True)

    def init_device(self):
        """
        Called by super().__init__(...) in __init__()
        Starting here, we can read properties.
        """
        Device.init_device(self)

        if self.stab_time is not None:
            raise RuntimeError(
                "stab_time property has changed and must not exist anymore -> use: still_time_before_saving"
            )

        try:
            print(f"Delta time still motor: {self.delta_time_still_motor}")
            print(f"Still time before saving: {self.still_time_before_saving}")

            tdb = tango.Database()

            # `configurator_url`: str: url of the configurator server
            # ex:  "tango://acs:10000/sys/hdb++config/id42"
            if self.configurator_url is None:
                self.configurator_url = tdb.get_property(
                    "HDB++", "HdbConfiguratorName"
                )["HdbConfiguratorName"][0]
                print("Use configurator_url found in tango database")
            else:
                print("Use configurator_url found in Bliss_brother tango config")

            # `subscriber_url`: str: url of the subscriber (archiver) server
            # ex:  "tango://acs.esrf.fr:10000/sys/hdb-es/id42"
            if self.subscriber_url is None:
                self.subscriber_url = tdb.get_property("HDB++", "HdbSubscriberName")[
                    "HdbSubscriberName"
                ][0]
                print("Use   subscriber_url found in tango database")
            else:
                print("Use   subscriber_url found in Bliss_brother tango config")

            # print(f"configurator_url = {self.configurator_url}")
            # print(f"  subscriber_url = {self.subscriber_url}")

            # Tango proxy to HdbConfiguratorServer using 'configurator_url' DS property.
            self.configurator_server = tango.DeviceProxy(self.configurator_url)

            try:
                self.configurator_server.ping()
                print(f"configurator_server= {self.configurator_server.dev_name()}")
            except tango.DevFailed:  # as tg_err:
                print(f"Cannot connect to configurator server: {self.configurator_url}")
                self.configurator_server = None
                # raise or continue ????
                # raise RuntimeError(f"Cannot connect to configurator server:{self.configurator_url}") from tg_err

            try:
                self.ctrl_system_name = tdb.get_property("CtrlSystem", "Name")["Name"][
                    0
                ]
            except KeyError as k_err:
                raise RuntimeError(
                    "Cannot found 'Name' property in 'CtrlSystem' FreeProperty in tango database"
                ) from k_err

            if self.blacklist is not None:
                # test if self.blacklist is a file.
                if os.path.isfile(self.blacklist):
                    print(f"file {self.blacklist} exists")
                    with open(self.blacklist, encoding="ascii") as bl_file:
                        bl_axes = bl_file.read()
                else:
                    bl_axes = self.blacklist
                self.blacklisted_axes = [name.strip() for name in bl_axes.split(",")]

            print("Blacklisted axes:", self.blacklisted_axes)

            self.redis_init()

            # Tango attributes creation and configuration.
            self.create_attributes()

            self.redis_read_all_values()

            self.set_state(DevState.ON)

            # Signal (for ctrl-c and others)
            super().register_signal(signal.SIGTERM)
            super().register_signal(signal.SIGINT)
        except Exception as exc:
            raise RuntimeError("ERROR initializing device") from exc

        print(f"BlissBrother {self.get_name()} initialized")

    def redis_init(self):
        """
        Open a connection to Redis database.
        Read all axis keys.
        """
        _sock = "/tmp/redis.sock"
        print("Connection to redis socket : ", _sock)
        self.redis = redis.Redis(unix_socket_path=_sock)

        self.start_time = time.perf_counter()
        axis_filter_str = "axis*"
        redis_keys = [
            x.decode().split(".")[1] for x in self.redis.keys(axis_filter_str)
        ]

        self.axes_names_list = [
            name for name in redis_keys if name not in self.blacklisted_axes
        ]

        self.axes_names_list.sort()
        _list_duration = time.perf_counter() - self.start_time

        print(
            f"list redis {len(self.axes_names_list)} axes took: {_list_duration*1000:2.3g} ms"
        )

        self.redis_print_axes_list()

    def redis_print_axes_list(self):
        """
        Print tabulated list of axes.
        """
        print("\nAxes found in redis database and not blacklisted:")

        display_width = shutil.get_terminal_size().columns
        max_length = max(len(x) for x in self.axes_names_list)

        # Number of items displayable on one line.
        item_number = int(display_width / max_length) + 1

        print(
            tabulate.tabulate(
                chunk_col(self.axes_names_list, item_number), tablefmt="plain"
            )
        )
        print("")

    def redis_get_axis_params(self, axis_name):
        """
        Return position and dial read in redis for axis named <axis_name>.
        """
        ans = self.redis.hgetall(f"axis.{axis_name}")
        if ans == {}:
            # raise ValueError(f"Axis '{axis_name}' not found")
            print(f"Axis '{axis_name}' not found")

        # print(f"{axis_name} -> ans=", ans)

        try:
            position = float(ans[b"position"].decode())
        except (KeyError, ValueError):
            position = 666.666

        try:
            dial = float(ans[b"dial_position"].decode())
        except (KeyError, ValueError):
            dial = 666.666

        return (position, dial)

    def redis_read_all_values(self):
        """
        Fill self.axes_values dict with for each axis:
        - position
        - dial
        - must save at (msa)
        - last changed pos value (lcv)
        """

        _t0 = time.perf_counter()
        print(
            f"----- time = {int(time.time())}s   {timestamp()}  -------------------------------------------"
        )
        print(
            "    axis   pos          dial         prev.pos  prev.dial   MustSaveAt  LastChangedValue"
        )
        #       XX  sampz  pos=48.9803 dial=51.0197  48.9803, 51.0197, _msa=1678446783(in 3560s), _lcv=48.9803

        for axis_name in self.axes_names_list:
            _time = time.time()
            _pos, _dial = self.redis_get_axis_params(axis_name)

            _attr_name = f"axis_{axis_name}_position"
            try:
                # previous position , previous dial ,  MustSaveAt,  last changed value
                _prev_pos, _prev_dial, _msa, _lcv = self.axes_values[axis_name]

                print(
                    #     axis         pos          dial             prev.pos       prev.dial
                    f"XX  {axis_name}  pos={_pos:g} dial={_dial:g}  {_prev_pos:g}, {_prev_dial:g},"
                    #   MustSaveAt                                    LastChangedValue"
                    f" _msa={int(_msa)}(in {int(_msa-time.time())}s), _lcv={_lcv:g}"
                )

                _pos_has_changed = not np.isclose(_pos, _lcv, rtol=0.01)
                if _pos_has_changed:
                    # Must save in 'still_time_before_saving' seconds.
                    print(_time, self.still_time_before_saving, _msa)
                    _msa = _time + self.still_time_before_saving

                    #                     prev pos, prev dial ,  MustSaveAt,  last changed value
                    self.axes_values[axis_name] = [_pos, _dial, _msa, _pos]

                _has_to_save = _time > _msa

                print(
                    f"_pos_has_changed={_pos_has_changed}   _has_to_save={_has_to_save} "
                )

                if _has_to_save:
                    print(f"push axis_{axis_name}")

                    # ??? booth needed ?
                    self.push_change_event(_attr_name, _pos)
                    self.push_archive_event(_attr_name, _pos)

                    # Flag to re-save in "delta_time_still_motor" seconds (value defined as property)
                    #                     prev pos, prev dial ,  MustSaveAt,  last changed value
                    self.axes_values[axis_name] = [
                        _pos,
                        _dial,
                        _time + self.delta_time_still_motor,
                        _pos,
                    ]

            except KeyError:
                print(
                    f" First reading and push of  {axis_name}  pos={_pos:g} dial={_dial:g} "
                )
                self.push_change_event(_attr_name, _pos)
                self.push_archive_event(_attr_name, _pos)

                #                     prev pos, prev dial ,  MustSaveAt,  last changed value
                self.axes_values[axis_name] = [
                    _pos,
                    _dial,
                    _time + self.delta_time_still_motor,
                    _pos,
                ]

        _duration = 1000 * (time.perf_counter() - _t0)
        self._status = (
            f" {timestamp()}  redis_read_all_values() duration: {_duration:g} ms"
            + f"  ({len(self.axes_names_list)} axes)"
        )

    def create_attributes(self):
        """
        Create tango attributes:
          axis_<name>_position
        and
          axis_<name>_dial
        from axes list.
        """

        added_attibutes_list = []

        for axis_name in self.axes_names_list:
            # Q: Enough to prevent collision with existing object name ?
            attr_pos_name = "axis_" + axis_name + "_position"
            attr_dial_name = "axis_" + axis_name + "_dial"
            print(f"Attribute {attr_pos_name:25s}", end="")

            self.add_attribute(
                Attr(attr_pos_name, tango.DevDouble, AttrWriteType.READ),
                r_meth=self.attr_read_pos_meth,
            )

            self.add_attribute(
                Attr(attr_dial_name, tango.DevDouble, AttrWriteType.READ),
                r_meth=self.attr_read_dial_meth,
            )

            # Configure Attributes
            #
            # Set an implemented flag for the attribute to indicate
            # that the server fires archive events manually, without
            # the polling to be started.
            # https://pytango.readthedocs.io/en/stable/server_api/server.html
            #                     (    attr_name, implemented, detect)
            self.set_archive_event(attr_pos_name, True, False)

            # Set an implemented flag for the attribute to indicate that the server
            # fires change events manually, without the polling to be started.
            #                    (    attr_name, implemented, detect)
            self.set_change_event(attr_pos_name, True, False)

            bl_name = self.ctrl_system_name.lower()
            dev_name = self.get_name()
            server_full_name = f"tango://{bl_name}.esrf.fr:20000/{dev_name}"
            pos_attr_full_url = f"{server_full_name}/{attr_pos_name}"

            # print("++++++++++++++++++++++++++++++++++++++++++++++++++++")
            # print("server_full_name =", server_full_name)
            # print("                   tango://id42.esrf.fr:20000/id42/blissbrother/1")
            # print("++++++++++++++++++++++++++++++++++++++++++++++++++++")

            self.subscriber_server = tango.DeviceProxy(self.subscriber_url)
            try:
                self.subscriber_server.ping()
                print("subscriber_server ping ok")
            except tango.DevFailed:  # as tg_err:
                print(f"Cannot connect to subscriber server: {self.subscriber_url}")
                self.subscriber_server = None
                # raise or continue ????
                # raise RuntimeError(f"Cannot connect to subscriber server:{self.subscriber_url}") from tg_err

            if self.configurator_server is None:
                print(
                    "WARNING : cannot access configurator server => attribute might not exist in HDB++"
                )
            else:
                if self.configurator_server.AttributeIsManaged(pos_attr_full_url):
                    # Ensure Attribute is declared in HDB++.
                    print(f"{attr_pos_name:25s} created, monitored in HDB++")
                else:
                    # If not, add it.
                    print(f"{attr_pos_name:25s} created, NOT monitored in HDB++")
                    print(f"-> adding {attr_pos_name} to HDB++")

                    _ans = self.configurator_server.AddAttributes(
                        [self.subscriber_url, pos_attr_full_url, "True"]
                    )

                    # Store results of AddAttributes (int) to test them at end of init.
                    added_attibutes_list.append(_ans)
                    print("AAL=", added_attibutes_list)

        for attr_added_id in added_attibutes_list:
            print(f"Waiting {attr_added_id}")
            _ans = self.configurator_server.AddingStatus(attr_added_id)
            while _ans != "No Error":
                print("Status=", _ans)
                time.sleep(1)
                _ans = self.configurator_server.AddingStatus(attr_added_id)

        print(f"{len(self.axes_names_list)} Attributes created")

    def attr_read_pos_meth(self, attrib):
        """
        Generic method to read POSITION value of attribute <attrib> from self.attr_vals dict.
        """
        # print("Attribute : ", attrib)

        attr_name = attrib.get_name()
        axis_name = "_".join(attr_name.split("_")[1:-1])

        # prev pos, prev dial ,  MustSaveAt,  last changed value
        _prev_pos, _prev_dial, _msa, _lcv = self.axes_values[axis_name]

        # print(f"{axis_name} : pos={_prev_pos:g}, dial={_prev_dial:g}")
        attrib.set_value(_prev_pos)

    def attr_read_dial_meth(self, attrib):
        """
        Generic method to read DIAL value of attribute <attrib> from self.attr_vals dict.
        """
        # print("Attribute : ", attrib)

        attr_name = attrib.get_name()
        axis_name = "_".join(attr_name.split("_")[1:-1])

        # prev pos, prev dial ,  MustSaveAt,  last changed value
        _prev_pos, _prev_dial, _msa, _lcv = self.axes_values[axis_name]

        # print(f"{axis_name} : pos={_prev_pos:g}, dial={_prev_dial:g}")
        attrib.set_value(_prev_dial)


def main():
    """
    Start server...
    """
    run([BlissBrother], green_mode=GreenMode.Gevent)
    print("END OF BlissBrother RUN", flush=True)


if __name__ == "__main__":
    sys.argv[0] = "BlissBrother"
    main()
