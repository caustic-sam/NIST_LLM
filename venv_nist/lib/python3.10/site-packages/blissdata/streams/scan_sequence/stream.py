# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import numpy as np
from blissdata.streams.base import BaseStream, BaseView, EventRange, StreamDefinition
from blissdata.redis_engine.encoding.json import JsonStreamEncoder
from blissdata.redis_engine.exceptions import IndexNoMoreThereError


class ScanView(BaseView):
    def __init__(self, data_store, events):
        self._data_store = data_store
        self._events = events

    @property
    def index(self):
        return self._events.index

    def __len__(self):
        return len(self._events)

    def get_data(self, start=None, stop=None):
        trimmed_range = range(len(self))[start:stop]
        offset = self._events.nb_expired
        data_start = trimmed_range.start - offset
        data_stop = trimmed_range.stop - offset
        if data_start < 0:
            raise IndexNoMoreThereError
        else:
            events = self._events.data[data_start:data_stop]
            return [self._data_store.load_scan(event["key"]) for event in events]


class ScanStream(BaseStream):
    """A stream containing reference to scans"""

    def __init__(self, event_stream):
        super().__init__(event_stream)

    @staticmethod
    def make_definition(name, info={}) -> StreamDefinition:
        info = info.copy()
        info["format"] = "subscan"
        info["plugin"] = "scan_sequence"
        return StreamDefinition(name, info, JsonStreamEncoder())

    @property
    def kind(self):
        return "scan"

    @property
    def plugin(self):
        return "scan_sequence"

    @property
    def dtype(self):
        return np.dtype("object")

    @property
    def shape(self):
        return ()

    def __len__(self):
        return len(self._event_stream)

    def __getitem__(self, key):
        data = self._event_stream[key]
        if isinstance(key, slice):
            return [
                self._event_stream._data_store.load_scan(event["key"]) for event in data
            ]
        else:
            return self._event_stream._data_store.load_scan(data["key"])

    def _need_last_only(self, last_only):
        return last_only

    def _build_view_from_events(self, index: int, events: EventRange, last_only: bool):
        return ScanView(self._event_stream._data_store, events)
