# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import contextlib
import gevent.event

from bliss.common import event


class EventListener:
    """Listener for `bliss.common.event` to simplify unit testing

    .. code-block::

        listener = EventListener()
        with listener.listening(obj, "state"):
            ...

    .. code-block::

        listener = EventListener()
        try:
            event.connect(obj, "state", listener)
            ...
        finally:
            event.disconnect(obj, "state", listener)
    """

    def __init__(self):
        self.events = []
        self.event_received = {}

    def __call__(self, value, signal, sender):
        self.events.append((value, signal, sender))
        ev = self.event_received.get((sender, signal))
        if ev is not None:
            ev.set()

    @contextlib.contextmanager
    def listening(self, obj: object, event_name: str, wait_event: bool = False):
        """Context manager to listen to an event, and eventually wait for it

        obj: event emitter
        event_name: signal name
        wait_event (defaults to False): wait for the event
        """
        try:
            ev = gevent.event.Event()
            self.event_received[(obj, event_name)] = ev
            event.connect(obj, event_name, self)
            yield ev
            if wait_event:
                ev.wait()
        finally:
            del self.event_received[(obj, event_name)]
            event.disconnect(obj, event_name, self)

    @property
    def last_value(self):
        return self.events[-1][0]

    @property
    def last_signal(self):
        return self.events[-1][1]

    @property
    def last_sender(self):
        return self.events[-1][2]

    @property
    def event_count(self):
        return len(self.events)
