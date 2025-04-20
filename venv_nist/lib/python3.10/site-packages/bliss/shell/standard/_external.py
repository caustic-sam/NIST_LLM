# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Standard functions provided to the BLISS shell.
"""

from __future__ import annotations
import typing

import gevent
import os
import socket
import subprocess
import sys
from bliss.common.scans import ct
from bliss.common.event import connect
from bliss.common.utils import typecheck
from bliss.config.conductor.client import get_redis_proxy
from bliss.comm.rpc import Client
from blisswriter.writer import scan_url_info
from bliss import current_session
from bliss.scanning.scan import Scan


def tw(*motors):
    """
    Display an user interface to move selected motors. (Limited to 5 motors)

    Args:
        motors (~bliss.common.axis.Axis): motor axis

    example:
      DEMO [18]: tw(m0, m1, m2)
    """

    def get_url(timeout=None):
        key = "tweak_ui_" + current_session.name
        redis = get_redis_proxy()

        if timeout is None:
            value = redis.lpop(key)
        else:
            result = redis.blpop(key, timeout=timeout)
            if result is not None:
                key, value = result
                redis.lpush(key, value)
            else:
                value = None

        if value is None:
            raise ValueError(
                "Tweak UI: cannot retrieve Tweak RPC server address from pid "
            )
        url = value.decode().split()[-1]
        return url

    def wait_tweak(tweak):
        while True:
            try:
                tweak.loaded
                break
            except socket.error:
                pass
            gevent.sleep(0.3)

    def create_env():
        from bliss.config.conductor.client import get_default_connection

        beacon = get_default_connection()
        beacon_config = f"{beacon._host}:{beacon._port}"

        env = dict(os.environ)
        env["BEACON_HOST"] = beacon_config
        return env

    if len(motors) > 5:
        raise TypeError("This tool can only display a maximum of 5 motors")

    try:
        with gevent.Timeout(10):
            tweak: Client | None
            try:
                url = get_url()
            except ValueError:
                pass
            else:
                tweak = Client(url)
                try:
                    tweak.close_new = True
                except socket.error:
                    pass

            tweak = None
            args = f"{sys.executable} -m bliss.shell.qtapp.tweak_ui --session {current_session.name} --motors".split()
            for motor in motors:
                args.append(motor.name)

            process = subprocess.Popen(args, env=create_env())

            try:
                url = get_url(timeout=10)
                tweak = Client(url)
                wait_tweak(tweak)
                connect(tweak, "ct_requested", _tw_ct_requested)
                print("Tweak UI started")
            except Exception:
                process.kill()
                print("Tweak UI launch has failed, please try again")

    except gevent.Timeout:
        process.kill()
        raise TimeoutError("The application took too long to start")


def _tw_ct_requested(acq_time, sender):
    ct(acq_time, title="auto_ct")


# Silx


@typecheck
def silx_view(scan: typing.Union[Scan, int, None] = None):
    """
    Open silx view on a given scan. When no scan is given it
    opens the current data file.
    """
    uris = None
    if scan is None:
        uris = [current_session.scan_saving.filename]
    elif isinstance(scan, int):
        try:
            scan_obj = current_session.scans[scan]
        except IndexError:
            pass
        else:
            uris = scan_url_info.scan_urls(scan_obj.scan_info)
    else:
        uris = scan_url_info.scan_urls(scan.scan_info)
    _launch_silx(uris)


def _launch_silx(uris: typing.Union[list[str], None] = None):
    args = f"{sys.executable} -m silx.app.view.main".split()
    if uris:
        args.extend(uris)
    return subprocess.Popen(args, start_new_session=True)


# PyMCA


@typecheck
def pymca(scan: typing.Union[Scan, None] = None):
    """
    Open PyMCA on a given scan (default last scan)
    """

    filename = None
    try:
        if scan is None:
            scan = current_session.scans[-1]
        filename = scan._scan_info["filename"]
    except IndexError:
        pass
    _launch_pymca(filename)


def _launch_pymca(filename: typing.Union[str, None] = None):
    args = f"{sys.executable} -m PyMca5.PyMcaGui.pymca.PyMcaMain".split()
    if filename:
        args.append(filename)
    return subprocess.Popen(args, start_new_session=True)
