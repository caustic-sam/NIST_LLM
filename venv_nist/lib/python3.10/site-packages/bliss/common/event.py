# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import sys
from louie import dispatcher
from louie.dispatcher import get_receivers  # noqa: F401
from louie import robustapply  # noqa: F401
from louie import saferef  # noqa: F401
from louie import Any
from bliss.common.proxy import ProxyWithoutCall


def _get_sender(sender):
    if isinstance(sender, ProxyWithoutCall):
        sender = sender.__wrapped__
    return sender


def send(sender, signal, *args, **kwargs):
    sender = _get_sender(sender)
    dispatcher.send(signal, sender, *args, **kwargs)


def send_safe(*args, **kwargs):
    """Emit the signal, but catch exceptions to not interrupt
    further processing

    The traceback goes through the except hook, so it can be printed
    """
    try:
        return send(*args, **kwargs)
    except Exception:
        sys.excepthook(*sys.exc_info())


def connect(sender, signal, callback):
    sender = _get_sender(sender)
    if signal is Any:
        dispatcher.connect(callback, sender=sender)
    else:
        dispatcher.connect(callback, signal, sender)


def disconnect(sender, signal, callback):
    sender = _get_sender(sender)
    try:
        if signal is Any:
            dispatcher.disconnect(callback, sender=sender)
        else:
            dispatcher.disconnect(callback, signal, sender)
    except Exception:
        pass
