#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


"""
Functions to display time durations in a human readable manner.

NB : ISO 8601 duration : https://en.wikipedia.org/wiki/ISO_8601#Durations
                         https://fr.wikipedia.org/wiki/ISO_8601#Dur.C3.A9e
"""

from __future__ import annotations


def duration_split(duration: float) -> tuple[int, int, int, int, int, int]:
    """
    Return a duration split in human readable periods.

    Argument:
        duration: A duration given in seconds

    Returns:
        A tuple `(days, hours, minutes, seconds, miliseconds, microseconds)`
        coresponding to the `duration`.
    """

    us_in_ms = 1000 * duration - int(1000 * duration)
    nb_us = us_in_ms * 1000
    us = us_in_ms / 1000.0

    duration = duration - us

    ms = duration - int(duration)
    nb_ms = ms * 1000

    duration = duration - ms
    # now duration must be an integer number of seconds.

    nb_minutes, nb_seconds = divmod(duration, 60)
    nb_hours, nb_minutes = divmod(nb_minutes, 60)
    nb_days, nb_hours = divmod(nb_hours, 24)

    return (
        int(nb_days),
        int(nb_hours),
        int(nb_minutes),
        int(nb_seconds),
        int(nb_ms),
        int(nb_us),
    )


def duration_format(duration: float) -> str:
    """Return a formated string corresponding to `duration`.

    Argument:
        duration: A duration given in seconds.

    Returns:
        A formated string in 'us' 'ms' 's' 'hours'.
    """

    (nb_days, nb_hours, nb_minutes, nb_seconds, nb_ms, nb_us) = duration_split(duration)

    duration_str = ""

    # micro seconds
    if nb_us != 0:
        duration_str = "%dμs" % nb_us + duration_str

    # mili seconds
    if nb_ms > 0:
        if len(duration_str) > 1:
            duration_str = "%dms " % nb_ms + duration_str
        else:
            duration_str = "%dms" % nb_ms + duration_str

    # seconds
    if nb_seconds > 0:
        if len(duration_str) > 1:
            duration_str = "%ds " % nb_seconds + duration_str
        else:
            duration_str = "%ds" % nb_seconds + duration_str

    # minutes
    if nb_minutes > 0:
        if len(duration_str) > 1:
            duration_str = "%dmn " % nb_minutes + duration_str
        else:
            duration_str = "%dmn" % nb_minutes + duration_str

    # hours
    if nb_hours > 0:
        if len(duration_str) > 1:
            duration_str = "%dh " % nb_hours + duration_str
        else:
            duration_str = "%dh" % nb_hours + duration_str

    # day(s)
    if nb_days > 1:
        if len(duration_str) > 1:
            duration_str = "%ddays " % nb_days + duration_str
        else:
            duration_str = "%ddays" % nb_days + duration_str
    elif nb_days > 0:
        if len(duration_str) > 1:
            duration_str = "%dday " % nb_days + duration_str
        else:
            duration_str = "%dday" % nb_days + duration_str

    # no years...

    return duration_str
