# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import numpy as np

from blissdata.h5api import dynamic_hdf5
from blissdata.redis_engine.exceptions import IndexNoMoreThereError
from blissdata.streams.base import Stream, StreamDefinition, BaseView, EventRange


class Hdf5BackedView(BaseView):
    def __init__(self, events: EventRange, _read_file):
        self._events = events
        self._read_file = _read_file

    @property
    def index(self):
        return self._events.index

    def __len__(self):
        return len(self._events)

    def get_data(self, start=None, stop=None):
        trimmed_range = range(len(self))[start:stop]
        file_start, data_start = sorted((trimmed_range.start, self._events.nb_expired))
        file_stop, data_stop = sorted((trimmed_range.stop, self._events.nb_expired))

        if file_start != file_stop:
            offset = self._events.index
            file_data = self._read_file(slice(file_start + offset, file_stop + offset))
            assert len(file_data) == file_stop - file_start
        else:
            file_data = []

        if data_start != data_stop:
            offset = -self._events.nb_expired
            stream_data = self._events.data[data_start + offset : data_stop + offset]
        else:
            return file_data

        if not len(file_data):
            return stream_data
        else:
            return np.concatenate((file_data, stream_data))


class Hdf5BackedStream(Stream):
    def __init__(self, event_stream, file_path=None):
        super().__init__(event_stream)
        if file_path is not None:
            # support legacy fallback streams (file_path was stored at scan level)
            self._file_path = file_path
        else:
            self._file_path = self.info["file_path"]
        self._data_path = self.info["data_path"]

    @staticmethod
    def make_definition(
        name, file_path, data_path, dtype, shape=None, info={}
    ) -> StreamDefinition:
        info = info.copy()
        info["file_path"] = file_path
        info["data_path"] = data_path
        info["save"] = True
        info["plugin"] = "hdf5_fallback"
        return Stream.make_definition(name, dtype, shape, info)

    @property
    def plugin(self):
        return "hdf5_fallback"

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except IndexNoMoreThereError:
            return self._get_from_file(key)

    def _get_from_file(self, key):
        # TODO do not reopen File for every __getitem__
        # TODO cache expired indexes for faster fallback
        with dynamic_hdf5.File(
            self._file_path, retry_timeout=0, retry_period=0
        ) as nxroot:
            return nxroot[self._data_path][key]

    def _build_view_from_events(self, index: int, events: EventRange, last_only: bool):
        assert len(events)
        return Hdf5BackedView(events, self._get_from_file)
